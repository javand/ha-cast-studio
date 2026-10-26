"""Tests for the briefing engine: prebuild, play, no-repeat and the dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.cast_studio import briefing
from custom_components.cast_studio.const import CONF_OUTPUT_DIR, DOMAIN
from custom_components.cast_studio.people import (
    Routine,
    briefing_day,
    find_routine,
    get_people,
)
from custom_components.cast_studio.segments import (
    ALL_HEARD_DIR,
    ALL_HEARD_KEY,
    SegmentStore,
    StoredSegment,
)

from .conftest import ALEX_ID, SAM_ID, person_subentry, routine_subentry

if TYPE_CHECKING:
    from collections.abc import Iterable

    from freezegun.api import FrozenDateTimeFactory

    from custom_components.cast_studio.people import Person

BRIEFING = "custom_components.cast_studio.briefing"
SPEAKER = "media_player.kitchen_speaker"
DISPLAY = "media_player.office_display"
QUICK = ["greeting", "weather"]
FULL = ["greeting", "weather", "commute", "scripture", "closing"]


def at(hour: int, minute: int = 0, day: int = 20) -> datetime:
    """Return a local, timezone-aware time on a fake October 2026 day."""
    return datetime(2026, 10, day, hour, minute, tzinfo=dt_util.get_default_time_zone())


def unversioned(name: str) -> str:
    """Drop the version tag stored files carry: ``weather.1a2b3c4d.mp3`` -> ``weather.mp3``."""
    return re.sub(r"\.[0-9a-f]{8}(?=\.[^.]+$)", "", name)


# --- fakes ------------------------------------------------------------------


@dataclass
class FakeBuilder:
    """Stands in for async_build_segments; writes tiny files into the store."""

    fail: set[str] = field(default_factory=set)
    gate: asyncio.Event | None = None
    started: asyncio.Event = field(default_factory=asyncio.Event)
    calls: list[list[str]] = field(default_factory=list)
    visual_calls: list[set[str]] = field(default_factory=list)

    async def __call__(  # noqa: PLR0913
        self,
        hass: HomeAssistant,
        store: SegmentStore,
        person: Person,
        keys: Iterable[str],
        ai_creds: dict[str, Any],
        *,
        now: datetime,
        visual_keys: Iterable[str] = (),
        all_heard_visual: bool = False,
    ) -> dict[str, StoredSegment | Exception]:
        wanted = list(dict.fromkeys(keys))
        visual = set(visual_keys)
        self.calls.append(wanted)
        self.visual_calls.append(visual)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        await store.async_load(person, now)
        today = briefing_day(person, now)
        jobs = [(key, store.day_dir(person, today)) for key in wanted]
        all_heard = store.all_heard_clip(person)
        if all_heard is None or all_heard.built_day != today:
            jobs.append((ALL_HEARD_KEY, store.person_dir(person) / ALL_HEARD_DIR))
        scratch = Path(tempfile.mkdtemp(prefix="fake_seg_"))
        results: dict[str, StoredSegment | Exception] = {}
        for key, target_dir in jobs:
            if key in self.fail:
                logging.getLogger(BRIEFING).warning("fake build failed: %s", key)
                results[key] = HomeAssistantError(f"{key} failed")
                continue
            staged: dict[str, str] = {}
            audio = scratch / f"{key}.mp3"
            audio.write_bytes(key.encode())
            staged[str(audio)] = str(target_dir / audio.name)
            clip_target = None
            if key in visual or (key == ALL_HEARD_KEY and all_heard_visual):
                clip = scratch / f"{key}.mp4"
                clip.write_bytes(key.encode())
                clip_target = str(target_dir / clip.name)
                staged[str(clip)] = clip_target
            segment = StoredSegment(
                key=key,
                audio_path=str(target_dir / audio.name),
                clip_path=clip_target,
                duration=0.0,
                built_at=now,
                built_day=today,
            )
            await store.async_store(person, target_dir, segment, staged)
            if key != ALL_HEARD_KEY:
                results[key] = segment
        return results

    @property
    def built_keys(self) -> list[str]:
        return [key for call in self.calls for key in call]


@dataclass
class FakeFFmpeg:
    """Records FFmpeg commands; a concat writes its output and logs its inputs."""

    concats: list[list[str]] = field(default_factory=list)

    async def __call__(self, hass: Any, command: list[str], **_: Any) -> bool:
        if "concat" in command:
            list_file = Path(command[command.index("-i") + 1])
            names = [
                Path(unversioned(line.split("'")[1])).stem
                for line in list_file.read_text(encoding="utf-8").splitlines()
                if line.startswith("file ")
            ]
            self.concats.append(names)
        Path(command[-1]).write_bytes(b"stitched")
        return True


@dataclass
class Harness:
    hass: HomeAssistant
    entry: MockConfigEntry
    builder: FakeBuilder
    ffmpeg: FakeFFmpeg
    events: list[tuple[str, dict[str, Any]]]

    def routine(self, name: str, person: str | None = None) -> Routine:
        return find_routine(self.entry, name, person)

    def person(self, person_id: str) -> Person:
        return next(p for p in get_people(self.entry) if p.person_id == person_id)

    @property
    def played(self) -> list[dict[str, Any]]:
        return [data for name, data in self.events if name == "play_media"]

    @property
    def turned_off(self) -> list[str]:
        return [data["entity_id"] for name, data in self.events if name == "turn_off"]

    @property
    def dashboards(self) -> list[dict[str, Any]]:
        return [data for name, data in self.events if name == "show_lovelace_view"]

    async def play(self, name: str, **kwargs: Any) -> None:
        await briefing.async_play(self.hass, self.entry, self.routine(name), **kwargs)
        await self.hass.async_block_till_done()

    async def prebuild(self, name: str) -> None:
        await briefing.async_prebuild(self.hass, self.entry, routine=self.routine(name))
        await self.hass.async_block_till_done()


def dashboard_routine(
    subentry_id: str, person_id: str, name: str, segments: list[str]
) -> dict[str, Any]:
    routine = routine_subentry(subentry_id, person_id, name, segments)
    routine["data"].update(dashboard_path="kitchen-dash", dashboard_view="morning")
    return routine


async def make_harness(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    subentries: list[dict[str, Any]],
) -> Harness:
    freezer.move_to(at(8))
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id=DOMAIN,
        title="CastStudio",
        data={CONF_OUTPUT_DIR: str(tmp_path)},
        subentries_data=subentries,
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    events: list[tuple[str, dict[str, Any]]] = []

    async def _record(call: ServiceCall) -> None:
        events.append((call.service, dict(call.data)))

    hass.services.async_register("media_player", "play_media", _record)
    hass.services.async_register("cast", "show_lovelace_view", _record)
    hass.services.async_register("media_player", "turn_off", _record)
    # Playing already: the orchestrator starts its lifecycle without waiting.
    hass.states.async_set(SPEAKER, "playing")
    hass.states.async_set(DISPLAY, "playing", {"app_name": "Default Media Receiver"})
    return Harness(hass, entry, FakeBuilder(), FakeFFmpeg(), events)


@pytest.fixture
def patched(request: pytest.FixtureRequest) -> Iterable[None]:
    """Patch the builder, FFmpeg and AI credentials for the harness."""
    harness_holder: dict[str, Harness] = {}
    request.node.harness_holder = harness_holder

    async def _builder(*args: Any, **kwargs: Any) -> Any:
        return await harness_holder["h"].builder(*args, **kwargs)

    async def _ffmpeg(*args: Any, **kwargs: Any) -> Any:
        return await harness_holder["h"].ffmpeg(*args, **kwargs)

    async def _creds(_hass: HomeAssistant) -> dict[str, Any]:
        return {"platform": "gemini", "api_key": "fake"}

    with (
        patch(f"{BRIEFING}.async_build_segments", side_effect=_builder),
        patch(f"{BRIEFING}.async_run_ffmpeg", side_effect=_ffmpeg),
        patch(f"{BRIEFING}.async_discover_ai_credentials", side_effect=_creds),
        patch(f"{BRIEFING}.RELEASE_WAIT_SECONDS", 0),
    ):
        yield


async def _harness(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    request: pytest.FixtureRequest,
    subentries: list[dict[str, Any]],
) -> Harness:
    harness = await make_harness(hass, tmp_path, freezer, subentries)
    request.node.harness_holder["h"] = harness
    return harness


def _sam(**data: Any) -> dict[str, Any]:
    return person_subentry(SAM_ID, "Sam", media_player=SPEAKER, **data)


@pytest.fixture
async def sam_quick(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    request: Any,
    patched: None,
) -> Harness:
    return await _harness(
        hass,
        tmp_path,
        freezer,
        request,
        [
            _sam(),
            routine_subentry(
                "r_sam_quick", SAM_ID, "Quick", QUICK, output_format="audio"
            ),
        ],
    )


@pytest.fixture
async def sam_full(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    request: Any,
    patched: None,
) -> Harness:
    return await _harness(
        hass,
        tmp_path,
        freezer,
        request,
        [
            _sam(),
            routine_subentry("r_sam_full", SAM_ID, "Full", FULL, output_format="audio"),
        ],
    )


@pytest.fixture
async def sam_quick_and_full(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    request: Any,
    patched: None,
) -> Harness:
    return await _harness(
        hass,
        tmp_path,
        freezer,
        request,
        [
            _sam(),
            routine_subentry(
                "r_sam_quick", SAM_ID, "Quick", QUICK, output_format="audio"
            ),
            routine_subentry("r_sam_full", SAM_ID, "Full", FULL, output_format="audio"),
        ],
    )


@pytest.fixture
async def prebuilt_sam_quick(sam_quick: Harness) -> Harness:
    await sam_quick.prebuild("Quick")
    sam_quick.builder.calls.clear()
    sam_quick.ffmpeg.concats.clear()
    return sam_quick


@pytest.fixture
async def sam_visual_with_dashboard(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    request: Any,
    patched: None,
) -> Harness:
    return await _harness(
        hass,
        tmp_path,
        freezer,
        request,
        [
            person_subentry(SAM_ID, "Sam", media_player=DISPLAY),
            dashboard_routine("r_sam_quick", SAM_ID, "Quick", QUICK),
        ],
    )


@pytest.fixture
async def sam_audio_with_dashboard(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    request: Any,
    patched: None,
) -> Harness:
    return await _harness(
        hass,
        tmp_path,
        freezer,
        request,
        [_sam(), dashboard_routine("r_sam_quick", SAM_ID, "Quick", QUICK)],
    )


@pytest.fixture
async def prebuilt_alex_office_day(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    request: Any,
    patched: None,
) -> Harness:
    harness = await _harness(
        hass,
        tmp_path,
        freezer,
        request,
        [
            person_subentry(
                ALEX_ID,
                "Alex",
                media_player=DISPLAY,
                commute_destination_zone="zone.office",
            ),
            routine_subentry("r_alex_quick", ALEX_ID, "Quick", QUICK),
        ],
    )
    await harness.prebuild("Quick")
    harness.builder.calls.clear()
    harness.ffmpeg.concats.clear()
    return harness


def _media_id(data: dict[str, Any]) -> str:
    return unversioned(str(data["media_content_id"]).split("?")[0])


# --- play -------------------------------------------------------------------


async def test_play_uses_fresh_prebuilt_segments_without_building(
    prebuilt_sam_quick: Harness,
) -> None:
    h = prebuilt_sam_quick
    await h.play("Quick")

    assert h.builder.calls == []
    assert len(h.played) == 1
    assert h.played[0]["entity_id"] == SPEAKER
    assert _media_id(h.played[0]).endswith(".mp3")
    assert h.ffmpeg.concats == [QUICK]
    state = h.hass.states.get("sensor.cast_studio_sam_quick")
    assert state is not None
    assert state.state == "ready"
    assert state.attributes["ready_segments"] == QUICK
    assert state.attributes["person"] == "Sam"
    assert state.attributes["routine"] == "Quick"
    assert state.attributes["briefing_day"] == "2026-10-20"


async def test_skip_played_counts_only_the_same_routine(
    sam_quick_and_full: Harness,
) -> None:
    h = sam_quick_and_full
    await h.play("Quick")
    await h.play("Full", skip_played=True)

    # Full has its own heard list: Quick's greeting and weather play again.
    assert h.ffmpeg.concats == [QUICK, FULL]
    assert len(h.played) == 2
    # Only the keys missing from the store were built live for Full.
    assert h.builder.calls == [QUICK, ["commute", "scripture", "closing"]]


async def test_play_all_heard_plays_all_heard_clip(sam_quick: Harness) -> None:
    h = sam_quick
    await h.play("Quick")
    await h.play("Quick", skip_played=True)

    assert len(h.played) == 2
    assert _media_id(h.played[1]).endswith(f"{ALL_HEARD_DIR}/{ALL_HEARD_KEY}.mp3")
    assert h.ffmpeg.concats == [QUICK]


async def test_play_skip_played_false_replays_everything_same_day(
    sam_quick: Harness,
) -> None:
    h = sam_quick
    await h.play("Quick")
    await h.play("Quick")

    assert h.ffmpeg.concats == [QUICK, QUICK]
    assert len(h.played) == 2
    assert h.builder.calls == [QUICK]


async def test_skip_played_tracks_types_not_signoff_variants(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    request: Any,
    patched: None,
) -> None:
    h = await _harness(
        hass,
        tmp_path,
        freezer,
        request,
        [
            _sam(),
            routine_subentry(
                "r_end",
                SAM_ID,
                "Ending",
                ["daily_encouragement", "closing"],
                output_format="audio",
            ),
            routine_subentry(
                "r_lift",
                SAM_ID,
                "Lift",
                ["daily_encouragement", "weather"],
                output_format="audio",
            ),
        ],
    )
    await h.play("Ending")

    # The encouragement type was heard (as the no-sign-off variant).
    assert h.ffmpeg.concats == [["daily_encouragement_nosignoff", "closing"]]
    history = await briefing.async_get_play_history(h.hass)
    sam = h.person(SAM_ID)
    assert history.played(sam, h.routine("Ending"), at(8)) == {
        "daily_encouragement",
        "closing",
    }
    assert history.played(sam, h.routine("Lift"), at(8)) == set()


async def test_play_after_reset_does_not_use_yesterdays_content(
    freezer: FrozenDateTimeFactory, prebuilt_sam_quick: Harness
) -> None:
    h = prebuilt_sam_quick
    freezer.move_to(at(3, 5, day=21))
    await h.play("Quick")

    assert h.builder.calls == [QUICK]
    store: SegmentStore = h.hass.data[DOMAIN][briefing.DATA_STORE]
    sam = h.person(SAM_ID)
    for key in QUICK:
        segment = store.get_fresh(sam, key, at(3, 5, day=21))
        assert segment is not None
        assert segment.built_day.isoformat() == "2026-10-21"
    assert len(h.played) == 1


async def test_play_waits_for_inflight_prebuild(sam_quick: Harness) -> None:
    h = sam_quick
    h.builder.gate = asyncio.Event()
    prebuild = asyncio.create_task(
        briefing.async_prebuild(h.hass, h.entry, routine=h.routine("Quick"))
    )
    await h.builder.started.wait()
    play = asyncio.create_task(briefing.async_play(h.hass, h.entry, h.routine("Quick")))
    for _ in range(5):
        await asyncio.sleep(0)
    assert h.played == []

    h.builder.gate.set()
    await asyncio.gather(prebuild, play)
    await h.hass.async_block_till_done()

    assert h.builder.calls == [QUICK]
    assert len(h.played) == 1
    assert h.ffmpeg.concats == [QUICK]


async def test_play_continues_when_one_segment_fails(
    sam_full: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    h = sam_full
    h.builder.fail = {"scripture"}
    with caplog.at_level(logging.WARNING):
        await h.play("Full")

    assert len(h.played) == 1
    assert h.ffmpeg.concats == [["greeting", "weather", "commute", "closing"]]
    assert any(
        "scripture" in record.getMessage() and record.name.endswith("cast_studio")
        for record in caplog.records
        if record.levelno == logging.WARNING
    )


async def test_play_marks_types_only_for_segments_that_played(
    sam_full: Harness,
) -> None:
    h = sam_full
    h.builder.fail = {"scripture"}
    await h.play("Full")
    h.builder.fail = set()
    await h.play("Full", skip_played=True)

    assert _media_id(h.played[1]).endswith("scripture.mp3")


async def test_play_missing_person_or_device_raises_and_sets_error(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    request: Any,
    patched: None,
) -> None:
    h = await _harness(
        hass,
        tmp_path,
        freezer,
        request,
        [
            person_subentry(SAM_ID, "Sam", media_player=None),
            routine_subentry(
                "r_sam_quick", SAM_ID, "Quick", QUICK, output_format="audio"
            ),
        ],
    )
    orphan_routine = Routine(
        routine_id="r_orphan",
        person_id="person_gone",
        name="Orphan",
        segments=("greeting",),
        dashboard_path=None,
        dashboard_view=None,
        dashboard_display=None,
    )
    with pytest.raises(HomeAssistantError, match="person"):
        await briefing.async_play(hass, h.entry, orphan_routine)

    # Sam has no output device.
    with pytest.raises(HomeAssistantError, match="media player"):
        await h.play("Quick")
    assert hass.states.get("sensor.cast_studio_sam_quick").state == "error"

    # An unavailable override target.
    hass.states.async_set("media_player.garage", "unavailable")
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await h.play("Quick", target="media_player.garage")
    assert hass.states.get("sensor.cast_studio_sam_quick").state == "error"
    assert h.played == []
    assert h.builder.calls == []


async def test_play_target_override_is_used(sam_quick: Harness) -> None:
    h = sam_quick
    h.hass.states.async_set("media_player.den", "playing")
    await h.play("Quick", target="media_player.den")
    assert h.played[0]["entity_id"] == "media_player.den"


# --- dashboard --------------------------------------------------------------


async def test_post_briefing_dashboard_runs_after_playback(
    sam_visual_with_dashboard: Harness,
) -> None:
    h = sam_visual_with_dashboard
    await h.play("Quick")

    assert [name for name, _ in h.events] == ["play_media", "show_lovelace_view"]
    assert h.dashboards == [
        {
            "entity_id": DISPLAY,
            "dashboard_path": "kitchen-dash",
            "view_path": "morning",
        }
    ]


async def test_dashboard_skipped_for_non_cast_target(
    sam_audio_with_dashboard: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    h = sam_audio_with_dashboard
    with caplog.at_level(logging.WARNING):
        await h.play("Quick")

    assert len(h.played) == 1
    assert h.dashboards == []
    assert any(
        "dashboard" in record.getMessage().lower()
        for record in caplog.records
        if record.levelno == logging.WARNING
    )


async def test_cast_display_is_released_after_playback_without_dashboard(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    request: Any,
    patched: None,
) -> None:
    h = await _harness(
        hass,
        tmp_path,
        freezer,
        request,
        [
            person_subentry(SAM_ID, "Sam", media_player=DISPLAY),
            routine_subentry(
                "r_sam_quick", SAM_ID, "Quick", QUICK, output_format="audio"
            ),
        ],
    )
    with patch(f"{BRIEFING}._async_release_player") as release:
        await h.play("Quick")

    release.assert_awaited_once_with(hass, DISPLAY)


async def test_display_with_dashboard_is_not_released(
    sam_visual_with_dashboard: Harness,
) -> None:
    h = sam_visual_with_dashboard
    with patch(f"{BRIEFING}._async_release_player") as release:
        await h.play("Quick")

    release.assert_not_awaited()
    assert len(h.dashboards) == 1


async def test_non_cast_speaker_is_not_released(sam_quick: Harness) -> None:
    with patch(f"{BRIEFING}._async_release_player") as release:
        await sam_quick.play("Quick")

    release.assert_not_awaited()


async def test_release_turns_off_an_idle_player(sam_quick: Harness) -> None:
    h = sam_quick
    h.hass.states.async_set(DISPLAY, "idle")
    await briefing._async_release_player(h.hass, DISPLAY)

    assert h.turned_off == [DISPLAY]


async def test_release_waits_for_the_player_to_stop(sam_quick: Harness) -> None:
    h = sam_quick
    with patch(f"{BRIEFING}.RELEASE_WAIT_SECONDS", 3600):
        task = asyncio.create_task(briefing._async_release_player(h.hass, DISPLAY))
        for _ in range(5):
            await asyncio.sleep(0)
        assert not task.done()
        h.hass.states.async_set(DISPLAY, "idle")
        await task

    assert h.turned_off == [DISPLAY]


async def test_release_leaves_a_player_that_keeps_playing(sam_quick: Harness) -> None:
    await briefing._async_release_player(sam_quick.hass, DISPLAY)  # stays "playing"

    assert sam_quick.turned_off == []


# --- visual and prebuild ----------------------------------------------------


async def test_visual_play_without_skip_uses_prestitched_video(
    prebuilt_alex_office_day: Harness,
) -> None:
    h = prebuilt_alex_office_day
    await h.play("Quick")

    assert h.ffmpeg.concats == []
    assert h.builder.calls == []
    assert _media_id(h.played[0]).endswith("routine_quick.mp4")
    assert h.played[0]["media_content_type"] == "video/mp4"


async def test_visual_prebuild_stores_stitched_routine_video(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    request: Any,
    patched: None,
) -> None:
    h = await _harness(
        hass,
        tmp_path,
        freezer,
        request,
        [
            person_subentry(ALEX_ID, "Alex", media_player=DISPLAY),
            routine_subentry("r_alex_quick", ALEX_ID, "Quick", QUICK),
        ],
    )
    await h.prebuild("Quick")

    assert h.ffmpeg.concats == [QUICK, QUICK]
    day = tmp_path / "people" / ALEX_ID / "2026-10-20"
    videos = list(day.glob("routine_quick.*.mp4"))
    assert len(videos) == 1
    assert videos[0].is_file()
    audio_meta = json.loads((day / "routine_quick-audio.json").read_text())
    assert audio_meta["audio"].endswith(".mp3")
    assert (day / audio_meta["audio"]).is_file()
    assert (day / "routine_quick.json").is_file()


async def test_prebuild_audio_routine_builds_no_clips_and_stitches_nothing(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    request: Any,
    patched: None,
) -> None:
    h = await _harness(
        hass,
        tmp_path,
        freezer,
        request,
        [
            person_subentry(ALEX_ID, "Alex", media_player=DISPLAY),
            routine_subentry(
                "r_alex_quick", ALEX_ID, "Quick", QUICK, output_format="audio"
            ),
        ],
    )
    await h.prebuild("Quick")

    assert h.builder.visual_calls == [set()]
    assert h.ffmpeg.concats == []
    assert h.hass.states.get("sensor.cast_studio_alex_quick").state == "ready"


async def test_prebuild_audio_routine_keeps_clips_a_visual_routine_shares(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    request: Any,
    patched: None,
) -> None:
    h = await _harness(
        hass,
        tmp_path,
        freezer,
        request,
        [
            _sam(),
            routine_subentry(
                "r_sam_night",
                SAM_ID,
                "Night",
                ["greeting", "closing"],
                output_format="audio",
            ),
            routine_subentry("r_sam_quick", SAM_ID, "Quick", QUICK),
        ],
    )
    await h.prebuild("Night")

    # Quick (visual) also uses the greeting, so it keeps its clip.
    assert h.builder.visual_calls == [{"greeting"}]
    store: SegmentStore = h.hass.data[DOMAIN][briefing.DATA_STORE]
    sam = h.person(SAM_ID)
    assert store.get_fresh(sam, "greeting", at(8)).clip_path is not None
    assert store.get_fresh(sam, "closing", at(8)).clip_path is None


async def test_prebuild_person_shared_segment_built_visual_once(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    request: Any,
    patched: None,
) -> None:
    h = await _harness(
        hass,
        tmp_path,
        freezer,
        request,
        [
            _sam(),
            routine_subentry(
                "r_sam_night",
                SAM_ID,
                "Night",
                ["greeting", "closing"],
                output_format="audio",
            ),
            routine_subentry("r_sam_quick", SAM_ID, "Quick", QUICK),
        ],
    )
    await briefing.async_prebuild(h.hass, h.entry, person=h.person(SAM_ID))

    assert h.builder.calls == [["greeting", "closing", "weather"]]
    assert h.builder.visual_calls == [{"greeting", "weather"}]
    for sensor in ("sensor.cast_studio_sam_night", "sensor.cast_studio_sam_quick"):
        assert h.hass.states.get(sensor).state == "ready"
    day = tmp_path / "people" / SAM_ID / "2026-10-20"
    assert (day / "routine_quick.json").is_file()
    assert not (day / "routine_night.json").exists()
    assert not (day / "routine_night-audio.json").exists()


async def test_visual_skip_played_stitches_clips_at_play_time(
    prebuilt_alex_office_day: Harness,
) -> None:
    h = prebuilt_alex_office_day
    hist = await briefing.async_get_play_history(h.hass)
    await hist.async_mark_played(
        h.person(ALEX_ID), h.routine("Quick"), ["greeting"], at(8)
    )
    await h.play("Quick", skip_played=True)

    assert _media_id(h.played[0]).endswith("weather.mp4")
    assert h.ffmpeg.concats == []


async def test_prestitched_video_ignored_after_segment_rebuilt(
    prebuilt_alex_office_day: Harness, freezer: FrozenDateTimeFactory
) -> None:
    h = prebuilt_alex_office_day
    freezer.move_to(at(8, 30))
    # A later live build of one segment makes the stored video out of date.
    store: SegmentStore = h.hass.data[DOMAIN][briefing.DATA_STORE]
    await h.builder(h.hass, store, h.person(ALEX_ID), ["weather"], {}, now=at(8, 30))
    await h.play("Quick")

    assert h.ffmpeg.concats == [QUICK]
    assert not _media_id(h.played[0]).endswith("routine_quick.mp4")


async def test_visual_routine_on_speaker_plays_prebuilt_audio(
    prebuilt_alex_office_day: Harness,
) -> None:
    h = prebuilt_alex_office_day
    await h.play("Quick", target=SPEAKER)

    assert _media_id(h.played[0]).endswith("routine_quick-audio.mp3")
    assert h.builder.calls == []
    assert h.ffmpeg.concats == []


async def test_visual_routine_on_speaker_without_routine_audio_stitches_mp3s(
    prebuilt_alex_office_day: Harness, tmp_path: Path
) -> None:
    h = prebuilt_alex_office_day
    day = tmp_path / "people" / ALEX_ID / "2026-10-20"
    (day / "routine_quick-audio.json").unlink()
    await h.play("Quick", target=SPEAKER)

    assert h.builder.calls == []
    assert h.ffmpeg.concats == [QUICK]
    assert _media_id(h.played[0]).endswith("play_quick.mp3")


async def test_audio_routine_on_display_plays_audio(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    request: Any,
    patched: None,
) -> None:
    h = await _harness(
        hass,
        tmp_path,
        freezer,
        request,
        [
            person_subentry(ALEX_ID, "Alex", media_player=DISPLAY),
            routine_subentry(
                "r_alex_quick", ALEX_ID, "Quick", QUICK, output_format="audio"
            ),
        ],
    )
    await h.play("Quick")

    assert h.builder.visual_calls == [set()]
    assert _media_id(h.played[0]).endswith("play_quick.mp3")


async def test_visual_routine_on_speaker_builds_missing_audio_only(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    request: Any,
    patched: None,
) -> None:
    h = await _harness(
        hass,
        tmp_path,
        freezer,
        request,
        [
            person_subentry(ALEX_ID, "Alex", media_player=DISPLAY),
            routine_subentry("r_alex_quick", ALEX_ID, "Quick", QUICK),
        ],
    )
    await h.play("Quick", target=SPEAKER)

    assert h.builder.visual_calls == [set()]
    assert _media_id(h.played[0]).endswith("play_quick.mp3")


async def test_all_heard_on_speaker_plays_mp3(
    prebuilt_alex_office_day: Harness,
) -> None:
    h = prebuilt_alex_office_day
    hist = await briefing.async_get_play_history(h.hass)
    await hist.async_mark_played(h.person(ALEX_ID), h.routine("Quick"), QUICK, at(8))
    await h.play("Quick", skip_played=True, target=SPEAKER)

    assert _media_id(h.played[0]).endswith(f"{ALL_HEARD_DIR}/{ALL_HEARD_KEY}.mp3")


async def test_prebuild_person_builds_union_of_routines(
    sam_quick_and_full: Harness,
) -> None:
    h = sam_quick_and_full
    await briefing.async_prebuild(h.hass, h.entry, person=h.person(SAM_ID))

    assert h.builder.calls == [FULL]
    assert h.played == []
    for sensor in ("sensor.cast_studio_sam_quick", "sensor.cast_studio_sam_full"):
        assert h.hass.states.get(sensor).state == "ready"


async def test_prebuild_requires_exactly_one_target(sam_quick: Harness) -> None:
    h = sam_quick
    with pytest.raises(ValueError, match="exactly one"):
        await briefing.async_prebuild(h.hass, h.entry)
    with pytest.raises(ValueError, match="exactly one"):
        await briefing.async_prebuild(
            h.hass, h.entry, routine=h.routine("Quick"), person=h.person(SAM_ID)
        )


def test_get_lock_is_per_person(hass: HomeAssistant) -> None:
    assert briefing.get_lock(hass, SAM_ID) is briefing.get_lock(hass, SAM_ID)
    assert briefing.get_lock(hass, SAM_ID) is not briefing.get_lock(hass, ALEX_ID)


# --- fix round 1 --------------------------------------------------------------


async def test_switch_audio_to_visual_rebuilds_segments_with_clips(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    request: Any,
    patched: None,
) -> None:
    h = await _harness(
        hass,
        tmp_path,
        freezer,
        request,
        [
            person_subentry(ALEX_ID, "Alex", media_player=DISPLAY),
            routine_subentry(
                "r_alex_quick", ALEX_ID, "Quick", QUICK, output_format="audio"
            ),
        ],
    )
    await h.prebuild("Quick")
    h.builder.calls.clear()
    h.ffmpeg.concats.clear()

    subentry = h.entry.subentries["r_alex_quick"]
    hass.config_entries.async_update_subentry(
        h.entry, subentry, data={**subentry.data, "output_format": "visual"}
    )
    await h.play("Quick")

    # The audio-only segments have no clips: they are rebuilt, then stitched.
    assert h.builder.calls == [QUICK]
    assert h.ffmpeg.concats == [QUICK]
    assert _media_id(h.played[0]).endswith("play_quick.mp4")
    state = hass.states.get("sensor.cast_studio_alex_quick")
    assert state.attributes["ready_segments"] == QUICK


async def test_prebuild_does_not_flip_a_playing_routine(sam_quick: Harness) -> None:
    h = sam_quick
    sensor = briefing.async_get_or_create_briefing_sensor(
        h.hass, h.person(SAM_ID), h.routine("Quick")
    )
    sensor.set_briefing_state("playing")
    states: list[str] = []
    original = sensor.set_briefing_state

    def _spy(state: str, **kwargs: Any) -> None:
        states.append(state)
        original(state, **kwargs)

    with patch.object(sensor, "set_briefing_state", side_effect=_spy):
        await h.prebuild("Quick")

    assert "generating" not in states
    assert h.hass.states.get("sensor.cast_studio_sam_quick").state == "playing"
    assert (
        h.hass.states.get("sensor.cast_studio_sam_quick").attributes["ready_segments"]
        == QUICK
    )


async def test_prebuild_shows_generating_only_while_holding_the_lock(
    sam_quick: Harness,
) -> None:
    h = sam_quick
    lock = briefing.get_lock(h.hass, SAM_ID)
    await lock.acquire()
    task = asyncio.create_task(
        briefing.async_prebuild(h.hass, h.entry, routine=h.routine("Quick"))
    )
    for _ in range(5):
        await asyncio.sleep(0)
    assert h.hass.states.get("sensor.cast_studio_sam_quick").state != "generating"
    lock.release()
    await task
    assert h.hass.states.get("sensor.cast_studio_sam_quick").state == "ready"


async def test_failure_after_play_dispatch_does_not_set_error(
    sam_quick: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    h = sam_quick
    history = await briefing.async_get_play_history(h.hass)
    with patch.object(history, "async_mark_played", side_effect=OSError("disk full")):
        await h.play("Quick")

    assert len(h.played) == 1
    assert h.hass.states.get("sensor.cast_studio_sam_quick").state == "ready"
    assert "Could not record played segments" in caplog.text


@pytest.mark.parametrize("player_state", ["unavailable", "off"])
async def test_play_that_never_starts_shows_error(
    sam_visual_with_dashboard: Harness,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
    player_state: str,
) -> None:
    h = sam_visual_with_dashboard
    h.hass.states.async_set(DISPLAY, "off")
    await briefing.async_play(h.hass, h.entry, h.routine("Quick"))
    # The cast fails: the display stays off or drops off instead of playing.
    h.hass.states.async_set(DISPLAY, player_state)
    for _ in range(10):
        await asyncio.sleep(0)
    assert h.hass.states.get("sensor.cast_studio_sam_quick").state == "playing"

    freezer.tick(30)
    async_fire_time_changed(h.hass)
    await h.hass.async_block_till_done()

    assert h.hass.states.get("sensor.cast_studio_sam_quick").state == "error"
    assert "never started playing" in caplog.text


async def test_play_that_never_starts_keeps_its_segments_unheard(
    sam_visual_with_dashboard: Harness, freezer: FrozenDateTimeFactory
) -> None:
    h = sam_visual_with_dashboard
    sam = h.person(SAM_ID)
    history = await briefing.async_get_play_history(h.hass)
    await history.async_mark_played(sam, h.routine("Quick"), ["greeting"], at(8))
    h.hass.states.async_set(DISPLAY, "off")
    await briefing.async_play(h.hass, h.entry, h.routine("Quick"))
    for _ in range(10):
        await asyncio.sleep(0)
    assert history.played(sam, h.routine("Quick"), at(8)) == {"greeting", "weather"}

    freezer.tick(30)
    async_fire_time_changed(h.hass)
    await h.hass.async_block_till_done()

    # Only what this play marked is undone; the earlier greeting stays heard.
    assert history.played(sam, h.routine("Quick"), at(8, 1)) == {"greeting"}


# --- final review fixes -------------------------------------------------------


def _url_name(data: dict[str, Any]) -> str:
    return Path(str(data["media_content_id"]).split("?")[0]).name


def _sidecar_video(tmp_path: Path, day: str = "2026-10-20") -> str:
    sidecar = tmp_path / "people" / ALEX_ID / day / "routine_quick.json"
    return json.loads(sidecar.read_text(encoding="utf-8"))["video"]


async def test_prebuild_during_playback_never_overwrites_streamed_files(
    prebuilt_alex_office_day: Harness,
    freezer: FrozenDateTimeFactory,
    tmp_path: Path,
) -> None:
    h = prebuilt_alex_office_day
    day = tmp_path / "people" / ALEX_ID / "2026-10-20"
    store: SegmentStore = h.hass.data[DOMAIN][briefing.DATA_STORE]
    alex = h.person(ALEX_ID)

    # A single-segment play streams the segment's own clip.
    hist = await briefing.async_get_play_history(h.hass)
    await hist.async_mark_played(alex, h.routine("Quick"), ["greeting"], at(8))
    await h.play("Quick", skip_played=True)
    streamed_clip = _url_name(h.played[0])
    weather = store.get_fresh(alex, "weather", at(8))
    assert streamed_clip == Path(weather.clip_path).name
    # A full play streams the pre-stitched routine video.
    await h.play("Quick")
    streamed_video = _url_name(h.played[1])
    assert streamed_video == _sidecar_video(tmp_path)

    # A pre-build while both may still be streaming.
    freezer.move_to(at(8, 15))
    await h.prebuild("Quick")

    assert (day / streamed_video).is_file()
    assert (day / streamed_clip).is_file()
    assert _sidecar_video(tmp_path) != streamed_video
    new_clip = Path(store.get_fresh(alex, "weather", at(8, 15)).clip_path).name
    assert new_clip != streamed_clip
    assert (day / new_clip).is_file()

    # The next play uses the new build.
    await h.play("Quick")
    assert _url_name(h.played[2]) == _sidecar_video(tmp_path)


async def test_newer_play_supersedes_earlier_play_of_same_routine(
    sam_visual_with_dashboard: Harness,
) -> None:
    h = sam_visual_with_dashboard
    # The display is idle, so each play's lifecycle waits for it to play.
    h.hass.states.async_set(DISPLAY, "idle", {"app_name": "Default Media Receiver"})

    await briefing.async_play(h.hass, h.entry, h.routine("Quick"))
    await briefing.async_play(h.hass, h.entry, h.routine("Quick"))
    for _ in range(10):
        await asyncio.sleep(0)

    # Play 1's lifecycle has ended (superseded): no dashboard, no sensor reset.
    assert len(h.played) == 2
    assert h.dashboards == []
    assert h.hass.states.get("sensor.cast_studio_sam_quick").state == "playing"

    # Play 2 starts and ends: its dashboard runs, once.
    h.hass.states.async_set(
        DISPLAY,
        "playing",
        {
            "app_name": "Default Media Receiver",
            "media_content_id": h.played[1]["media_content_id"],
        },
    )
    await h.hass.async_block_till_done()

    assert [name for name, _ in h.events] == [
        "play_media",
        "play_media",
        "show_lovelace_view",
    ]
    assert h.hass.states.get("sensor.cast_studio_sam_quick").state == "ready"
    assert briefing.DATA_PLAYS not in h.hass.data[DOMAIN] or (
        SAM_ID not in h.hass.data[DOMAIN][briefing.DATA_PLAYS]
    )


async def test_newer_play_of_other_routine_supersedes_dashboard(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    request: Any,
    patched: None,
) -> None:
    h = await _harness(
        hass,
        tmp_path,
        freezer,
        request,
        [
            person_subentry(SAM_ID, "Sam", media_player=DISPLAY),
            dashboard_routine("r_sam_quick", SAM_ID, "Quick", QUICK),
            routine_subentry("r_sam_full", SAM_ID, "Full", FULL, output_format="audio"),
        ],
    )
    hass.states.async_set(DISPLAY, "idle", {"app_name": "Default Media Receiver"})

    await briefing.async_play(hass, h.entry, h.routine("Quick"))
    await briefing.async_play(hass, h.entry, h.routine("Full"), skip_played=True)
    for _ in range(10):
        await asyncio.sleep(0)

    # Quick was replaced: it is no longer playing and shows no dashboard.
    assert h.dashboards == []
    assert hass.states.get("sensor.cast_studio_sam_quick").state == "ready"
    assert hass.states.get("sensor.cast_studio_sam_full").state == "playing"

    hass.states.async_set(
        DISPLAY,
        "playing",
        {
            "app_name": "Default Media Receiver",
            "media_content_id": h.played[1]["media_content_id"],
        },
    )
    await hass.async_block_till_done()
    assert h.dashboards == []  # Full has no dashboard; Quick's never ran
    assert hass.states.get("sensor.cast_studio_sam_full").state == "ready"


async def test_play_only_setup_prunes_earlier_briefing_days(
    sam_quick: Harness, freezer: FrozenDateTimeFactory, tmp_path: Path
) -> None:
    h = sam_quick
    person_dir = tmp_path / "people" / SAM_ID
    await h.play("Quick")
    assert (person_dir / "2026-10-20").is_dir()

    freezer.move_to(at(8, day=21))
    await h.play("Quick")

    assert not (person_dir / "2026-10-20").exists()
    assert (person_dir / "2026-10-21").is_dir()
    assert len(h.played) == 2


async def test_play_rebuilds_the_commute_live_after_a_prebuild(
    sam_full: Harness,
) -> None:
    h = sam_full
    await h.prebuild("Full")
    h.builder.calls.clear()

    await h.play("Full")

    assert h.builder.calls == [["commute"]]
    assert len(h.played) == 1


async def test_failed_live_commute_falls_back_to_the_prebuilt_one(
    sam_full: Harness,
) -> None:
    h = sam_full
    await h.prebuild("Full")
    h.ffmpeg.concats.clear()
    h.builder.fail = {"commute"}

    await h.play("Full")

    assert "commute" in h.ffmpeg.concats[-1]
    assert len(h.played) == 1
