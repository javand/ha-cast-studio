"""Tests for CastStudio's ``prebuild`` and ``play`` actions and their sensors."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.cast_studio.const import DOMAIN

if TYPE_CHECKING:
    from pathlib import Path


async def _setup_entry_with_sam_quick(hass: Any, tmp_path: Path) -> Any:
    """Set up a loaded entry where Sam (audio) has a routine called Quick."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.cast_studio.const import CONF_OUTPUT_DIR

    from .conftest import SAM_ID, person_subentry, routine_subentry

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id=DOMAIN,
        title="CastStudio",
        data={CONF_OUTPUT_DIR: str(tmp_path)},
        subentries_data=[
            person_subentry(
                SAM_ID,
                "Sam",
                media_player="media_player.kitchen_speaker",
            ),
            routine_subentry("r_sam_quick", SAM_ID, "Quick", ["greeting", "weather"]),
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
async def sam_quick(hass: Any, tmp_path: Path) -> Any:
    """A set-up entry with Sam and the Quick routine."""
    return await _setup_entry_with_sam_quick(hass, tmp_path)


async def test_only_the_current_actions_are_registered(
    hass: Any, sam_quick: Any
) -> None:
    """The old pipeline's actions are gone; briefing and announcement ones remain."""
    assert set(hass.services.async_services()[DOMAIN]) == {
        "prebuild",
        "play",
        "rebuild_announcement",
        "announce",
    }


async def test_play_and_prebuild_actions_resolve_routines(
    hass: Any, sam_quick: Any
) -> None:
    """The new actions look routines and people up live from the entry."""
    import voluptuous as vol

    with (
        patch(
            "custom_components.cast_studio.briefing.async_play", new=AsyncMock()
        ) as play,
        patch(
            "custom_components.cast_studio.briefing.async_prebuild", new=AsyncMock()
        ) as prebuild,
    ):
        await hass.services.async_call(
            DOMAIN,
            "play",
            {"routine": "Quick", "person": "Sam", "skip_played": True},
            blocking=True,
        )
        assert play.await_args.args[2].routine_id == "r_sam_quick"
        assert play.await_args.kwargs == {
            "target": None,
            "skip_played": True,
            "volume": None,
        }

        await hass.services.async_call(
            DOMAIN, "prebuild", {"person": "sam"}, blocking=True
        )
        assert prebuild.await_args.kwargs["person"].name == "Sam"

        await hass.services.async_call(
            DOMAIN, "prebuild", {"routine": "Quick", "person": "Sam"}, blocking=True
        )
        assert prebuild.await_args.kwargs["routine"].routine_id == "r_sam_quick"

        with pytest.raises(vol.Invalid):
            await hass.services.async_call(DOMAIN, "prebuild", {}, blocking=True)

        await hass.services.async_call(
            DOMAIN, "play", {"routine": "Quick", "volume": 0.5}, blocking=True
        )
        assert play.await_args.kwargs["volume"] == 0.5

        for bad in (1.5, -0.1):
            with pytest.raises(vol.Invalid):
                await hass.services.async_call(
                    DOMAIN, "play", {"routine": "Quick", "volume": bad}, blocking=True
                )


async def test_routine_sensor_entity_id_is_person_and_routine_slug(
    hass: Any, sam_quick: Any
) -> None:
    """Spec §5.7: new sensors are keyed by person and routine."""
    state = hass.states.get("sensor.cast_studio_sam_quick")
    assert state is not None
    assert state.state == "idle"
    assert state.attributes["person"] == "Sam"
    assert state.attributes["routine"] == "Quick"
    assert state.attributes["briefing_day"]
    assert state.attributes["ready_segments"] == []


async def test_play_action_target_must_be_a_media_player(
    hass: Any, sam_quick: Any
) -> None:
    """The play action's target is limited to the media_player domain."""
    import voluptuous as vol

    with (
        patch(
            "custom_components.cast_studio.briefing.async_play", new=AsyncMock()
        ) as play,
        pytest.raises(vol.Invalid),
    ):
        await hass.services.async_call(
            DOMAIN,
            "play",
            {"routine": "Quick", "target": "light.kitchen"},
            blocking=True,
        )
    play.assert_not_awaited()


def test_resolve_cast_media_url_maps_www_to_local() -> None:
    """Verify resolve_cast_media_url converts a path in /config/www/ to HTTP URL."""
    from custom_components.cast_studio.orchestrator import resolve_cast_media_url

    with patch(
        "custom_components.cast_studio.orchestrator.get_url",
        return_value="http://192.168.1.50:8123",
    ):
        result = resolve_cast_media_url(
            MagicMock(),
            "/config/www/cast_studio/people/p/2026-10-24/routine_quick.mp4",
        )
        assert result.startswith(
            "http://192.168.1.50:8123/local/cast_studio/people/p/2026-10-24/routine_quick.mp4?v="
        )
