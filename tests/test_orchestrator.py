"""Tests for the routine orchestrator (action partitioning and playback sync)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cast_studio.orchestrator import (
    OrchestratorError,
    PlaybackNotStartedError,
    _async_sync_slides,
    _create_task,
    _is_routine_media_playing,
    async_execute_background_actions,
    async_play_and_sync_routine,
    get_media_payload,
    get_routine_sync_task,
    has_screen,
    resolve_cast_media_url,
)

CALL_COUNT_TWO = 2
CALL_COUNT_THREE = 3
CALL_COUNT_FOUR = 4


@pytest.mark.asyncio
async def test_execute_background_actions_concurrently():
    """Test concurrent execution of background actions with asyncio.gather."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()

    steps = [
        {
            "name": "morning_prep",
            "visual": False,
            "actions": [
                {
                    "action": "switch.turn_on",
                    "target": {"entity_id": "switch.espresso"},
                },
                {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}},
            ],
        }
    ]

    await async_execute_background_actions(hass, steps)
    assert hass.services.async_call.call_count == CALL_COUNT_TWO
    hass.services.async_call.assert_any_call(
        "switch",
        "turn_on",
        target={"entity_id": "switch.espresso"},
    )
    hass.services.async_call.assert_any_call(
        "light",
        "turn_on",
        target={"entity_id": "light.kitchen"},
    )


@pytest.mark.asyncio
async def test_play_and_sync_waits_for_playing_state():
    """Test play_and_sync registers state listener and calls play_media."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()

    # Mock media_player state listener
    with patch(
        "custom_components.cast_studio.orchestrator.async_track_state_change_event"
    ) as mock_track:
        metadata = {"video_path": "/local/morning.mp4", "slide_timestamps": []}
        await async_play_and_sync_routine(
            hass, "morning", "media_player.display", metadata, []
        )
        call_args = hass.services.async_call.await_args
        assert call_args[0][:2] == ("media_player", "play_media")
        assert call_args[0][2]["entity_id"] == "media_player.display"
        assert call_args[0][2]["media_content_id"].startswith("/local/morning.mp4?v=")
        assert call_args[0][2]["media_content_type"] == "video/mp4"
        mock_track.assert_called_once()


@pytest.mark.asyncio
async def test_execute_background_actions_direct_list_and_service_key():
    """Test background actions passed as direct list using service and service_data keys."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()

    actions = [
        {
            "service": "switch.turn_on",
            "target": {"entity_id": "switch.kettle"},
            "service_data": {"option": "fast"},
        },
        {
            "action": "climate.set_temperature",
            "target": {"entity_id": "climate.living_room"},
            "data": {"temperature": 70},
        },
    ]

    await async_execute_background_actions(hass, actions)
    assert hass.services.async_call.call_count == CALL_COUNT_TWO
    hass.services.async_call.assert_any_call(
        "switch",
        "turn_on",
        service_data={"option": "fast"},
        target={"entity_id": "switch.kettle"},
    )
    hass.services.async_call.assert_any_call(
        "climate",
        "set_temperature",
        service_data={"temperature": 70},
        target={"entity_id": "climate.living_room"},
    )


@pytest.mark.asyncio
async def test_execute_background_actions_skips_visual_steps():
    """Test that steps marked visual=True are ignored by background action executor."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()

    steps = [
        {
            "name": "visual_greeting",
            "visual": True,
            "actions": [
                {"action": "light.turn_on", "target": {"entity_id": "light.desk"}}
            ],
        },
        {
            "name": "prep",
            "visual": False,
            "actions": [
                {
                    "action": "switch.turn_on",
                    "target": {"entity_id": "switch.espresso"},
                }
            ],
        },
    ]

    await async_execute_background_actions(hass, steps)
    assert hass.services.async_call.call_count == 1
    hass.services.async_call.assert_awaited_once_with(
        "switch",
        "turn_on",
        target={"entity_id": "switch.espresso"},
    )


@pytest.mark.asyncio
async def test_execute_background_actions_error_tolerance():
    """Test that a failing action does not raise or block other concurrent actions."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock(
        side_effect=[Exception("Device offline"), None]
    )

    actions = [
        {"action": "switch.turn_on", "target": {"entity_id": "switch.broken"}},
        {"action": "light.turn_on", "target": {"entity_id": "light.working"}},
    ]

    # Should log exception and complete gracefully without raising
    await async_execute_background_actions(hass, actions)
    assert hass.services.async_call.call_count == CALL_COUNT_TWO


@pytest.mark.asyncio
async def test_execute_background_actions_skips_invalid():
    """Test skipping malformed action definitions safely."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()

    items = [
        "not a dict",
        {},
        {"invalid_key": "val"},
        {"action": "nodot"},
        123,
    ]

    await async_execute_background_actions(hass, items)  # type: ignore[arg-type]
    assert hass.services.async_call.call_count == 0


@pytest.mark.asyncio
async def test_play_and_sync_triggers_actions_on_playing():
    """Test player state transition to 'playing' triggers timed actions_on_display."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    mock_state = MagicMock()
    mock_state.state = "idle"
    hass.states.get.return_value = mock_state

    metadata = {
        "video_path": "/local/morning.mp4",
        "prepare_player": False,
        "slide_timestamps": [
            {
                "timestamp": 0.0,
                "actions_on_display": [
                    {
                        "action": "light.turn_on",
                        "target": {"entity_id": "light.kitchen"},
                    }
                ],
            },
            {
                "timestamp": 2.5,
                "actions_on_display": [
                    {
                        "action": "switch.turn_on",
                        "target": {"entity_id": "switch.coffee"},
                    }
                ],
            },
        ],
    }

    with (
        patch(
            "custom_components.cast_studio.orchestrator.async_track_state_change_event"
        ) as mock_track,
        patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
    ):
        await async_play_and_sync_routine(
            hass, "morning", "media_player.display", metadata, []
        )
        assert mock_track.call_count == 1
        state_cb = mock_track.call_args[0][2]

        # 1. State changed to buffering -> display actions should NOT fire yet
        event_buffering = MagicMock()
        event_buffering.data = {"new_state": MagicMock(state="buffering")}
        state_cb(event_buffering)
        # Only media_player.play_media was called
        assert hass.services.async_call.call_count == 1

        # 2. State changed to playing -> triggers sync
        event_playing = MagicMock()
        event_playing.data = {"new_state": MagicMock(state="playing")}
        sync_task = state_cb(event_playing)
        if sync_task:
            await sync_task

        # media_player.play_media + light.turn_on + switch.turn_on = 3 calls
        assert hass.services.async_call.call_count == CALL_COUNT_THREE
        mock_sleep.assert_awaited_with(pytest.approx(2.5, rel=1e-2))


@pytest.mark.asyncio
async def test_play_and_sync_audio_only():
    """Test audio-only routine playback with audio/mp3 and audio/wav."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()

    with patch(
        "custom_components.cast_studio.orchestrator.async_track_state_change_event"
    ):
        metadata_mp3 = {
            "video_path": None,
            "audio_path": "/local/morning.mp3",
            "slide_timestamps": [],
        }
        await async_play_and_sync_routine(
            hass, "morning", "media_player.speaker", metadata_mp3, []
        )
        call_args_mp3 = hass.services.async_call.await_args
        assert call_args_mp3[0][:2] == ("media_player", "play_media")
        assert call_args_mp3[0][2]["entity_id"] == "media_player.speaker"
        assert call_args_mp3[0][2]["media_content_id"].startswith(
            "/local/morning.mp3?v="
        )
        assert call_args_mp3[0][2]["media_content_type"] == "audio/mp3"

        metadata_wav = {
            "video_path": None,
            "audio_path": "/local/morning.wav",
            "slide_timestamps": [],
        }
        await async_play_and_sync_routine(
            hass, "morning", "media_player.speaker", metadata_wav, []
        )
        call_args_wav = hass.services.async_call.await_args
        assert call_args_wav[0][:2] == ("media_player", "play_media")
        assert call_args_wav[0][2]["entity_id"] == "media_player.speaker"
        assert call_args_wav[0][2]["media_content_id"].startswith(
            "/local/morning.wav?v="
        )
        assert call_args_wav[0][2]["media_content_type"] == "audio/wav"


@pytest.mark.asyncio
async def test_play_and_sync_already_playing():
    """Test that if the player is already 'playing' at call time, sync begins immediately."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    mock_state = MagicMock()
    mock_state.state = "playing"
    hass.states.get.return_value = mock_state

    metadata = {
        "video_path": "/local/morning.mp4",
        "prepare_player": False,
        "slide_timestamps": [
            {
                "timestamp": 0.0,
                "actions_on_display": [
                    {
                        "action": "light.turn_on",
                        "target": {"entity_id": "light.living_room"},
                    }
                ],
            }
        ],
    }

    with patch(
        "custom_components.cast_studio.orchestrator.async_track_state_change_event"
    ) as mock_track:
        sync_task = await async_play_and_sync_routine(
            hass, "morning", "media_player.display", metadata, []
        )
        # async_track_state_change_event should not be registered when already playing
        assert mock_track.call_count == 0

        if sync_task:
            await sync_task
        # 1 call for play_media, 1 call for light.turn_on
        assert hass.services.async_call.call_count == CALL_COUNT_TWO


def test_is_routine_media_playing_helper():
    """Test helper distinguishes routine media from Lovelace dashboard or other media."""
    # None or non-playing states
    assert not _is_routine_media_playing(None)
    assert not _is_routine_media_playing(MagicMock(state="idle"))
    assert not _is_routine_media_playing(MagicMock(state="buffering"))
    assert not _is_routine_media_playing(MagicMock(state="off"))

    # Lovelace app names and IDs must never be considered routine media
    lovelace_states = [
        MagicMock(state="playing", attributes={"app_name": "Home Assistant Lovelace"}),
        MagicMock(state="playing", attributes={"app_name": "Lovelace"}),
        MagicMock(state="playing", attributes={"app_name": "home assistant cast"}),
        MagicMock(state="playing", attributes={"app_id": "A078F6B0"}),
        MagicMock(state="playing", attributes={"app_id": "B12CE3C8"}),
    ]
    for state_obj in lovelace_states:
        assert not _is_routine_media_playing(
            state_obj,
            expected_media_url="http://ha.local:8123/local/cast_studio/morning_briefing.mp4?v=1",
            media_path="/config/www/cast_studio/morning_briefing.mp4",
        )

    # Completely different media content id (e.g. Spotify or radio)
    spotify_state = MagicMock(
        state="playing",
        attributes={"app_name": "Spotify", "media_content_id": "spotify:track:xyz123"},
    )
    assert not _is_routine_media_playing(
        spotify_state,
        expected_media_url="http://ha.local:8123/local/cast_studio/morning_briefing.mp4?v=1",
        media_path="/config/www/cast_studio/morning_briefing.mp4",
    )

    # Valid routine media playback in Default Media Receiver
    receiver_state = MagicMock(
        state="playing",
        attributes={
            "app_name": "Default Media Receiver",
            "app_id": "CC1AD845",
            "media_content_id": "http://ha.local:8123/local/cast_studio/morning_briefing.mp4?v=1",
        },
    )
    assert _is_routine_media_playing(
        receiver_state,
        expected_media_url="http://ha.local:8123/local/cast_studio/morning_briefing.mp4?v=1",
        media_path="/config/www/cast_studio/morning_briefing.mp4",
    )

    # Receiver in idle or buffering state while loading our media
    receiver_idle = MagicMock(
        state="idle",
        attributes={
            "app_name": "Default Media Receiver",
            "app_id": "CC1AD845",
            "media_content_id": "http://ha.local:8123/local/cast_studio/morning_briefing.mp4?v=1",
        },
    )
    assert _is_routine_media_playing(
        receiver_idle,
        expected_media_url="http://ha.local:8123/local/cast_studio/morning_briefing.mp4?v=1",
        media_path="/config/www/cast_studio/morning_briefing.mp4",
    )

    receiver_buffering = MagicMock(
        state="buffering",
        attributes={
            "app_name": "Default Media Receiver",
            "app_id": "CC1AD845",
            "media_content_id": "http://ha.local:8123/local/cast_studio/morning_briefing.mp4?v=1",
        },
    )
    assert _is_routine_media_playing(
        receiver_buffering,
        expected_media_url="http://ha.local:8123/local/cast_studio/morning_briefing.mp4?v=1",
        media_path="/config/www/cast_studio/morning_briefing.mp4",
    )

    # Generic or mock state without conflicting attributes
    generic_state = MagicMock(state="playing", attributes={})
    assert _is_routine_media_playing(generic_state)


@pytest.mark.asyncio
async def test_play_and_sync_ignores_lovelace_state():
    """Test that if the player is in 'playing' showing Lovelace, orchestrator waits for Default Media Receiver."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()

    # Initial state is playing Lovelace
    lovelace_state = MagicMock(
        state="playing",
        attributes={
            "app_name": "Home Assistant Lovelace",
            "app_id": "A078F6B0",
        },
    )
    hass.states.get.return_value = lovelace_state

    metadata = {
        "video_path": "/local/morning_briefing.mp4",
        "prepare_player": False,
        "duration": 5.0,
        "slide_timestamps": [
            {
                "timestamp": 0.0,
                "actions_on_display": [
                    {
                        "action": "light.turn_on",
                        "target": {"entity_id": "light.kitchen"},
                    }
                ],
            }
        ],
        "post_routine_actions": [
            {
                "action": "cast.show_lovelace_view",
                "target": {"entity_id": "media_player.display"},
                "data": {"dashboard_path": "nest-display", "view_path": "morning"},
            }
        ],
    }

    with (
        patch(
            "custom_components.cast_studio.orchestrator.async_track_state_change_event"
        ) as mock_track,
        patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
    ):
        playback_task = await async_play_and_sync_routine(
            hass, "morning", "media_player.display", metadata, []
        )
        # Should register state change listener despite current_state.state == "playing"
        assert mock_track.call_count == 1
        state_cb = mock_track.call_args[0][2]

        # 1. State change event while still in Lovelace -> ignored
        event_lovelace = MagicMock()
        event_lovelace.data = {
            "new_state": MagicMock(
                state="playing",
                attributes={"app_name": "Home Assistant Lovelace"},
            )
        }
        res1 = state_cb(event_lovelace)
        assert res1 is None
        # Light should NOT have turned on yet
        assert hass.services.async_call.call_count == 1

        # 2. State change event entering Default Media Receiver with routine video
        event_receiver = MagicMock()
        event_receiver.data = {
            "new_state": MagicMock(
                state="playing",
                attributes={
                    "app_name": "Default Media Receiver",
                    "media_content_id": "http://ha.local:8123/local/morning_briefing.mp4?v=123",
                },
            )
        }
        sync_task = state_cb(event_receiver)
        assert sync_task is not None

        await playback_task

        # play_media (1) + light.turn_on (1) + cast.show_lovelace_view (1) = 3 calls
        assert hass.services.async_call.call_count == CALL_COUNT_THREE
        # Verify sleep was awaited for the duration
        mock_sleep.assert_awaited_with(pytest.approx(5.0, rel=1e-2))


@pytest.mark.asyncio
async def test_play_and_sync_safety_timeout():
    """Test safety timeout clean up when player never enters 'playing' state."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    mock_state = MagicMock()
    mock_state.state = "idle"
    hass.states.get.return_value = mock_state

    metadata = {
        "video_path": "/local/morning.mp4",
        "prepare_player": False,
        "slide_timestamps": [
            {
                "timestamp": 0.0,
                "actions_on_display": [
                    {
                        "action": "light.turn_on",
                        "target": {"entity_id": "light.kitchen"},
                    }
                ],
            }
        ],
    }

    with (
        patch(
            "custom_components.cast_studio.orchestrator.async_track_state_change_event"
        ) as mock_track,
        patch(
            "custom_components.cast_studio.orchestrator.async_call_later"
        ) as mock_call_later,
    ):
        mock_unsub = MagicMock()
        mock_track.return_value = mock_unsub

        await async_play_and_sync_routine(
            hass, "morning", "media_player.display", metadata, []
        )
        assert mock_call_later.call_count == 1
        timeout_cb = mock_call_later.call_args[0][2]

        # Trigger timeout callback
        timeout_cb(None)

        # State listener unsubscriber should be called to clean up
        mock_unsub.assert_called_once()
        # Display action should NOT have been called
        assert hass.services.async_call.call_count == 1


def test_get_media_payload_validation():
    """Test media payload determination and validation errors."""
    with pytest.raises(OrchestratorError):
        get_media_payload({})

    path, ctype = get_media_payload({"video_path": "/path/test.mp4"})
    assert path == "/path/test.mp4"
    assert ctype == "video/mp4"

    path, ctype = get_media_payload({"audio_path": "/path/test.flac"})
    assert path == "/path/test.flac"
    assert ctype == "audio/flac"

    path, ctype = get_media_payload(
        {"media_path": "/path/test.mp3", "media_content_type": "audio/mpeg"}
    )
    assert path == "/path/test.mp3"
    assert ctype == "audio/mpeg"


@pytest.mark.asyncio
async def test_get_routine_sync_task_and_reentry():
    """Test get_routine_sync_task query and re-entry guards."""
    assert get_routine_sync_task("nonexistent") is None

    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    mock_state = MagicMock()
    mock_state.state = "idle"
    hass.states.get.return_value = mock_state

    metadata = {
        "video_path": "/local/test.mp4",
        "slide_timestamps": [
            {
                "timestamp": 0.0,
                "actions_on_display": [
                    {"action": "light.turn_on", "target": {"entity_id": "light.desk"}}
                ],
            }
        ],
    }

    with patch(
        "custom_components.cast_studio.orchestrator.async_track_state_change_event"
    ) as mock_track:
        await async_play_and_sync_routine(
            hass, "test_routine", "media_player.display", metadata, []
        )
        state_cb = mock_track.call_args[0][2]

        event_playing = MagicMock()
        event_playing.data = {"new_state": MagicMock(state="playing")}

        # First call starts sync and registers active task
        sync_task1 = state_cb(event_playing)
        assert sync_task1 is not None
        assert get_routine_sync_task("test_routine") == sync_task1

        # Re-entry: second call returns None
        sync_task2 = state_cb(event_playing)
        assert sync_task2 is None

        await sync_task1
        # Task cleanup callback removes task from _ROUTINE_SYNC_TASKS
        assert get_routine_sync_task("test_routine") is None


@pytest.mark.asyncio
async def test_background_actions_container_and_empty_actions():
    """Test step containers with background_actions and empty action lists."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()

    # None or non-list input
    await async_execute_background_actions(hass, None)  # type: ignore[arg-type]
    assert hass.services.async_call.call_count == 0

    # Empty list
    await async_execute_background_actions(hass, [])
    assert hass.services.async_call.call_count == 0

    # Step with BOTH actions and background_actions, plus root-level entity_id
    steps_both = [
        {
            "name": "morning",
            "visual": False,
            "actions": [{"action": "light.turn_on", "entity_id": "light.kitchen"}],
            "background_actions": [
                {"service": "switch.turn_on", "entity_id": "switch.espresso"}
            ],
        }
    ]
    await async_execute_background_actions(hass, steps_both)
    assert hass.services.async_call.call_count == CALL_COUNT_TWO
    hass.services.async_call.assert_any_call(
        "light",
        "turn_on",
        target={"entity_id": "light.kitchen"},
    )
    hass.services.async_call.assert_any_call(
        "switch",
        "turn_on",
        target={"entity_id": "switch.espresso"},
    )


@pytest.mark.asyncio
async def test_slide_actions_fallback_to_steps():
    """Test display actions falling back to steps definition matched by name."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    mock_state = MagicMock()
    mock_state.state = "idle"
    hass.states.get.return_value = mock_state

    # slide_timestamps[0] has no actions and corresponds to visual step 'intro'
    # steps has a preceding non-visual step 'prep' to test name matching over positional index
    metadata = {
        "video_path": "/local/morning.mp4",
        "prepare_player": False,
        "slide_timestamps": [
            {"timestamp": 0.0, "step_name": "intro"},
        ],
    }
    steps = [
        {
            "name": "prep",
            "visual": False,
            "actions": [
                {"action": "switch.turn_on", "target": {"entity_id": "switch.unused"}}
            ],
        },
        {
            "name": "intro",
            "visual": True,
            "actions_on_display": [
                {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}
            ],
        },
    ]

    with patch(
        "custom_components.cast_studio.orchestrator.async_track_state_change_event"
    ) as mock_track:
        await async_play_and_sync_routine(
            hass, "morning", "media_player.display", metadata, steps
        )
        state_cb = mock_track.call_args[0][2]
        event_playing = MagicMock()
        event_playing.data = {"new_state": MagicMock(state="playing")}
        sync_task = state_cb(event_playing)
        if sync_task:
            await sync_task
        assert hass.services.async_call.call_count == CALL_COUNT_TWO
        hass.services.async_call.assert_any_call(
            "light",
            "turn_on",
            target={"entity_id": "light.kitchen"},
        )

    # 2. empty slide_timestamps in metadata, schedule extracted from steps
    metadata_empty_slides = {
        "video_path": "/local/morning.mp4",
        "prepare_player": False,
        "slide_timestamps": [],
    }
    steps_with_actions = [
        {
            "name": "step1",
            "duration": 1.0,
            "actions_on_display": [
                {"action": "switch.turn_on", "target": {"entity_id": "switch.fan"}}
            ],
        }
    ]
    with patch(
        "custom_components.cast_studio.orchestrator.async_track_state_change_event"
    ) as mock_track:
        await async_play_and_sync_routine(
            hass,
            "morning",
            "media_player.display",
            metadata_empty_slides,
            steps_with_actions,
        )
        state_cb = mock_track.call_args[0][2]
        event_playing = MagicMock()
        event_playing.data = {"new_state": MagicMock(state="playing")}
        sync_task = state_cb(event_playing)
        if sync_task:
            await sync_task
        assert hass.services.async_call.call_count == CALL_COUNT_FOUR


@pytest.mark.asyncio
async def test_async_sync_slides_drift_prevention():
    """Test sleep delays anchor to monotonic loop time, avoiding cumulative drift."""
    hass = MagicMock()
    schedule = [
        (0.0, [{"action": "light.turn_on", "target": {"entity_id": "light.one"}}]),
        (2.0, [{"action": "light.turn_on", "target": {"entity_id": "light.two"}}]),
    ]

    loop = asyncio.get_running_loop()
    time_cursor = [100.0]

    def fake_time() -> float:
        return time_cursor[0]

    async def fake_execute(*_args: Any, **_kwargs: Any) -> None:
        # Simulate execution of action consuming 0.5 seconds
        time_cursor[0] += 0.5

    with (
        patch.object(loop, "time", side_effect=fake_time),
        patch(
            "custom_components.cast_studio.orchestrator.async_execute_background_actions",
            side_effect=fake_execute,
        ),
        patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
    ):
        await _async_sync_slides(hass, schedule)
        # Offset 2.0 minus elapsed 0.5 should sleep 1.5 instead of 2.0
        mock_sleep.assert_awaited_once_with(pytest.approx(1.5))

    # Empty schedule returns immediately
    await _async_sync_slides(hass, [])


@pytest.mark.asyncio
async def test_create_task_with_home_assistant_task():
    """Test _create_task when hass.async_create_task returns an actual Task."""
    hass = MagicMock()
    loop = asyncio.get_running_loop()

    async def dummy() -> int:
        return 42

    real_task = loop.create_task(dummy())
    hass.async_create_task = MagicMock(return_value=real_task)

    async def sample_coro() -> int:
        return 1

    coro = sample_coro()
    created = _create_task(hass, coro)
    assert created is real_task
    coro.close()
    await real_task


@pytest.mark.asyncio
async def test_play_and_sync_timeout_after_sync_started():
    """Test timeout callback returns early if sync has already started."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    mock_state = MagicMock()
    mock_state.state = "idle"
    hass.states.get.return_value = mock_state

    metadata = {
        "video_path": "/local/morning.mp4",
        "prepare_player": False,
        "slide_timestamps": [
            {
                "timestamp": 0.0,
                "actions_on_display": [
                    {
                        "action": "light.turn_on",
                        "target": {"entity_id": "light.kitchen"},
                    }
                ],
            }
        ],
    }

    with (
        patch(
            "custom_components.cast_studio.orchestrator.async_track_state_change_event"
        ) as mock_track,
        patch(
            "custom_components.cast_studio.orchestrator.async_call_later"
        ) as mock_call_later,
    ):
        await async_play_and_sync_routine(
            hass, "morning", "media_player.display", metadata, []
        )
        state_cb = mock_track.call_args[0][2]
        timeout_cb = mock_call_later.call_args[0][2]

        # 1. Player enters playing
        event_playing = MagicMock()
        event_playing.data = {"new_state": MagicMock(state="playing")}
        sync_task = state_cb(event_playing)
        if sync_task:
            await sync_task

        # 2. Timeout fires after sync started -> should return early
        timeout_cb(None)
        assert hass.services.async_call.call_count == CALL_COUNT_TWO


@pytest.mark.asyncio
async def test_play_and_sync_returns_lifecycle_task():
    """Test async_play_and_sync_routine returns lifecycle task that completes upon sync."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    mock_state = MagicMock()
    mock_state.state = "idle"
    hass.states.get.return_value = mock_state

    metadata = {
        "video_path": "/local/morning.mp4",
        "prepare_player": False,
        "duration": 0.0,
        "slide_timestamps": [
            {
                "timestamp": 0.0,
                "actions_on_display": [
                    {"action": "light.turn_on", "target": {"entity_id": "light.desk"}}
                ],
            }
        ],
    }

    with (
        patch(
            "custom_components.cast_studio.orchestrator.async_track_state_change_event"
        ) as mock_track,
        patch("custom_components.cast_studio.orchestrator.async_call_later"),
    ):
        playback_task = await async_play_and_sync_routine(
            hass, "morning", "media_player.display", metadata, []
        )
        assert isinstance(playback_task, asyncio.Task)
        assert not playback_task.done()

        # Trigger playing event
        state_cb = mock_track.call_args[0][2]
        event_playing = MagicMock()
        event_playing.data = {"new_state": MagicMock(state="playing")}
        state_cb(event_playing)

        # Await the lifecycle task to verify it completes once sync finishes
        await playback_task
        assert playback_task.done()
        assert hass.services.async_call.call_count == CALL_COUNT_TWO


def test_resolve_cast_media_url_http_and_media_source():
    """Verify HTTP/HTTPS and media-source URLs remain untouched."""
    hass = MagicMock()
    assert (
        resolve_cast_media_url(hass, "http://192.168.1.100/video.mp4")
        == "http://192.168.1.100/video.mp4"
    )
    assert (
        resolve_cast_media_url(hass, "https://example.com/audio.mp3")
        == "https://example.com/audio.mp3"
    )
    assert (
        resolve_cast_media_url(hass, "media-source://media_source/local/test.mp3")
        == "media-source://media_source/local/test.mp3"
    )


def test_resolve_cast_media_url_local_config_www():
    """Verify /config/www/ paths are converted to accessible HTTP URLs using get_url."""
    hass = MagicMock()
    with patch(
        "custom_components.cast_studio.orchestrator.get_url",
        return_value="http://192.168.1.50:8123",
    ):
        result = resolve_cast_media_url(
            hass, "/config/www/cast_studio/morning_briefing.mp4"
        )
        assert result.startswith(
            "http://192.168.1.50:8123/local/cast_studio/morning_briefing.mp4?v="
        )


def test_resolve_cast_media_url_fallback_on_get_url_error():
    """Verify that if get_url raises an exception, relative /local/ path is returned."""
    hass = MagicMock()
    with patch(
        "custom_components.cast_studio.orchestrator.get_url",
        side_effect=Exception("No base URL configured"),
    ):
        result = resolve_cast_media_url(
            hass, "/config/www/cast_studio/morning_briefing.mp4"
        )
        assert result.startswith("/local/cast_studio/morning_briefing.mp4?v=")


def test_resolve_cast_media_url_non_www_path():
    """Verify non-www local filesystem paths are returned unchanged."""
    hass = MagicMock()
    assert (
        resolve_cast_media_url(hass, "/var/media/scratch/clip.mp4")
        == "/var/media/scratch/clip.mp4"
    )


@pytest.mark.asyncio
async def test_play_and_sync_resolves_cast_media_url():
    """Verify async_play_and_sync_routine passes the resolved cast URL to play_media."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()

    with (
        patch(
            "custom_components.cast_studio.orchestrator.get_url",
            return_value="http://homeassistant.local:8123",
        ),
        patch(
            "custom_components.cast_studio.orchestrator.async_track_state_change_event"
        ),
        patch("custom_components.cast_studio.orchestrator.async_call_later"),
    ):
        metadata = {
            "video_path": "/config/www/cast_studio/morning.mp4",
            "slide_timestamps": [],
        }
        await async_play_and_sync_routine(
            hass, "morning", "media_player.kitchen_cast", metadata, []
        )
        play_call = hass.services.async_call.await_args
        assert play_call[0][:2] == ("media_player", "play_media")
        assert play_call[0][2]["entity_id"] == "media_player.kitchen_cast"
        assert play_call[0][2]["media_content_id"].startswith(
            "http://homeassistant.local:8123/local/cast_studio/morning.mp4?v="
        )
        assert play_call[0][2]["media_content_type"] == "video/mp4"


def test_resolve_cast_media_url_local_prefix():
    """Test resolve_cast_media_url converts /local/ path to absolute HTTP URL."""
    hass = MagicMock()
    with patch(
        "custom_components.cast_studio.orchestrator.get_url",
        return_value="http://192.168.1.50:8123",
    ):
        resolved = resolve_cast_media_url(hass, "/local/cast_studio/static/freezer.mp4")
        assert resolved.startswith(
            "http://192.168.1.50:8123/local/cast_studio/static/freezer.mp4?v="
        )


def test_resolve_cast_media_url_appends_cache_buster():
    """Verify resolve_cast_media_url appends timestamp cache buster query parameter."""
    hass = MagicMock()
    with patch(
        "custom_components.cast_studio.orchestrator.get_url",
        return_value="http://homeassistant.local:8123",
    ):
        url = resolve_cast_media_url(hass, "/config/www/automations/morning.mp4")
        assert "http://homeassistant.local:8123/local/automations/morning.mp4?v=" in url


@pytest.mark.asyncio
async def test_async_play_and_sync_routine_prepares_player():
    """Verify async_play_and_sync_routine wakes up, unmutes, and initializes volume before play_media."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()

    with (
        patch(
            "custom_components.cast_studio.orchestrator.async_track_state_change_event"
        ),
        patch("custom_components.cast_studio.orchestrator.async_call_later"),
    ):
        metadata = {
            "video_path": "/local/morning.mp4",
            "volume_level": 0.75,
            "prepare_player": True,
        }
        await async_play_and_sync_routine(
            hass, "morning", "media_player.bathroom_display", metadata, []
        )

        hass.services.async_call.assert_any_call(
            "media_player", "turn_on", {"entity_id": "media_player.bathroom_display"}
        )
        hass.services.async_call.assert_any_call(
            "media_player",
            "volume_mute",
            {"entity_id": "media_player.bathroom_display", "is_volume_muted": False},
        )
        hass.services.async_call.assert_any_call(
            "media_player",
            "volume_set",
            {"entity_id": "media_player.bathroom_display", "volume_level": 0.75},
        )


@pytest.mark.asyncio
async def test_async_play_and_sync_routine_prepares_player_default_volume_and_disabled():
    """Verify default volume 0.60 is used and prepare_player=False disables player preparation."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()

    with (
        patch(
            "custom_components.cast_studio.orchestrator.async_track_state_change_event"
        ),
        patch("custom_components.cast_studio.orchestrator.async_call_later"),
    ):
        # 1. Default volume level (0.60) when volume_level not specified
        metadata_default = {"video_path": "/local/morning.mp4"}
        await async_play_and_sync_routine(
            hass, "morning", "media_player.display", metadata_default, []
        )
        hass.services.async_call.assert_any_call(
            "media_player",
            "volume_set",
            {"entity_id": "media_player.display", "volume_level": 0.60},
        )

        # 2. Disabled prepare_player
        hass.services.async_call.reset_mock()
        metadata_disabled = {
            "video_path": "/local/morning.mp4",
            "prepare_player": False,
        }
        await async_play_and_sync_routine(
            hass, "morning", "media_player.display", metadata_disabled, []
        )
        # Only play_media called, no turn_on / volume calls
        assert hass.services.async_call.call_count == 1
        assert hass.services.async_call.call_args[0][:2] == (
            "media_player",
            "play_media",
        )


@pytest.mark.asyncio
async def test_async_play_and_sync_routine_prepares_player_exception_suppressed():
    """Verify exceptions during player preparation do not prevent playback."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock(
        side_effect=[Exception("Device unavailable"), None, None, None]
    )

    with (
        patch(
            "custom_components.cast_studio.orchestrator.async_track_state_change_event"
        ),
        patch("custom_components.cast_studio.orchestrator.async_call_later"),
    ):
        metadata = {"video_path": "/local/morning.mp4"}
        await async_play_and_sync_routine(
            hass, "morning", "media_player.display", metadata, []
        )
        # Even though turn_on raised an exception, play_media was still attempted
        play_media_calls = [
            c
            for c in hass.services.async_call.call_args_list
            if c[0][:2] == ("media_player", "play_media")
        ]
        assert len(play_media_calls) == 1


def test_resolve_cast_media_url_preexisting_query_param():
    """Verify resolve_cast_media_url appends &v= when url already has query parameters."""
    hass = MagicMock()
    with patch(
        "custom_components.cast_studio.orchestrator.get_url",
        return_value="http://192.168.1.50:8123",
    ):
        resolved = resolve_cast_media_url(
            hass, "/local/cast_studio/video.mp4?autoplay=1"
        )
        assert resolved.startswith(
            "http://192.168.1.50:8123/local/cast_studio/video.mp4?autoplay=1&v="
        )


@pytest.mark.asyncio
async def test_async_play_and_sync_routine_executes_post_actions():
    """Verify post_routine_actions execute upon playback completion."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    mock_state = MagicMock()
    mock_state.state = "idle"
    hass.states.get.return_value = mock_state

    post_actions = [
        {
            "service": "cast.show_lovelace_view",
            "target": {"entity_id": "media_player.bathroom_display"},
            "data": {
                "dashboard_path": "nest-display",
                "view_path": "morning",
            },
        }
    ]
    metadata = {
        "video_path": "/local/morning.mp4",
        "prepare_player": False,
        "duration": 0.05,
        "slide_timestamps": [],
        "post_routine_actions": post_actions,
    }

    with (
        patch(
            "custom_components.cast_studio.orchestrator.async_track_state_change_event"
        ) as mock_track,
        patch("custom_components.cast_studio.orchestrator.async_call_later"),
    ):
        playback_task = await async_play_and_sync_routine(
            hass, "morning", "media_player.bathroom_display", metadata, []
        )
        assert isinstance(playback_task, asyncio.Task)

        # Trigger playing event
        state_cb = mock_track.call_args[0][2]
        event_playing = MagicMock()
        event_playing.data = {"new_state": MagicMock(state="playing")}
        state_cb(event_playing)

        # Await the playback lifecycle to complete
        await playback_task

        # Verify that cast.show_lovelace_view was called
        hass.services.async_call.assert_any_call(
            "cast",
            "show_lovelace_view",
            service_data={"dashboard_path": "nest-display", "view_path": "morning"},
            target={"entity_id": "media_player.bathroom_display"},
        )


@pytest.mark.asyncio
async def test_async_play_and_sync_routine_executes_post_actions_fallback():
    """Verify post_actions fallback key and single dict format execute upon playback completion."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    mock_state = MagicMock()
    mock_state.state = "idle"
    hass.states.get.return_value = mock_state

    # Test single dictionary provided via 'post_actions'
    post_action_dict = {
        "service": "cast.show_lovelace_view",
        "target": {"entity_id": "media_player.kitchen_hub"},
        "data": {
            "dashboard_path": "kitchen-dashboard",
            "view_path": "overview",
        },
    }
    metadata = {
        "video_path": "/local/morning.mp4",
        "prepare_player": False,
        "duration": 0.02,
        "slide_timestamps": [],
        "post_actions": post_action_dict,
    }

    with (
        patch(
            "custom_components.cast_studio.orchestrator.async_track_state_change_event"
        ) as mock_track,
        patch("custom_components.cast_studio.orchestrator.async_call_later"),
    ):
        playback_task = await async_play_and_sync_routine(
            hass, "morning", "media_player.kitchen_hub", metadata, []
        )
        assert isinstance(playback_task, asyncio.Task)

        # Trigger playing event
        state_cb = mock_track.call_args[0][2]
        event_playing = MagicMock()
        event_playing.data = {"new_state": MagicMock(state="playing")}
        state_cb(event_playing)

        await playback_task

        hass.services.async_call.assert_any_call(
            "cast",
            "show_lovelace_view",
            service_data={
                "dashboard_path": "kitchen-dashboard",
                "view_path": "overview",
            },
            target={"entity_id": "media_player.kitchen_hub"},
        )


@pytest.mark.asyncio
async def test_async_play_and_sync_routine_executes_post_actions_on_timeout():
    """Verify post_routine_actions execute even if safety timeout expires."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    mock_state = MagicMock()
    mock_state.state = "idle"
    hass.states.get.return_value = mock_state

    post_actions = [
        {
            "service": "cast.show_lovelace_view",
            "target": {"entity_id": "media_player.bathroom_display"},
            "data": {
                "dashboard_path": "nest-display",
                "view_path": "morning",
            },
        }
    ]
    metadata = {
        "video_path": "/local/morning.mp4",
        "prepare_player": False,
        "duration": 5.0,
        "slide_timestamps": [],
        "post_routine_actions": post_actions,
    }

    with (
        patch(
            "custom_components.cast_studio.orchestrator.async_track_state_change_event"
        ),
        patch(
            "custom_components.cast_studio.orchestrator.async_call_later"
        ) as mock_call_later,
        patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
    ):
        playback_task = await async_play_and_sync_routine(
            hass, "morning", "media_player.bathroom_display", metadata, []
        )
        assert isinstance(playback_task, asyncio.Task)

        # Trigger timeout callback directly instead of entering playing
        timeout_cb = mock_call_later.call_args[0][2]
        timeout_cb(None)

        # Await the playback lifecycle to complete
        await playback_task

        # Verify fallback sleep was awaited for the remaining duration
        mock_sleep.assert_awaited_with(pytest.approx(5.0, rel=1e-2))

        # Verify that cast.show_lovelace_view was called after timeout and duration sleep
        hass.services.async_call.assert_any_call(
            "cast",
            "show_lovelace_view",
            service_data={"dashboard_path": "nest-display", "view_path": "morning"},
            target={"entity_id": "media_player.bathroom_display"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("state_at_timeout", ["unavailable", "off", None])
async def test_play_and_sync_timeout_with_player_gone_fails_without_waiting(
    state_at_timeout: str | None,
) -> None:
    """A player that is off or gone at the timeout never played: fail at once."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.states.get.return_value = MagicMock(state="idle")
    metadata = {
        "video_path": "/local/morning.mp4",
        "prepare_player": False,
        "duration": 5.0,
        "slide_timestamps": [],
    }

    with (
        patch(
            "custom_components.cast_studio.orchestrator.async_track_state_change_event"
        ),
        patch(
            "custom_components.cast_studio.orchestrator.async_call_later"
        ) as mock_call_later,
        patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
    ):
        playback_task = await async_play_and_sync_routine(
            hass, "morning", "media_player.bathroom_display", metadata, []
        )
        hass.states.get.return_value = (
            None if state_at_timeout is None else MagicMock(state=state_at_timeout)
        )
        mock_call_later.call_args[0][2](None)

        with pytest.raises(PlaybackNotStartedError):
            await playback_task
        mock_sleep.assert_not_awaited()


def test_is_routine_media_playing_default_media_receiver_proxy_stream() -> None:
    """Test _is_routine_media_playing recognizes Cast Default Media Receiver playing even with proxy URL."""
    from custom_components.cast_studio.orchestrator import _is_routine_media_playing

    state_obj = MagicMock()
    state_obj.state = "playing"
    state_obj.attributes = {
        "app_id": "CC1AD845",
        "app_name": "Default Media Receiver",
        "media_content_id": "http://192.168.1.50:8123/api/media_player_proxy/123456",
    }
    # Even though /api/media_player_proxy does not match morning.mp4, CC1AD845 playing is recognized
    assert (
        _is_routine_media_playing(
            state_obj, expected_media_url="http://192.168.1.50:8123/local/morning.mp4"
        )
        is True
    )


def test_is_routine_media_playing_rejects_ha_cast_splash_screen() -> None:
    """Test _is_routine_media_playing rejects Home Assistant Cast splash screen."""
    from custom_components.cast_studio.orchestrator import _is_routine_media_playing

    state_obj = MagicMock()
    state_obj.state = "playing"
    state_obj.attributes = {
        "app_id": "CC1AD845",
        "app_name": "Default Media Receiver",
        "media_content_id": "https://www.home-assistant.io/images/cast/splash.png",
    }
    assert (
        _is_routine_media_playing(
            state_obj, expected_media_url="https://ha.apollo-home.net/local/morning.mp4"
        )
        is False
    )


@pytest.mark.asyncio
async def test_coordinate_playback_lifecycle_wakes_player_for_post_actions() -> None:
    """Verify player turn_on is called to ensure display is awake before post-routine actions."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    mock_state = MagicMock()
    mock_state.state = "idle"
    hass.states.get.return_value = mock_state

    metadata = {
        "video_path": "/local/morning.mp4",
        "prepare_player": True,
        "duration": 0.01,
        "slide_timestamps": [],
        "post_routine_actions": [
            {
                "service": "cast.show_lovelace_view",
                "target": {"entity_id": "media_player.bathroom_display"},
                "data": {"dashboard_path": "nest-display", "view_path": "morning"},
            }
        ],
    }

    with (
        patch(
            "custom_components.cast_studio.orchestrator.async_track_state_change_event"
        ) as mock_track,
        patch("custom_components.cast_studio.orchestrator.async_call_later"),
    ):
        playback_task = await async_play_and_sync_routine(
            hass, "morning", "media_player.bathroom_display", metadata, []
        )
        state_cb = mock_track.call_args[0][2]
        event_playing = MagicMock()
        event_playing.data = {"new_state": MagicMock(state="playing")}
        state_cb(event_playing)

        await playback_task

        # Verify media_player.turn_on was called for target_player
        hass.services.async_call.assert_any_call(
            "media_player", "turn_on", {"entity_id": "media_player.bathroom_display"}
        )


def _cast_player(
    hass: HomeAssistant,
    object_id: str,
    *,
    model: str | None,
    device_name: str = "Device",
    platform: str = "cast",
) -> str:
    """Register a media player entity on a device with ``model``."""
    config_entry = MockConfigEntry(domain=platform)
    config_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(platform, object_id)},
        name=device_name,
        model=model,
    )
    entity = er.async_get(hass).async_get_or_create(
        "media_player",
        platform,
        object_id,
        config_entry=config_entry,
        device_id=device.id,
        suggested_object_id=object_id,
    )
    return entity.entity_id


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("Google Nest Hub", True),
        ("Google Nest Hub Max", True),
        ("Chromecast", True),
        ("Google TV Streamer", True),
        ("Chromecast Audio", False),
        ("Google Nest Mini", False),
        ("Google Home Max", False),
        ("Google Cast Group", False),
        ("SRS-XB501G", False),
    ],
)
async def test_has_screen_by_model(
    hass: HomeAssistant, model: str, *, expected: bool
) -> None:
    entity_id = _cast_player(hass, "office_speaker", model=model)
    assert has_screen(hass, entity_id) is expected


async def test_has_screen_group_named_display_is_not_a_screen(
    hass: HomeAssistant,
) -> None:
    entity_id = _cast_player(
        hass, "display_group", model="Google Cast Group", device_name="Display Group"
    )
    assert has_screen(hass, entity_id) is False


async def test_has_screen_name_fallback_without_model(hass: HomeAssistant) -> None:
    assert has_screen(hass, _cast_player(hass, "hall_display", model=None)) is True
    assert has_screen(hass, _cast_player(hass, "hall_speaker", model=None)) is False


async def test_has_screen_unregistered_entity_uses_name(hass: HomeAssistant) -> None:
    for entity_id in ("media_player.office_display", "media_player.kitchen_speaker"):
        hass.states.async_set(entity_id, "idle", {"app_name": "Default Media Receiver"})
    assert has_screen(hass, "media_player.office_display") is True
    assert has_screen(hass, "media_player.kitchen_speaker") is False


async def test_has_screen_non_cast_player_is_never_a_screen(
    hass: HomeAssistant,
) -> None:
    entity_id = _cast_player(
        hass, "den_display", model="Google Nest Hub", platform="squeezebox"
    )
    assert has_screen(hass, entity_id) is False
    assert has_screen(hass, None) is False
    assert has_screen(hass, "") is False


async def test_lifecycle_waits_extra_seconds_added_during_playback() -> None:
    """Time added while playing (an announcement pause) delays the end."""
    from custom_components.cast_studio.orchestrator import PlayControl

    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.states.get.return_value = MagicMock(state="idle")
    metadata: dict = {
        "video_path": "/local/morning.mp4",
        "prepare_player": False,
        "duration": 0.1,
    }
    loop = asyncio.get_running_loop()
    with (
        patch(
            "custom_components.cast_studio.orchestrator.async_track_state_change_event"
        ) as mock_track,
        patch("custom_components.cast_studio.orchestrator.async_call_later"),
    ):
        control = PlayControl()
        task = await async_play_and_sync_routine(
            hass,
            "morning",
            "media_player.office_display",
            metadata,
            [],
            control=control,
        )
        event = MagicMock()
        event.data = {"new_state": MagicMock(state="playing")}
        mock_track.call_args[0][2](event)
        started = loop.time()
        await asyncio.sleep(0.03)
        assert control.started.done()
        control.extra_seconds = 0.2
        await task
    assert loop.time() - started >= 0.28
