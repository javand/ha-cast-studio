"""Tests for CastStudio's ``prebuild`` and ``play`` actions and their sensors."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cast_studio.const import CONF_OUTPUT_DIR, DOMAIN
from custom_components.cast_studio.slide_renderer import render_template_slide

from .conftest import SAM_ID, announcement_subentry, person_subentry, routine_subentry


async def _setup_entry_with_sam_quick(hass: Any, tmp_path: Path) -> Any:
    """Set up a loaded entry where Sam (audio) has a routine called Quick."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.cast_studio.const import CONF_OUTPUT_DIR

    from .conftest import SAM_ID, person_subentry

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
        "refresh",
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


async def test_refresh_action_resolves_routine_and_person(
    hass: Any, sam_quick: Any
) -> None:
    """``refresh`` targets a routine or a person, like ``prebuild``."""
    import voluptuous as vol
    from homeassistant.exceptions import HomeAssistantError

    with patch(
        "custom_components.cast_studio.briefing.async_refresh", new=AsyncMock()
    ) as refresh:
        await hass.services.async_call(
            DOMAIN, "refresh", {"routine": "Quick", "person": "Sam"}, blocking=True
        )
        assert refresh.await_args.kwargs["routine"].routine_id == "r_sam_quick"

        await hass.services.async_call(
            DOMAIN, "refresh", {"person": "Sam"}, blocking=True
        )
        assert refresh.await_args.kwargs["person"].name == "Sam"
        assert refresh.await_args.kwargs["segments"] is None

        await hass.services.async_call(
            DOMAIN, "refresh", {"person": "Sam", "segments": "weather"}, blocking=True
        )
        assert refresh.await_args.kwargs["segments"] == ["weather"]

        with pytest.raises(vol.Invalid):
            await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
        with pytest.raises(HomeAssistantError, match="not found"):
            await hass.services.async_call(
                DOMAIN, "refresh", {"person": "Nobody"}, blocking=True
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
    assert state.attributes["dynamic_segments"] == ["weather"]


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


async def test_warriors_gameday_announcement_lifecycle(
    hass: Any, tmp_path: Path
) -> None:
    """Golden State Warriors end-to-end template announcement lifecycle test.

    Verifies:
    1. Custom background image + locked template layout with Jinja sensor bindings.
    2. Live evaluation against initial opponent (Miami Heat).
    3. External automation trigger via rebuild_announcement action.
    4. Deterministic rebuild when sensor updates to Sacramento Kings with zero AI tokens.
    """
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    bg_file = media_dir / "warriors_bg.png"
    Image.new("RGB", (1024, 600), "#002b49").save(bg_file)
    chime_file = media_dir / "espn_chime.mp3"
    chime_file.write_bytes(b"dummy_chime_sound")

    hass.config.media_dirs = {"local": str(media_dir)}

    # Initial opponent: Miami Heat
    hass.states.async_set(
        "sensor.nba_golden_state_warriors",
        "PRE",
        {"opponent_long_name": "Miami Heat", "clock": "11/5 - 10:00 PM EST"},
    )

    locked_layout = {
        "elements": [
            {
                "role": "title",
                "template": "Warriors vs. {{ state_attr('sensor.nba_golden_state_warriors', 'opponent_long_name') }}",
                "box": [400, 100, 500, 60],
                "font_size": 36,
                "font_weight": "bold",
                "color": "#ffffff",
            },
            {
                "role": "footer",
                "template": "Tip-off: {{ state_attr('sensor.nba_golden_state_warriors', 'clock') }}",
                "box": [400, 180, 500, 40],
                "font_size": 24,
                "font_weight": "regular",
                "color": "#ffc72c",
            },
        ]
    }

    ann_sub = announcement_subentry(
        "a_warriors",
        "Warriors Gameday",
        visual_mode="template",
        template_image="media-source://media_source/local/warriors_bg.png",
        template_prompt="Show Warriors gameday alert from sensor.nba_golden_state_warriors",
        template_layout=locked_layout,
        audio_mode="sound",
        chime_sound="media-source://media_source/local/espn_chime.mp3",
        hold_seconds=6.0,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=2,
        unique_id=DOMAIN,
        title="CastStudio",
        data={CONF_OUTPUT_DIR: str(tmp_path / "output")},
        subentries_data=[
            person_subentry(SAM_ID, "Sam"),
            ann_sub,
        ],
    )
    entry.add_to_hass(hass)

    rendered_elements_log: list[list[dict[str, Any]]] = []

    def _track_render(bg: Any, elements: Any, out: Any) -> str:
        rendered_elements_log.append(list(elements))
        return render_template_slide(bg, elements, out)

    async def _mock_ffmpeg(_h: Any, cmd: list[str], **_kw: Any) -> bool:
        Path(cmd[-1]).write_bytes(b"video_bytes")
        return True

    with (
        patch(
            "custom_components.cast_studio.ai_client.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.cast_studio.announcement_runtime.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.cast_studio.announcement_build.get_audio_duration",
            return_value=2.0,
        ),
        patch(
            "custom_components.cast_studio.segments.async_run_ffmpeg",
            new=AsyncMock(side_effect=_mock_ffmpeg),
        ),
        patch(
            "custom_components.cast_studio.announcement_build.render_template_slide",
            side_effect=_track_render,
        ),
        patch(
            "custom_components.cast_studio.announcement_layout.async_plan_announcement_layout",
        ) as mock_plan,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Step 1: Rebuild announcement for Miami Heat
        await hass.services.async_call(
            DOMAIN,
            "rebuild_announcement",
            {"announcement": "Warriors Gameday"},
            blocking=True,
        )

        assert len(rendered_elements_log) >= 1
        assert rendered_elements_log[-1][0]["text"] == "Warriors vs. Miami Heat"
        assert rendered_elements_log[-1][1]["text"] == "Tip-off: 11/5 - 10:00 PM EST"

        # Step 2: Next game - opponent changes to Sacramento Kings
        hass.states.async_set(
            "sensor.nba_golden_state_warriors",
            "PRE",
            {"opponent_long_name": "Sacramento Kings", "clock": "11/7 - 7:30 PM PST"},
        )

        # Step 3: External automation fires rebuild_announcement
        await hass.services.async_call(
            DOMAIN,
            "rebuild_announcement",
            {"announcement": "Warriors Gameday"},
            blocking=True,
        )

        assert len(rendered_elements_log) >= 2
        assert rendered_elements_log[-1][0]["text"] == "Warriors vs. Sacramento Kings"
        assert rendered_elements_log[-1][1]["text"] == "Tip-off: 11/7 - 7:30 PM PST"

        # Verify zero calls to AI layout planner on rebuilds!
        mock_plan.assert_not_called()

        # Verify final build state
        manager = hass.data[DOMAIN]["announcements"]
        build_state = manager.state("a_warriors")
        assert build_state is not None
        assert build_state.status == "ready"
