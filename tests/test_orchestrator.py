"""Tests for the routine orchestrator (action partitioning and playback sync)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.automation_generator.orchestrator import (
    OrchestratorError,
    _async_sync_slides,
    _create_task,
    async_execute_background_actions,
    async_play_and_sync_routine,
    get_media_payload,
    get_routine_sync_task,
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
        "custom_components.automation_generator.orchestrator.async_track_state_change_event"
    ) as mock_track:
        metadata = {"video_path": "/local/morning.mp4", "slide_timestamps": []}
        await async_play_and_sync_routine(
            hass, "morning", "media_player.display", metadata, []
        )
        hass.services.async_call.assert_awaited_with(
            "media_player",
            "play_media",
            {
                "entity_id": "media_player.display",
                "media_content_id": "/local/morning.mp4",
                "media_content_type": "video/mp4",
            },
        )
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
            "custom_components.automation_generator.orchestrator.async_track_state_change_event"
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
        "custom_components.automation_generator.orchestrator.async_track_state_change_event"
    ):
        metadata_mp3 = {
            "video_path": None,
            "audio_path": "/local/morning.mp3",
            "slide_timestamps": [],
        }
        await async_play_and_sync_routine(
            hass, "morning", "media_player.speaker", metadata_mp3, []
        )
        hass.services.async_call.assert_awaited_with(
            "media_player",
            "play_media",
            {
                "entity_id": "media_player.speaker",
                "media_content_id": "/local/morning.mp3",
                "media_content_type": "audio/mp3",
            },
        )

        metadata_wav = {
            "video_path": None,
            "audio_path": "/local/morning.wav",
            "slide_timestamps": [],
        }
        await async_play_and_sync_routine(
            hass, "morning", "media_player.speaker", metadata_wav, []
        )
        hass.services.async_call.assert_awaited_with(
            "media_player",
            "play_media",
            {
                "entity_id": "media_player.speaker",
                "media_content_id": "/local/morning.wav",
                "media_content_type": "audio/wav",
            },
        )


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
        "custom_components.automation_generator.orchestrator.async_track_state_change_event"
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
            "custom_components.automation_generator.orchestrator.async_track_state_change_event"
        ) as mock_track,
        patch(
            "custom_components.automation_generator.orchestrator.async_call_later"
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
        "custom_components.automation_generator.orchestrator.async_track_state_change_event"
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
        "custom_components.automation_generator.orchestrator.async_track_state_change_event"
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
        "custom_components.automation_generator.orchestrator.async_track_state_change_event"
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
            "custom_components.automation_generator.orchestrator.async_execute_background_actions",
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
            "custom_components.automation_generator.orchestrator.async_track_state_change_event"
        ) as mock_track,
        patch(
            "custom_components.automation_generator.orchestrator.async_call_later"
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
