"""Tests for each routine's device: Play, Rebuild, Regenerate and play, Play on."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cast_studio.const import CONF_OUTPUT_DIR, DOMAIN

from .conftest import SAM_ID, person_subentry, routine_subentry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

ROUTINE_ID = "r_sam_quick"
SPEAKER = "media_player.sam_speaker"
GUEST = "media_player.guest_speaker"
BRIEFING = "custom_components.cast_studio.briefing"
PLAY = "button.cast_studio_play_sam_quick"
REBUILD = "button.cast_studio_rebuild_sam_quick"
REGENERATE = "button.cast_studio_regenerate_and_play_sam_quick"
REFRESH = "button.cast_studio_refresh_sam_quick"
PLAY_ON = "select.cast_studio_play_on_sam_quick"
SENSOR = "sensor.cast_studio_sam_quick"


def _cast_player(hass: HomeAssistant, entity_id: str, state: str = "idle") -> None:
    """Register ``entity_id`` as a Google Cast media player."""
    er.async_get(hass).async_get_or_create(
        "media_player", "cast", entity_id, suggested_object_id=entity_id.split(".")[1]
    )
    hass.states.async_set(entity_id, state)


@pytest.fixture
async def entry(hass: HomeAssistant, tmp_path: Path) -> MockConfigEntry:
    """A loaded entry: Sam, whose output device is a speaker, with Quick."""
    for player in (SPEAKER, GUEST):
        _cast_player(hass, player)
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=2,
        unique_id=DOMAIN,
        title="CastStudio",
        data={CONF_OUTPUT_DIR: str(tmp_path)},
        subentries_data=[
            person_subentry(SAM_ID, "Sam", media_player=SPEAKER),
            routine_subentry(ROUTINE_ID, SAM_ID, "Quick", ["greeting", "weather"]),
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )


async def _choose(hass: HomeAssistant, player: str) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": PLAY_ON, "option": player},
        blocking=True,
    )


def test_each_control_is_on_the_routines_own_device(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    entities = er.async_get(hass)
    devices = {
        entities.async_get(entity_id).device_id
        for entity_id in (PLAY, REBUILD, REGENERATE, REFRESH, PLAY_ON, SENSOR)
    }
    assert len(devices) == 1
    device = dr.async_get(hass).async_get(devices.pop())
    assert device.name == "Quick (Sam)"


async def test_all_controls_belong_to_the_routines_subentry(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    entities = er.async_get(hass)
    for entity_id in (PLAY, REBUILD, REGENERATE, REFRESH, PLAY_ON, SENSOR):
        assert entities.async_get(entity_id).config_subentry_id == ROUTINE_ID
    device = dr.async_get(hass).async_get(entities.async_get(PLAY).device_id)
    assert device.config_entries_subentries[entry.entry_id] == {ROUTINE_ID}


async def test_controls_survive_a_sensor_registered_before_the_device(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Upgrading: the status sensor is already registered, with no device."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=2,
        unique_id=DOMAIN,
        title="CastStudio",
        data={CONF_OUTPUT_DIR: str(tmp_path)},
        subentries_data=[
            person_subentry(SAM_ID, "Sam", media_player=SPEAKER),
            routine_subentry(ROUTINE_ID, SAM_ID, "Quick", ["greeting"]),
        ],
    )
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"cast_studio_routine_{ROUTINE_ID}",
        suggested_object_id="cast_studio_sam_quick",
        config_entry=entry,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entities = er.async_get(hass)
    for entity_id in (PLAY, REBUILD, REGENERATE, REFRESH, PLAY_ON, SENSOR):
        assert entities.async_get(entity_id) is not None, entity_id


async def test_play_plays_on_the_persons_output_device(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    with (
        patch(f"{BRIEFING}.async_play", new=AsyncMock()) as play,
        patch(f"{BRIEFING}.async_prebuild", new=AsyncMock()) as prebuild,
    ):
        await _press(hass, PLAY)

    prebuild.assert_not_awaited()
    play.assert_awaited_once()
    assert play.await_args.args[2].routine_id == ROUTINE_ID
    assert play.await_args.kwargs == {"target": SPEAKER}


async def test_play_on_starts_at_the_output_device_and_lists_players(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    state = hass.states.get(PLAY_ON)
    assert state.state == SPEAKER
    assert {SPEAKER, GUEST} <= set(state.attributes["options"])


async def test_play_uses_the_player_chosen_in_play_on(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    await _choose(hass, GUEST)

    assert hass.states.get(PLAY_ON).state == GUEST
    with patch(f"{BRIEFING}.async_play", new=AsyncMock()) as play:
        await _press(hass, PLAY)
    assert play.await_args.kwargs == {"target": GUEST}


async def test_a_new_output_device_replaces_an_earlier_play_on_choice(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    await _choose(hass, GUEST)
    person = next(s for s in entry.subentries.values() if s.subentry_type == "person")
    hass.config_entries.async_update_subentry(
        entry, person, data={**person.data, "media_player": "media_player.other"}
    )
    await hass.async_block_till_done()

    assert hass.states.get(PLAY_ON).state == "media_player.other"


async def test_rebuild_builds_the_routine_without_playing(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    with (
        patch(f"{BRIEFING}.async_play", new=AsyncMock()) as play,
        patch(f"{BRIEFING}.async_prebuild", new=AsyncMock()) as prebuild,
    ):
        await _press(hass, REBUILD)

    play.assert_not_awaited()
    prebuild.assert_awaited_once()
    assert prebuild.await_args.kwargs["routine"].routine_id == ROUTINE_ID


async def test_regenerate_and_play_rebuilds_then_plays_on_the_chosen_player(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    await _choose(hass, GUEST)
    calls: list[str] = []

    async def _prebuild(*_a: Any, **_k: Any) -> None:
        calls.append("prebuild")

    async def _play(*_a: Any, **kwargs: Any) -> None:
        calls.append(f"play {kwargs['target']}")

    with (
        patch(f"{BRIEFING}.async_prebuild", new=_prebuild),
        patch(f"{BRIEFING}.async_play", new=_play),
    ):
        await _press(hass, REGENERATE)

    assert calls == ["prebuild", f"play {GUEST}"]


async def test_a_failed_play_is_reported_to_the_user(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    failure = HomeAssistantError(f"Media player {GUEST} is unavailable")
    with (
        patch(f"{BRIEFING}.async_play", new=AsyncMock(side_effect=failure)),
        pytest.raises(HomeAssistantError, match="is unavailable"),
    ):
        await _press(hass, PLAY)


async def test_a_failed_rebuild_does_not_play(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    failure = HomeAssistantError("no segment could be built")
    with (
        patch(f"{BRIEFING}.async_prebuild", new=AsyncMock(side_effect=failure)),
        patch(f"{BRIEFING}.async_play", new=AsyncMock()) as play,
        pytest.raises(HomeAssistantError, match="no segment"),
    ):
        await _press(hass, REGENERATE)

    play.assert_not_awaited()


async def test_a_routine_added_later_gets_its_controls(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    data = routine_subentry("r_sam_late", SAM_ID, "Late", ["closing"])["data"]
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=MappingProxyType(data),
            subentry_type="routine",
            title="Late",
            unique_id=None,
            subentry_id="r_sam_late",
        ),
    )
    await hass.async_block_till_done()

    assert hass.states.get("button.cast_studio_play_sam_late") is not None
    assert hass.states.get("select.cast_studio_play_on_sam_late") is not None


async def test_startup_writes_each_entity_once_with_its_real_value(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """No placeholder state at startup, so the logbook shows no fake change."""
    import json

    from homeassistant.const import EVENT_STATE_CHANGED
    from homeassistant.util import dt as dt_util

    hass.states.async_set(SPEAKER, "idle")
    now = dt_util.now()
    day = now.date() if now.hour >= 3 else now.date().fromordinal(now.toordinal() - 1)
    folder = tmp_path / "people" / SAM_ID / day.isoformat()
    folder.mkdir(parents=True)
    segments = {}
    for key in ("greeting", "weather"):
        (folder / f"{key}.mp3").write_bytes(b"x")
        segments[key] = {
            "audio": f"{key}.mp3",
            "clip": None,
            "duration": 1.0,
            "built_at": now.isoformat(),
            "built_day": day.isoformat(),
            "degraded": False,
            "persona": "default_host",
        }
    (folder / "manifest.json").write_text(json.dumps({"segments": segments}))
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=2,
        unique_id=DOMAIN,
        title="CastStudio",
        data={CONF_OUTPUT_DIR: str(tmp_path)},
        subentries_data=[
            person_subentry(SAM_ID, "Sam", media_player=SPEAKER),
            routine_subentry(
                ROUTINE_ID,
                SAM_ID,
                "Quick",
                ["greeting", "weather"],
                output_format="audio",
            ),
        ],
    )
    entry.add_to_hass(hass)
    seen: dict[str, list[str]] = {PLAY_ON: [], SENSOR: []}

    def _record(event: Any) -> None:
        if event.data["entity_id"] in seen and event.data["new_state"]:
            seen[event.data["entity_id"]].append(event.data["new_state"].state)

    hass.bus.async_listen(EVENT_STATE_CHANGED, _record)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert seen[PLAY_ON][0] == SPEAKER
    assert set(seen[PLAY_ON]) == {SPEAKER}
    assert seen[SENSOR][0] == "ready"
    assert set(seen[SENSOR]) == {"ready"}


async def test_play_on_lists_only_players_a_routine_uses(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Cast players and configured players; no app players, nothing unavailable."""
    squeezebox = "media_player.bedroom_squeezebox"
    hass.states.async_set(squeezebox, "idle")
    hass.states.async_set("media_player.plex_apple_tv", "idle")
    hass.states.async_set("media_player.onkyo_receiver", "on")
    _cast_player(hass, "media_player.attic_speaker", "unavailable")
    person = next(s for s in entry.subentries.values() if s.subentry_type == "person")
    routine = entry.subentries[ROUTINE_ID]
    hass.config_entries.async_update_subentry(
        entry, routine, data={**routine.data, "dashboard_display": squeezebox}
    )
    await hass.async_block_till_done()
    assert person.data["media_player"] == SPEAKER

    options = hass.states.get(PLAY_ON).attributes["options"]
    assert options == sorted([GUEST, SPEAKER, squeezebox])


async def test_refresh_rebuilds_weather_and_commute_without_playing(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    with (
        patch(f"{BRIEFING}.async_play", new=AsyncMock()) as play,
        patch(f"{BRIEFING}.async_refresh", new=AsyncMock()) as refresh,
    ):
        await _press(hass, REFRESH)

    play.assert_not_awaited()
    refresh.assert_awaited_once()
    assert refresh.await_args.kwargs["routine"].routine_id == ROUTINE_ID


async def test_refresh_says_when_a_routine_has_nothing_to_refresh(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    routine = entry.subentries[ROUTINE_ID]
    hass.config_entries.async_update_subentry(
        entry, routine, data={**routine.data, "segments": ["greeting"]}
    )
    await hass.async_block_till_done()
    with pytest.raises(HomeAssistantError, match="has no segments set to refresh"):
        await _press(hass, REFRESH)


def test_rebuild_and_play_is_named_like_rebuild(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Renamed from Regenerate and play; the entity id stays for automations."""
    assert hass.states.get(REGENERATE).name == "Quick (Sam) Rebuild and play"
    assert hass.states.get(REFRESH).name == "Quick (Sam) Refresh"
