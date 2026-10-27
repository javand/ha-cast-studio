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
PLAY_ON = "select.cast_studio_play_on_sam_quick"
SENSOR = "sensor.cast_studio_sam_quick"


@pytest.fixture
async def entry(hass: HomeAssistant, tmp_path: Path) -> MockConfigEntry:
    """A loaded entry: Sam, whose output device is a speaker, with Quick."""
    for player in (SPEAKER, GUEST):
        hass.states.async_set(player, "idle")
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
        for entity_id in (PLAY, REBUILD, REGENERATE, PLAY_ON, SENSOR)
    }
    assert len(devices) == 1
    device = dr.async_get(hass).async_get(devices.pop())
    assert device.name == "Quick (Sam)"


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
