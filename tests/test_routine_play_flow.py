"""Tests for playing a routine from its gear dialog in the configuration UI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import data_entry_flow
from homeassistant.exceptions import HomeAssistantError

from custom_components.cast_studio.people import find_routine

from .conftest import SAM_ID, make_entry, person_subentry, routine_subentry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

ROUTINE_ID = "r_sam_quick"
SPEAKER = "media_player.sam_speaker"
GUEST = "media_player.guest_speaker"
BRIEFING = "custom_components.cast_studio.briefing"
FLOW_TYPES = data_entry_flow.FlowResultType


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """Sam, whose output device is a speaker, with a Quick routine."""
    entry = make_entry(
        [
            person_subentry(SAM_ID, "Sam", media_player=SPEAKER),
            routine_subentry(ROUTINE_ID, SAM_ID, "Quick", ["greeting", "weather"]),
        ]
    )
    entry.add_to_hass(hass)
    return entry


async def _open_gear(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    return await entry.start_subentry_reconfigure_flow(hass, ROUTINE_ID)


async def _choose(
    hass: HomeAssistant, result: dict[str, Any], option: str
) -> dict[str, Any]:
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": option}
    )


async def _run(
    hass: HomeAssistant, result: dict[str, Any], user_input: dict[str, Any]
) -> dict[str, Any]:
    """Submit the play form, then follow the progress screens to the end."""
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input
    )
    for _ in range(10):
        if result["type"] not in (
            FLOW_TYPES.SHOW_PROGRESS,
            FLOW_TYPES.SHOW_PROGRESS_DONE,
        ):
            break
        await hass.async_block_till_done()
        result = await hass.config_entries.subentries.async_configure(result["flow_id"])
    return result


async def test_gear_opens_a_menu_of_edit_play_and_regenerate(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await _open_gear(hass, entry)

    assert result["type"] == FLOW_TYPES.MENU
    assert result["step_id"] == "reconfigure"
    assert result["menu_options"] == ["edit", "play", "regenerate_play"]


async def test_edit_option_opens_the_routine_form_and_saves(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await _choose(hass, await _open_gear(hass, entry), "edit")

    assert result["type"] == FLOW_TYPES.FORM
    assert result["step_id"] == "edit"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"person_id": SAM_ID, "name": "Quick", "segments": ["closing"]},
    )
    assert result["type"] == FLOW_TYPES.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert find_routine(entry, "Quick").segments == ("closing",)


@pytest.mark.parametrize("option", ["play", "regenerate_play"])
async def test_play_form_asks_for_a_device_and_volume(
    hass: HomeAssistant, entry: MockConfigEntry, option: str
) -> None:
    result = await _choose(hass, await _open_gear(hass, entry), option)

    assert result["type"] == FLOW_TYPES.FORM
    assert result["step_id"] == option
    schema = {str(k): k for k in result["data_schema"].schema}
    assert set(schema) == {"target", "volume"}
    # The person's own output device is offered, not forced.
    assert schema["target"].description == {"suggested_value": SPEAKER}
    target = result["data_schema"].schema[schema["target"]]
    assert target.config["domain"] == ["media_player"]
    volume = result["data_schema"].schema[schema["volume"]].config
    assert (volume["min"], volume["max"]) == (0, 1)


async def test_play_runs_the_routine_on_the_chosen_device(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await _choose(hass, await _open_gear(hass, entry), "play")
    with (
        patch(f"{BRIEFING}.async_play", new=AsyncMock()) as play,
        patch(f"{BRIEFING}.async_prebuild", new=AsyncMock()) as prebuild,
    ):
        result = await _run(hass, result, {"target": GUEST, "volume": 0.5})

    prebuild.assert_not_awaited()
    play.assert_awaited_once()
    assert play.await_args.args[2].routine_id == ROUTINE_ID
    assert play.await_args.kwargs == {"target": GUEST, "volume": 0.5}
    assert result["type"] == FLOW_TYPES.ABORT
    assert result["reason"] == "played"
    assert result["description_placeholders"]["target"] == GUEST


async def test_play_without_a_choice_uses_the_defaults(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await _choose(hass, await _open_gear(hass, entry), "play")
    with patch(f"{BRIEFING}.async_play", new=AsyncMock()) as play:
        result = await _run(hass, result, {})

    assert play.await_args.kwargs == {"target": None, "volume": None}
    assert result["reason"] == "played"
    # With no device chosen, the message names the person's own.
    assert result["description_placeholders"]["target"] == SPEAKER


async def test_regenerate_rebuilds_the_routine_then_plays_it(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await _choose(hass, await _open_gear(hass, entry), "regenerate_play")
    calls: list[str] = []

    async def _prebuild(*_a: Any, **_k: Any) -> None:
        calls.append("prebuild")

    async def _play(*_a: Any, **_k: Any) -> None:
        calls.append("play")

    with (
        patch(f"{BRIEFING}.async_prebuild", new=_prebuild),
        patch(f"{BRIEFING}.async_play", new=_play),
    ):
        result = await _run(hass, result, {"target": GUEST})

    assert calls == ["prebuild", "play"]
    assert result["reason"] == "played"


async def test_a_failed_play_reports_the_reason(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await _choose(hass, await _open_gear(hass, entry), "play")
    failure = HomeAssistantError(f"Media player {GUEST} is unavailable")
    with patch(f"{BRIEFING}.async_play", new=AsyncMock(side_effect=failure)):
        result = await _run(hass, result, {"target": GUEST})

    assert result["type"] == FLOW_TYPES.ABORT
    assert result["reason"] == "play_failed"
    assert "is unavailable" in result["description_placeholders"]["error"]


async def test_a_failed_rebuild_does_not_play(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await _choose(hass, await _open_gear(hass, entry), "regenerate_play")
    with (
        patch(
            f"{BRIEFING}.async_prebuild",
            new=AsyncMock(side_effect=HomeAssistantError("no segment could be built")),
        ),
        patch(f"{BRIEFING}.async_play", new=AsyncMock()) as play,
    ):
        result = await _run(hass, result, {})

    play.assert_not_awaited()
    assert result["reason"] == "play_failed"
    assert "no segment could be built" in result["description_placeholders"]["error"]


async def test_result_names_segments_voiced_by_the_fallback_voice(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await _choose(hass, await _open_gear(hass, entry), "regenerate_play")
    with (
        patch(f"{BRIEFING}.async_prebuild", new=AsyncMock()),
        patch(f"{BRIEFING}.async_play", new=AsyncMock()),
        patch(f"{BRIEFING}.degraded_segment_keys", return_value=["weather"]),
    ):
        result = await _run(hass, result, {"target": GUEST})

    assert result["reason"] == "played_fallback_voice"
    assert result["description_placeholders"]["segments"] == "weather"


async def test_closing_the_dialog_does_not_cancel_the_work(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await _choose(hass, await _open_gear(hass, entry), "regenerate_play")
    release = asyncio.Event()
    finished: list[str] = []

    async def _prebuild(*_a: Any, **_k: Any) -> None:
        await release.wait()
        finished.append("prebuild")

    async def _play(*_a: Any, **_k: Any) -> None:
        finished.append("play")

    with (
        patch(f"{BRIEFING}.async_prebuild", new=_prebuild),
        patch(f"{BRIEFING}.async_play", new=_play),
    ):
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {}
        )
        assert result["type"] == FLOW_TYPES.SHOW_PROGRESS
        # The dialog is closed while the routine is still rebuilding.
        hass.config_entries.subentries.async_abort(result["flow_id"])
        release.set()
        await hass.async_block_till_done()

    assert finished == ["prebuild", "play"]


def test_strings_cover_the_menu_and_play_steps() -> None:
    base = Path(__file__).parent.parent / "custom_components" / "cast_studio"
    for path in (base / "strings.json", base / "translations" / "en.json"):
        routine = json.loads(path.read_text(encoding="utf-8"))["config_subentries"][
            "routine"
        ]
        steps = routine["step"]
        assert set(steps["reconfigure"]["menu_options"]) == {
            "edit",
            "play",
            "regenerate_play",
        }
        assert "name" in steps["edit"]["data"]
        for step in ("play", "regenerate_play"):
            assert set(steps[step]["data"]) == {"target", "volume"}
        assert {"playing", "regenerating"} <= set(routine["progress"])
        assert {"played", "played_fallback_voice", "play_failed"} <= set(
            routine["abort"]
        )
