"""Tests for per-person segment identity, step construction and the segment store."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.cast_studio import segments as segments_module
from custom_components.cast_studio.people import (
    Person,
    Routine,
    get_first_person,
    get_people,
)
from custom_components.cast_studio.personas import get_persona
from custom_components.cast_studio.segments import (
    ALL_HEARD_KEY,
    ALL_HEARD_TEXT,
    SegmentStore,
    StoredSegment,
    _all_heard_step,
    async_build_segments,
    build_segment_step,
    routine_segment_keys,
    segment_key,
    segment_type_of,
)
from custom_components.cast_studio.tts_client import _mark_voice_fallback

GEN = "custom_components.cast_studio.generator"
SEG = "custom_components.cast_studio.segments"

SAM_SETTINGS = {
    "weather_entity": "weather.home",
    "calendar_entity": "calendar.sam",
    "todo_entity": "todo.sam",
    "scripture_entity": "sensor.daily_text",
    "commute_drive_sensor": "sensor.sam_commute_drive",
    "commute_transit_sensor": "sensor.sam_commute_transit",
    "host_voice": "VoiceA",
    "scripture_voice": "VoiceB",
    "host_personality": "calm_host",
    "scripture_personality": "warm_reader",
    "greeting_title": "Morning, Sam",
    "greeting_subtitle": "Rise and shine",
    "greeting_image": "sam_avatar.png",
}


def make_person(**overrides: Any) -> Person:
    """Build a fake person, Sam."""
    values: dict[str, Any] = {
        "person_id": "person_sam",
        "name": "Sam",
        "phonetic_name": "Sam",
        "media_player": "media_player.kitchen_speaker",
        "commute_destination_zone": "zone.office",
        "transportation_mode": "driving",
        "reset_time": time(3, 0),
        "max_age_minutes": 0,
        "settings": dict(SAM_SETTINGS),
    }
    values.update(overrides)
    return Person(**values)


def make_routine(segments: tuple[str, ...]) -> Routine:
    return Routine(
        routine_id="r_sam_full",
        person_id="person_sam",
        name="Full",
        segments=segments,
        dashboard_path=None,
        dashboard_view=None,
        dashboard_display=None,
    )


def at(hour: int, minute: int = 0, day: int = 20) -> datetime:
    """Return a local, timezone-aware time on a fake October 2026 day."""
    return datetime(2026, 10, day, hour, minute, tzinfo=dt_util.get_default_time_zone())


# --- identity -----------------------------------------------------------------


def test_segment_key_only_suffixes_encouragement_without_signoff() -> None:
    assert segment_key("weather") == "weather"
    assert segment_key("weather", include_signoff=False) == "weather"
    assert segment_key("daily_encouragement") == "daily_encouragement"
    assert (
        segment_key("daily_encouragement", include_signoff=False)
        == "daily_encouragement_nosignoff"
    )


def test_segment_type_of_maps_keys_back_to_types() -> None:
    assert segment_type_of("daily_encouragement_nosignoff") == "daily_encouragement"
    assert segment_type_of("daily_encouragement") == "daily_encouragement"
    assert segment_type_of("weather") == "weather"
    assert segment_type_of("closing") == "closing"


def test_routine_segment_keys_drops_signoff_when_closing_present() -> None:
    with_closing = make_routine(("scripture", "daily_encouragement", "closing"))
    assert routine_segment_keys(with_closing) == [
        "scripture",
        "daily_encouragement_nosignoff",
        "closing",
    ]
    without_closing = make_routine(("scripture", "daily_encouragement"))
    assert routine_segment_keys(without_closing) == [
        "scripture",
        "daily_encouragement",
    ]


# --- step construction ------------------------------------------------------


def test_build_segment_step_uses_person_settings_and_name_rule() -> None:
    person = make_person()
    steps = {
        key: build_segment_step(person, key, visual=False)
        for key in (
            "greeting",
            "weather",
            "national_day",
            "fun_fact",
            "agenda",
            "commute",
            "scripture",
            "daily_encouragement",
            "daily_encouragement_nosignoff",
            "closing",
        )
    }

    greeting = steps["greeting"]
    assert greeting["is_first_step"] is True
    assert greeting["title"] == "Morning, Sam"
    assert greeting["subtitle"] == "Rise and shine"
    assert greeting["image_path"] == "sam_avatar.png"

    weather = steps["weather"]
    assert weather["entity_id"] == person.settings["weather_entity"]
    assert "is_first_step" not in weather
    assert weather["voice"] == "VoiceA"
    assert weather["voice_personality"] == "calm_host"

    for key, step in steps.items():
        assert step["standalone_segment"] is True, key
        assert step["type"] == segment_type_of(key), key
        assert step["name"] == segment_type_of(key), key
        assert step["visual"] is False, key
        if key != "greeting":
            assert "is_first_step" not in step, key

    commute = steps["commute"]
    assert commute["transportation_mode"] == person.transportation_mode == "driving"
    assert commute["destination_zone"] == "zone.office"

    agenda = steps["agenda"]
    assert agenda["calendar_entities"] == ["calendar.sam"]
    assert agenda["todo_entity"] == "todo.sam"

    scripture = steps["scripture"]
    assert scripture["entity_id"] == "sensor.daily_text"
    assert scripture["voice"] == "VoiceB"
    assert scripture["voice_personality"] == "warm_reader"

    assert steps["daily_encouragement"]["include_signoff"] is True
    assert steps["daily_encouragement_nosignoff"]["include_signoff"] is False
    assert steps["daily_encouragement_nosignoff"]["type"] == "daily_encouragement"
    # The AI writes these from the date; they read no entity.
    assert "entity_id" not in steps["fun_fact"]
    assert "entity_id" not in steps["national_day"]


def test_build_segment_step_defaults_and_visual_flag() -> None:
    person = make_person(settings={})
    greeting = build_segment_step(person, "greeting")
    assert greeting["visual"] is True
    assert greeting["title"] == "Sam"
    assert greeting["subtitle"] == "Good Morning"
    assert "image_path" not in greeting
    assert build_segment_step(person, "weather")["entity_id"] == ""


def test_build_segment_step_visual_flag() -> None:
    person = make_person(settings={})
    assert build_segment_step(person, "weather")["visual"] is True
    assert build_segment_step(person, "weather", visual=False)["visual"] is False


def test_build_segment_step_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="bogus"):
        build_segment_step(make_person(), "bogus")


# --- store ------------------------------------------------------------------


def test_person_dir_stays_inside_output_dir(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    store = SegmentStore(hass, str(tmp_path))
    person_dir = store.person_dir(make_person())
    assert person_dir.resolve().is_relative_to(tmp_path.resolve())
    assert person_dir != tmp_path

    with pytest.raises(ValueError, match="outside"):
        store.person_dir(make_person(person_id="../../escape"))


def _fake_ffmpeg() -> AsyncMock:
    """FFmpeg stand-in that writes the command's output file."""

    async def _run(_hass: Any, cmd: list[str], **_kw: Any) -> bool:
        Path(cmd[-1]).write_bytes(b"media")
        return True

    return AsyncMock(side_effect=_run)


def _llm_content(*_args: Any, **_kw: Any) -> dict[str, Any]:
    return {
        "visual_card": {"title": "Card", "subtitle": "", "bullet_points": []},
        "narration_script": "Some narration here.",
    }


def _build_patches(**overrides: Any) -> dict[str, Any]:
    mocks: dict[str, Any] = {
        "llm": AsyncMock(side_effect=_llm_content),
        "tts": AsyncMock(side_effect=lambda _h, _s, _v, _p, path, **_k: path),
        "render": MagicMock(return_value=None),
        "image": AsyncMock(return_value=None),
        "map": AsyncMock(return_value=None),
        "ffmpeg": _fake_ffmpeg(),
    }
    mocks.update(overrides)
    return mocks


class _Patched:
    """Patch the generator boundaries (LLM, TTS, visuals, FFmpeg) for a build."""

    def __init__(self, mocks: dict[str, Any]) -> None:
        self.mocks = mocks
        self._patchers = [
            patch(f"{GEN}.async_generate_step_content", new=mocks["llm"]),
            patch(f"{GEN}.async_synthesize_speech", new=mocks["tts"]),
            patch(f"{GEN}.render_slide", new=mocks["render"]),
            patch(f"{GEN}.async_generate_ai_image", new=mocks["image"]),
            patch(f"{GEN}.async_fetch_commute_map", new=mocks["map"]),
            patch(
                f"{GEN}.async_resolve_google_maps_api_key",
                new=AsyncMock(return_value="fake-key"),
            ),
            patch(f"{GEN}.async_run_ffmpeg", new=mocks["ffmpeg"]),
            patch(f"{SEG}.async_run_ffmpeg", new=mocks["ffmpeg"]),
        ]

    def __enter__(self) -> dict[str, Any]:
        for p in self._patchers:
            p.start()
        return self.mocks

    def __exit__(self, *exc: object) -> None:
        for p in reversed(self._patchers):
            p.stop()


def _set_fake_states(hass: HomeAssistant) -> None:
    hass.states.async_set("weather.home", "sunny", {"temperature": 70})
    hass.states.async_set("sensor.daily_text", "ok")
    hass.states.async_set("calendar.sam", "off")
    hass.states.async_set("todo.sam", "0")
    hass.states.async_set("sensor.sam_commute_drive", "25", {"mode": "driving"})
    hass.states.async_set("sensor.sam_commute_transit", "40", {"mode": "transit"})
    hass.states.async_set(
        "zone.office", "0", {"latitude": 32.9, "longitude": -117.2, "radius": 100}
    )


def _manifest(store: SegmentStore, person: Person, now: datetime) -> dict[str, Any]:
    day_dir = store.person_dir(person) / "2026-10-20"
    assert now.date().isoformat() == "2026-10-20"
    return json.loads((day_dir / "manifest.json").read_text(encoding="utf-8"))


async def test_audio_only_person_builds_no_visuals(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _set_fake_states(hass)
    person = make_person()
    store = SegmentStore(hass, str(tmp_path))
    now = at(8)
    keys = ["greeting", "weather", "fun_fact", "national_day", "commute", "closing"]

    with _Patched(_build_patches()) as mocks:
        result = await async_build_segments(
            hass, store, person, keys, {"platform": "gemini", "api_key": "k"}, now=now
        )

    mocks["render"].assert_not_called()
    mocks["map"].assert_not_called()
    mocks["image"].assert_not_called()
    for key in keys:
        segment = result[key]
        assert isinstance(segment, StoredSegment), (key, segment)
        assert segment.clip_path is None
        assert re.fullmatch(
            rf"{key}\.[0-9a-f]{{8}}\.mp3", Path(segment.audio_path).name
        )
        assert Path(segment.audio_path).is_file()
        assert segment.built_day == now.date()
        assert segment.built_at == now


def _fallback_tts(*, fell_back: bool) -> AsyncMock:
    """A TTS fake that reports whether the configured voice fell back."""

    async def _speak(_h: Any, _s: Any, _v: Any, _p: Any, path: str, **_k: Any) -> str:
        if fell_back:
            _mark_voice_fallback()
        return path

    return AsyncMock(side_effect=_speak)


async def test_fallback_voiced_segment_is_flagged_and_replaced_by_the_next_build(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _set_fake_states(hass)
    person = make_person()
    store = SegmentStore(hass, str(tmp_path))
    now = at(8)
    creds = {"platform": "gemini", "api_key": "k"}

    with _Patched(_build_patches(tts=_fallback_tts(fell_back=True))):
        result = await async_build_segments(
            hass, store, person, ["greeting"], creds, now=now
        )

    segment = result["greeting"]
    assert segment.degraded is True
    assert _manifest(store, person, now)["segments"]["greeting"]["degraded"] is True
    # It still plays: the flag does not make it stale.
    assert store.get_fresh(person, "greeting", now) == segment
    # The all-heard line was voiced by the fallback voice too.
    assert store.all_heard_clip(person).degraded is True

    with _Patched(_build_patches(tts=_fallback_tts(fell_back=False))):
        result = await async_build_segments(
            hass, store, person, ["greeting"], creds, now=now
        )

    assert result["greeting"].degraded is False
    assert store.all_heard_clip(person).degraded is False


async def test_manifest_without_degraded_reads_as_not_degraded(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _set_fake_states(hass)
    person = make_person()
    store = SegmentStore(hass, str(tmp_path))
    now = at(8)

    with _Patched(_build_patches()):
        await async_build_segments(
            hass,
            store,
            person,
            ["greeting"],
            {"platform": "gemini", "api_key": "k"},
            now=now,
        )
    manifest_path = store.person_dir(person) / "2026-10-20" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["segments"]["greeting"]["degraded"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    reloaded = SegmentStore(hass, str(tmp_path))
    await reloaded.async_load(person, now)

    assert reloaded.get_fresh(person, "greeting", now) is not None


async def test_build_segments_visual_only_for_visual_keys(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _set_fake_states(hass)
    person = make_person()
    store = SegmentStore(hass, str(tmp_path))

    with _Patched(_build_patches()):
        result = await async_build_segments(
            hass,
            store,
            person,
            ["greeting", "weather"],
            {"platform": "gemini", "api_key": "k"},
            now=at(8),
            visual_keys={"weather"},
            all_heard_visual=False,
        )

    assert result["weather"].clip_path is not None
    assert result["greeting"].clip_path is None
    assert store.all_heard_clip(person).clip_path is None


async def test_visual_person_builds_uniform_clips(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _set_fake_states(hass)
    person = make_person()
    store = SegmentStore(hass, str(tmp_path))

    with _Patched(_build_patches()) as mocks:
        result = await async_build_segments(
            hass,
            store,
            person,
            ["weather", "closing"],
            {"platform": "conversation_agent"},
            now=at(8),
            visual_keys={"weather", "closing"},
        )

    for key in ("weather", "closing"):
        segment = result[key]
        assert isinstance(segment, StoredSegment)
        assert segment.clip_path is not None
        assert re.fullmatch(rf"{key}\.[0-9a-f]{{8}}\.mp4", Path(segment.clip_path).name)
        assert Path(segment.clip_path).is_file()
    assert mocks["render"].call_count >= 2

    commands = [c.args[1] for c in mocks["ffmpeg"].call_args_list]
    audio_cmds = [c for c in commands if c[-1].endswith(".mp3")]
    clip_cmds = [c for c in commands if c[-1].endswith(".mp4")]
    assert audio_cmds
    assert clip_cmds
    # Every segment uses one fixed encode so stitching can stream-copy.
    for cmd in audio_cmds:
        joined = " ".join(cmd)
        assert "-c:a libmp3lame" in joined
        assert "-b:a 192k" in joined
        assert "-ar 44100" in joined
        assert "-ac 1" in joined
    clip_params = {
        tuple(a for a in cmd if not a.startswith(("/", "apad")) and not a[0].isdigit())
        for cmd in clip_cmds
    }
    assert len(clip_params) == 1
    joined = " ".join(clip_cmds[0])
    for fragment in (
        "-c:v libx264",
        "-pix_fmt yuv420p",
        "-c:a aac",
        "-b:a 192k",
        "-ar 44100",
        "-ac 1",
        "-r 25",
    ):
        assert fragment in joined


async def test_build_segments_one_failure_does_not_stop_others(
    hass: HomeAssistant, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _set_fake_states(hass)
    person = make_person()
    store = SegmentStore(hass, str(tmp_path))
    now = at(8)

    def _llm(_hass: Any, step: dict[str, Any], *_a: Any, **_k: Any) -> Any:
        if step["type"] == "scripture":
            msg = "scripture sensor exploded"
            raise RuntimeError(msg)
        return _llm_content()

    with _Patched(_build_patches(llm=AsyncMock(side_effect=_llm))):
        result = await async_build_segments(
            hass,
            store,
            person,
            ["scripture", "weather"],
            {"platform": "conversation_agent"},
            now=now,
        )

    assert isinstance(result["scripture"], Exception)
    assert isinstance(result["weather"], StoredSegment)
    manifest = _manifest(store, person, now)
    assert "weather" in manifest["segments"]
    assert "scripture" not in manifest["segments"]
    assert "scripture" in caplog.text
    assert store.get_fresh(person, "weather", now) is not None
    assert store.get_fresh(person, "scripture", now) is None


async def test_build_segments_unconfigured_entity_is_a_failure(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    person = make_person(settings={})
    store = SegmentStore(hass, str(tmp_path))
    with _Patched(_build_patches()) as mocks:
        result = await async_build_segments(
            hass, store, person, ["weather"], {"platform": "mock"}, now=at(8)
        )
    assert isinstance(result["weather"], Exception)
    mocks["llm"].assert_not_called()


async def test_rebuild_replaces_same_day_entry_and_keeps_others(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _set_fake_states(hass)
    person = make_person()
    store = SegmentStore(hass, str(tmp_path))
    with _Patched(_build_patches()):
        await async_build_segments(
            hass, store, person, ["greeting", "weather"], {}, now=at(8)
        )
        await async_build_segments(hass, store, person, ["weather"], {}, now=at(9))

    manifest = _manifest(store, person, at(9))
    assert set(manifest["segments"]) == {"greeting", "weather"}
    assert manifest["segments"]["weather"]["built_at"] == at(9).isoformat()
    assert store.get_fresh(person, "weather", at(9)).built_at == at(9)

    # A fresh store (e.g. after a restart) reads the manifest from disk.
    reloaded = SegmentStore(hass, str(tmp_path))
    await reloaded.async_load(person, at(9, 30))
    assert reloaded.get_fresh(person, "greeting", at(9, 30)).built_at == at(8)


async def test_all_heard_clip_built_once_per_day_with_fixed_text(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    person = make_person()
    store = SegmentStore(hass, str(tmp_path))
    assert store.all_heard_clip(person) is None

    with _Patched(_build_patches()) as mocks:
        await async_build_segments(hass, store, person, [], {}, now=at(8))
        await async_build_segments(hass, store, person, [], {}, now=at(9))

    mocks["llm"].assert_not_called()
    spoken = [c.args[1] for c in mocks["tts"].call_args_list]
    assert spoken == [ALL_HEARD_TEXT]
    assert ALL_HEARD_TEXT == "That's everything for this morning."
    clip = store.all_heard_clip(person)
    assert clip is not None
    assert clip.key == ALL_HEARD_KEY
    assert Path(clip.audio_path).is_file()

    with _Patched(_build_patches()) as mocks:
        await async_build_segments(hass, store, person, [], {}, now=at(8, day=21))
    assert mocks["tts"].call_count == 1  # rebuilt on the next briefing day


async def test_get_fresh_ignores_previous_day_and_prune_removes_it(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _set_fake_states(hass)
    person = make_person()
    store = SegmentStore(hass, str(tmp_path))
    yesterday = at(8, day=19)
    with _Patched(_build_patches()):
        await async_build_segments(hass, store, person, ["weather"], {}, now=yesterday)

    old_dir = store.person_dir(person) / "2026-10-19"
    assert old_dir.is_dir()
    assert store.get_fresh(person, "weather", yesterday) is not None
    # 02:59 on the 20th still belongs to the 19th's briefing day.
    assert store.get_fresh(person, "weather", at(2, 59)) is not None
    assert store.get_fresh(person, "weather", at(3, 0)) is None
    assert store.get_fresh(person, "weather", at(8)) is None

    await store.async_prune(person, at(8))
    assert not old_dir.exists()
    assert store.all_heard_clip(person) is not None  # not day-scoped, kept


async def test_rebuild_writes_new_files_and_leaves_superseded_ones(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _set_fake_states(hass)
    person = make_person()
    store = SegmentStore(hass, str(tmp_path))
    with _Patched(_build_patches()):
        await async_build_segments(
            hass, store, person, ["weather"], {}, now=at(8), visual_keys={"weather"}
        )
        first = store.get_fresh(person, "weather", at(8))
        await async_build_segments(
            hass, store, person, ["weather"], {}, now=at(9), visual_keys={"weather"}
        )
    second = store.get_fresh(person, "weather", at(9))

    # A play may still be streaming the first build: it is never overwritten.
    assert second.audio_path != first.audio_path
    assert second.clip_path != first.clip_path
    for path in (first.audio_path, first.clip_path):
        assert Path(path).is_file()
    manifest = _manifest(store, person, at(9))["segments"]["weather"]
    assert manifest["audio"] == Path(second.audio_path).name
    assert manifest["clip"] == Path(second.clip_path).name


async def test_prune_removes_superseded_all_heard_files_once_out_of_use(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    person = make_person()
    store = SegmentStore(hass, str(tmp_path))
    with _Patched(_build_patches()):
        await async_build_segments(hass, store, person, [], {}, now=at(8))
        old = store.all_heard_clip(person)
        await async_build_segments(hass, store, person, [], {}, now=at(8, day=21))
    new = store.all_heard_clip(person)
    assert Path(old.audio_path).is_file()

    # Rebuilt only minutes ago: the old clip may still be playing.
    await store.async_prune(person, at(8, 30, day=21))
    assert Path(old.audio_path).is_file()

    await store.async_prune(person, at(9, 30, day=21))
    assert not Path(old.audio_path).exists()
    assert Path(new.audio_path).is_file()
    assert (Path(new.audio_path).parent / "manifest.json").is_file()


async def test_get_fresh_honours_max_age(hass: HomeAssistant, tmp_path: Path) -> None:
    _set_fake_states(hass)
    person = make_person(max_age_minutes=30)
    store = SegmentStore(hass, str(tmp_path))
    with _Patched(_build_patches()):
        await async_build_segments(hass, store, person, ["weather"], {}, now=at(8))
    assert store.get_fresh(person, "weather", at(8) + timedelta(minutes=30))
    assert store.get_fresh(person, "weather", at(8) + timedelta(minutes=31)) is None


async def test_prune_keeps_today(hass: HomeAssistant, tmp_path: Path) -> None:
    _set_fake_states(hass)
    person = make_person()
    store = SegmentStore(hass, str(tmp_path))
    with _Patched(_build_patches()):
        await async_build_segments(hass, store, person, ["weather"], {}, now=at(8))
    await store.async_prune(person, at(9))
    assert store.get_fresh(person, "weather", at(9)) is not None
    assert (store.person_dir(person) / "2026-10-20" / "manifest.json").is_file()


async def test_person_conversation_agent_overrides_creds(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    person = make_person(settings={"conversation_agent": "conversation.fake_agent"})
    store = SegmentStore(hass, str(tmp_path))
    creds = {"platform": "conversation_agent"}
    with _Patched(_build_patches()) as mocks:
        await async_build_segments(hass, store, person, ["greeting"], creds, now=at(8))
    used_creds = mocks["llm"].call_args.args[3]
    assert used_creds["agent_id"] == "conversation.fake_agent"
    assert "agent_id" not in creds


def test_build_segment_step_commute_uses_person_sensors() -> None:
    driving = build_segment_step(make_person(), "commute")
    assert driving["commute_drive_sensor"] == "sensor.sam_commute_drive"
    assert driving["commute_transit_sensor"] == "sensor.sam_commute_transit"
    assert driving["commute_sensors"] == ["sensor.sam_commute_drive"]
    transit = build_segment_step(make_person(transportation_mode="transit"), "commute")
    assert transit["commute_sensors"] == ["sensor.sam_commute_transit"]
    fastest = build_segment_step(make_person(transportation_mode="fastest"), "commute")
    assert fastest["commute_sensors"] == [
        "sensor.sam_commute_drive",
        "sensor.sam_commute_transit",
    ]


async def test_commute_skipped_without_person_zone_never_uses_other_persons(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Sam has no zone; the first person Alex's zone.office is never used."""
    from tests.conftest import ALEX_ID, SAM_ID, make_entry, person_subentry

    _set_fake_states(hass)
    entry = make_entry(
        [
            person_subentry(ALEX_ID, "Alex", commute_destination_zone="zone.office"),
            person_subentry(
                SAM_ID,
                "Sam",
                commute_drive_sensor="sensor.sam_commute_drive",
            ),
        ]
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    assert get_first_person(hass).commute_destination_zone == "zone.office"
    sam = get_people(entry)[1]
    assert sam.commute_destination_zone is None
    assert "zone.office" not in build_segment_step(sam, "commute").values()

    store = SegmentStore(hass, str(tmp_path))
    with _Patched(_build_patches()) as mocks:
        result = await async_build_segments(
            hass, store, sam, ["commute", "greeting"], {}, now=at(8)
        )
    assert isinstance(result["commute"], HomeAssistantError)
    assert str(result["commute"]) == "No work zone configured for Sam"
    assert isinstance(result["greeting"], StoredSegment)
    mocks["map"].assert_not_called()
    assert all(c.args[1]["type"] != "commute" for c in mocks["llm"].call_args_list)


async def test_commute_skipped_without_person_sensor(
    hass: HomeAssistant, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _set_fake_states(hass)
    person = make_person(settings={})
    store = SegmentStore(hass, str(tmp_path))
    with _Patched(_build_patches()) as mocks:
        result = await async_build_segments(
            hass, store, person, ["commute", "greeting"], {}, now=at(8)
        )
    assert isinstance(result["commute"], HomeAssistantError)
    assert str(result["commute"]) == "No commute sensor configured for Sam"
    assert isinstance(result["greeting"], StoredSegment)
    mocks["map"].assert_not_called()
    assert "No commute sensor configured for Sam" in caplog.text


async def test_commute_builds_from_person_zone_and_sensor(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _set_fake_states(hass)
    person = make_person()
    store = SegmentStore(hass, str(tmp_path))
    with _Patched(_build_patches()) as mocks:
        result = await async_build_segments(
            hass, store, person, ["commute"], {}, now=at(8), visual_keys={"commute"}
        )
    assert isinstance(result["commute"], StoredSegment)
    mocks["map"].assert_called_once()
    context = mocks["llm"].call_args.args[1]["context"]
    assert "25 minutes" in context


async def test_build_segments_logs_each_build_time_with_source(
    hass: HomeAssistant, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _set_fake_states(hass)
    store = SegmentStore(hass, str(tmp_path))
    with caplog.at_level(logging.INFO), _Patched(_build_patches()):
        await async_build_segments(
            hass, store, make_person(), ["weather"], {}, now=at(8), source="refresh"
        )
    pattern = re.compile(r"Built segment 'weather' for Sam in \d+\.\ds \(refresh\)")
    assert any(
        pattern.search(r.getMessage())
        for r in caplog.records
        if r.levelno == logging.INFO
    )


async def test_build_segments_failure_log_includes_elapsed_and_source(
    hass: HomeAssistant, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _set_fake_states(hass)
    store = SegmentStore(hass, str(tmp_path))

    def _llm(_hass: Any, step: dict[str, Any], *_a: Any, **_k: Any) -> Any:
        if step["type"] == "scripture":
            msg = "scripture sensor exploded"
            raise RuntimeError(msg)
        return _llm_content()

    with _Patched(_build_patches(llm=AsyncMock(side_effect=_llm))):
        await async_build_segments(
            hass, store, make_person(), ["scripture"], {}, now=at(8)
        )
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "scripture" in m and "after " in m and "(prebuild)" in m for m in warnings
    )


async def test_build_segments_passes_sensor_refresh_timeout_to_commute_step(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _set_fake_states(hass)
    store = SegmentStore(hass, str(tmp_path))

    def _steps(mocks: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {c.args[1]["type"]: c.args[1] for c in mocks["llm"].call_args_list}

    with _Patched(_build_patches()) as mocks:
        await async_build_segments(
            hass,
            store,
            make_person(),
            ["commute", "weather"],
            {},
            now=at(8),
            sensor_refresh_timeout=5,
        )
        steps = _steps(mocks)
    assert steps["commute"]["sensor_refresh_timeout"] == 5
    assert "sensor_refresh_timeout" not in steps["weather"]

    with _Patched(_build_patches()) as mocks:
        await async_build_segments(
            hass,
            SegmentStore(hass, str(tmp_path / "b")),
            make_person(),
            ["commute"],
            {},
            now=at(8),
        )
        steps = _steps(mocks)
    assert "sensor_refresh_timeout" not in steps["commute"]


async def test_build_segments_can_skip_the_all_heard_clip(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _set_fake_states(hass)
    person = make_person()
    store = SegmentStore(hass, str(tmp_path))
    with _Patched(_build_patches()):
        await async_build_segments(
            hass, store, person, ["weather"], {}, now=at(8), build_all_heard=False
        )
    assert store.get_fresh(person, "weather", at(8)) is not None
    assert store.all_heard_clip(person) is None


async def test_failure_log_includes_traceback(
    hass: HomeAssistant, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def _boom(*_a: Any, **_k: Any) -> Any:
        msg = "unexpected boom"
        raise KeyError(msg)

    store = SegmentStore(hass, str(tmp_path))
    with _Patched(_build_patches(llm=AsyncMock(side_effect=_boom))):
        await async_build_segments(
            hass, store, make_person(), ["greeting"], {}, now=at(8)
        )
    records = [r for r in caplog.records if "greeting" in r.getMessage()]
    assert records
    assert records[0].exc_info is not None


async def test_concurrent_builds_for_one_person_keep_every_entry(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Manifest writes for one person are serialised: nothing is lost."""
    _set_fake_states(hass)
    person = make_person()
    store = SegmentStore(hass, str(tmp_path))
    real_commit = segments_module._commit_segment
    active = 0
    peak = 0
    guard = threading.Lock()

    def _slow_commit(*args: Any) -> Any:
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        try:
            threading.Event().wait(0.05)
            return real_commit(*args)
        finally:
            with guard:
                active -= 1

    with (
        _Patched(_build_patches()),
        patch(f"{SEG}._commit_segment", new=_slow_commit),
    ):
        results = await asyncio.gather(
            async_build_segments(
                hass, store, person, ["greeting", "weather"], {}, now=at(8)
            ),
            async_build_segments(
                hass, store, person, ["scripture", "fun_fact"], {}, now=at(8)
            ),
        )

    assert peak == 1
    for result in results:
        assert all(isinstance(v, StoredSegment) for v in result.values()), result
    manifest = _manifest(store, person, at(8))
    assert set(manifest["segments"]) == {"greeting", "weather", "scripture", "fun_fact"}
    for key in ("greeting", "weather", "scripture", "fun_fact"):
        assert store.get_fresh(person, key, at(8)) is not None


def test_commit_segment_moves_files_across_filesystems(tmp_path: Path) -> None:
    """Scratch (/tmp) and the output dir (/config) can be separate filesystems."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    source = scratch / "greeting.mp3"
    source.write_bytes(b"audio")
    target_dir = tmp_path / "out"
    target = str(target_dir / "greeting.mp3")
    segment = StoredSegment(
        key="greeting",
        audio_path=target,
        clip_path=None,
        duration=1.0,
        built_at=dt_util.now(),
        built_day=dt_util.now().date(),
    )
    real_replace = Path.replace

    def _no_cross_device(self: Path, dest: Any) -> Path:
        if Path(dest).parent != self.parent:
            raise OSError(18, "Invalid cross-device link")
        return real_replace(self, dest)

    with patch.object(Path, "replace", _no_cross_device):
        segments = segments_module._commit_segment(
            target_dir, segment, {str(source): target}
        )

    stored = Path(segments["greeting"].audio_path)
    assert stored.parent == target_dir
    assert stored.read_bytes() == b"audio"
    assert not source.exists()


def test_build_segment_step_commute_passes_return_sensors() -> None:
    person = make_person(
        settings={
            **SAM_SETTINGS,
            "commute_drive_return_sensor": "sensor.sam_drive_home",
            "commute_transit_return_sensor": "sensor.sam_metro_home",
        }
    )
    step = build_segment_step(person, "commute")
    assert step["commute_drive_return_sensor"] == "sensor.sam_drive_home"
    assert step["commute_transit_return_sensor"] == "sensor.sam_metro_home"


def test_agenda_step_uses_every_person_calendar() -> None:
    """A person with several calendars gets all of them in the agenda step."""
    person = make_person(
        settings={
            "calendar_entities": ["calendar.sam", "calendar.family"],
            "todo_entity": "todo.sam",
        },
    )
    step = build_segment_step(person, "agenda")
    assert step["calendar_entities"] == ["calendar.sam", "calendar.family"]
    assert step["entity_ids"] == ["calendar.sam", "calendar.family", "todo.sam"]


# --- personas -----------------------------------------------------------------

PERSONA_DAY = date(2026, 10, 20)


def _with_settings(**settings: Any) -> Person:
    return make_person(settings=dict(settings))


def test_no_personas_keeps_todays_step() -> None:
    """With no persona picked, the default voices and styles are unchanged."""
    person = _with_settings()
    greeting = build_segment_step(person, "greeting", PERSONA_DAY)
    scripture = build_segment_step(person, "scripture", PERSONA_DAY)
    assert (greeting["voice"], greeting["voice_personality"]) == (
        "Pulcherrima",
        "news_anchor",
    )
    assert (scripture["voice"], scripture["voice_personality"]) == (
        "Enceladus",
        "life_coach",
    )
    assert "voice_profile" not in greeting
    assert "openai_voice" not in scripture


def test_host_persona_applies_to_host_segments() -> None:
    """A host persona speaks the host segments and leaves the reading alone."""
    person = _with_settings(host_personas=["arthur"])
    arthur = get_persona("arthur")
    for key in ("greeting", "weather", "closing"):
        step = build_segment_step(person, key, PERSONA_DAY)
        assert step["voice"] == "Iapetus"
        assert step["voice_personality"] == arthur.character
        assert step["voice_profile"] == arthur.delivery
        assert step["openai_voice"] == "fable"
    scripture = build_segment_step(person, "scripture", PERSONA_DAY)
    assert scripture["voice"] == "Enceladus"
    assert "voice_profile" not in scripture


def test_reader_persona_applies_to_reading_segments() -> None:
    """A reader persona reads the daily text and the encouragement."""
    person = _with_settings(reader_personas=["malenfant"])
    for key in ("scripture", "daily_encouragement"):
        assert build_segment_step(person, key, PERSONA_DAY)["voice"] == "Charon"
    assert build_segment_step(person, "greeting", PERSONA_DAY)["voice"] == (
        "Pulcherrima"
    )


def test_persona_overrides_advanced_fields() -> None:
    """A picked persona wins over a typed host voice and personality."""
    person = _with_settings(
        host_voice="Zephyr", host_personality="cheerful", host_personas=["theo"]
    )
    step = build_segment_step(person, "greeting", PERSONA_DAY)
    assert step["voice"] == "Puck"
    assert step["voice_personality"] == get_persona("theo").character


def test_unknown_persona_falls_back_to_today() -> None:
    """A persona no longer in the catalog leaves the typed settings in charge."""
    person = _with_settings(host_personas=["gone_persona"], host_voice="Zephyr")
    step = build_segment_step(person, "greeting", PERSONA_DAY)
    assert step["voice"] == "Zephyr"
    assert "voice_profile" not in step


def test_random_pick_is_stable_for_the_day() -> None:
    """Every host segment on a day gets the same randomly picked persona."""
    person = _with_settings(host_personas=["arthur", "theo", "ray"])
    voices = {
        build_segment_step(person, key, PERSONA_DAY)["voice"]
        for key in ("greeting", "weather", "agenda", "closing")
    }
    assert len(voices) == 1


def test_all_heard_uses_host_persona() -> None:
    """The that's-everything clip is spoken by the host persona."""
    person = _with_settings(host_personas=["arthur"])
    assert _all_heard_step(person, PERSONA_DAY)["voice"] == "Iapetus"
