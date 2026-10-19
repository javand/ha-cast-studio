"""Tests for CastStudio service handlers, sensor entities, and setup."""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import ServiceCall

from custom_components.cast_studio import (
    _is_media_ready_and_fresh,
    async_compile_clip,
    async_pregenerate_routine,
    async_reload,
    async_run_routine,
    async_setup,
)
from custom_components.cast_studio.const import DOMAIN
from custom_components.cast_studio.sensor import (
    CastStudioRoutineSensor,
    async_get_or_create_sensor,
    async_update_routine_sensor,
)

if TYPE_CHECKING:
    from pathlib import Path

DURATION_4_0 = 4.0
DURATION_5_0 = 5.0
DURATION_15_0 = 15.0
DURATION_18_5 = 18.5
DURATION_20_0 = 20.0
DURATION_25_0 = 25.0
SLIDE_COUNT_ONE = 1
SLIDE_COUNT_THREE = 3
STALE_SECONDS = 90000


@pytest.fixture
def mock_hass() -> MagicMock:
    """Fixture providing a mock HomeAssistant instance."""
    hass = MagicMock()
    hass.data = {}
    hass.services.async_register = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.states.async_set = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    hass.config_entries.async_entries = MagicMock(return_value=[])
    hass.async_create_task = MagicMock(
        side_effect=lambda coro, **_kwargs: asyncio.create_task(coro)
    )
    return hass


@pytest.fixture
def sample_config(tmp_path: Path) -> dict[str, Any]:
    """Fixture providing sample YAML configuration."""
    return {
        DOMAIN: {
            "recipient": {"name": "Javan", "phonetic_name": "Jay-ven"},
            "routines": {
                "morning": {
                    "title": "Morning Briefing",
                    "visuals": True,
                    "target_player": "media_player.living_room_display",
                    "output_dir": str(tmp_path),
                    "background_actions": [
                        {
                            "action": "switch.turn_on",
                            "target": {"entity_id": "switch.espresso"},
                        }
                    ],
                    "steps": [
                        {
                            "name": "greeting",
                            "visual": True,
                            "voice": "Pulcherrima",
                            "actions_on_display": [
                                {
                                    "action": "light.turn_on",
                                    "target": {"entity_id": "light.kitchen"},
                                }
                            ],
                        },
                        {
                            "name": "prep",
                            "visual": False,
                            "actions": [
                                {
                                    "action": "climate.set_temperature",
                                    "data": {"temperature": 70},
                                }
                            ],
                        },
                    ],
                }
            },
        }
    }


@pytest.mark.asyncio
async def test_services_registered(
    mock_hass: MagicMock, sample_config: dict[str, Any]
) -> None:
    """Test that async_setup registers all 4 domain services and initial sensors."""
    assert await async_setup(mock_hass, sample_config) is True

    # Check services registered
    registered_services = [
        call.args[1] for call in mock_hass.services.async_register.call_args_list
    ]
    assert "pregenerate_routine" in registered_services
    assert "run_routine" in registered_services
    assert "compile_clip" in registered_services
    assert "reload" in registered_services

    # Check routine sensor created
    sensors = mock_hass.data[DOMAIN]["sensors"]
    assert "morning" in sensors
    sensor = sensors["morning"]
    assert isinstance(sensor, CastStudioRoutineSensor)
    assert sensor.entity_id == "sensor.cast_studio_morning"
    assert sensor.native_value == "idle"


test_setup_registers_all_services = test_services_registered


@pytest.mark.asyncio
async def test_pregenerate_routine_service_success(
    mock_hass: MagicMock, sample_config: dict[str, Any], tmp_path: Path
) -> None:
    """Test pregenerate_routine service call triggers compilation and updates sensor."""
    await async_setup(mock_hass, sample_config)

    calls = mock_hass.services.async_register.call_args_list
    pregen_handler = next(
        c.args[2] for c in calls if c.args[1] == "pregenerate_routine"
    )

    mock_metadata = {
        "video_path": str(tmp_path / "morning.mp4"),
        "audio_path": None,
        "duration": DURATION_18_5,
        "slide_timestamps": [{"timestamp": 0.0}],
        "last_pregenerated": "2026-10-17T04:30:00Z",
    }

    with patch(
        "custom_components.cast_studio.async_compile_routine_media",
        new=AsyncMock(return_value=mock_metadata),
    ) as mock_compile:
        call = ServiceCall(
            mock_hass, DOMAIN, "pregenerate_routine", {"routine_name": "morning"}
        )
        await pregen_handler(call)

        mock_compile.assert_awaited_once()
        sensor = mock_hass.data[DOMAIN]["sensors"]["morning"]
        assert sensor.native_value == "ready"
        assert sensor.extra_state_attributes["duration"] == DURATION_18_5
        assert sensor.extra_state_attributes["video_path"] == str(
            tmp_path / "morning.mp4"
        )
        assert sensor.extra_state_attributes["slide_count"] == SLIDE_COUNT_ONE
        assert sensor.extra_state_attributes["active_task"] is None


@pytest.mark.asyncio
async def test_pregenerate_routine_service_failure(
    mock_hass: MagicMock, sample_config: dict[str, Any]
) -> None:
    """Test pregenerate_routine service sets error state when compilation fails."""
    await async_setup(mock_hass, sample_config)

    calls = mock_hass.services.async_register.call_args_list
    pregen_handler = next(
        c.args[2] for c in calls if c.args[1] == "pregenerate_routine"
    )

    with patch(
        "custom_components.cast_studio.async_compile_routine_media",
        new=AsyncMock(side_effect=RuntimeError("FFmpeg error")),
    ):
        call = ServiceCall(
            mock_hass, DOMAIN, "pregenerate_routine", {"routine_name": "morning"}
        )
        with pytest.raises(RuntimeError, match="FFmpeg error"):
            await pregen_handler(call)

        sensor = mock_hass.data[DOMAIN]["sensors"]["morning"]
        assert sensor.native_value == "error"
        assert sensor.extra_state_attributes["active_task"] is None


@pytest.mark.asyncio
async def test_run_routine_with_preexisting_media(
    mock_hass: MagicMock, sample_config: dict[str, Any], tmp_path: Path
) -> None:
    """Test run_routine uses pre-existing fresh media without recompiling."""
    await async_setup(mock_hass, sample_config)

    calls = mock_hass.services.async_register.call_args_list
    run_handler = next(c.args[2] for c in calls if c.args[1] == "run_routine")

    video_file = tmp_path / "morning.mp4"
    video_file.write_bytes(b"dummy_video_data")
    meta_file = tmp_path / "morning.metadata.json"
    metadata_content = {
        "video_path": str(video_file),
        "audio_path": None,
        "duration": DURATION_15_0,
        "slide_timestamps": [{"timestamp": 0.0}],
    }
    meta_file.write_text(json.dumps(metadata_content), encoding="utf-8")

    dummy_play_task = asyncio.create_task(asyncio.sleep(0.02))

    with (
        patch(
            "custom_components.cast_studio.async_compile_routine_media",
            new=AsyncMock(),
        ) as mock_compile,
        patch(
            "custom_components.cast_studio.async_execute_background_actions",
            new=AsyncMock(),
        ) as mock_bg,
        patch(
            "custom_components.cast_studio.async_play_and_sync_routine",
            new=AsyncMock(return_value=dummy_play_task),
        ) as mock_play,
    ):
        call = ServiceCall(
            mock_hass, DOMAIN, "run_routine", {"routine_name": "morning"}
        )
        await run_handler(call)

        # Compilation should NOT be triggered
        mock_compile.assert_not_called()

        # Background actions and playback should be dispatched
        mock_bg.assert_called_once()
        mock_play.assert_awaited_once()

        sensor = mock_hass.data[DOMAIN]["sensors"]["morning"]
        assert sensor.native_value == "playing"

        await dummy_play_task
        await asyncio.sleep(0.01)
        assert sensor.native_value == "ready"


@pytest.mark.asyncio
async def test_run_routine_on_demand_fallback(
    mock_hass: MagicMock, sample_config: dict[str, Any], tmp_path: Path
) -> None:
    """Test run_routine compiles media on-demand when pregenerated file is missing."""
    await async_setup(mock_hass, sample_config)

    calls = mock_hass.services.async_register.call_args_list
    run_handler = next(c.args[2] for c in calls if c.args[1] == "run_routine")

    mock_metadata = {
        "video_path": str(tmp_path / "morning.mp4"),
        "audio_path": None,
        "duration": DURATION_20_0,
        "slide_timestamps": [],
    }

    dummy_play_task = asyncio.create_task(asyncio.sleep(0.02))

    with (
        patch(
            "custom_components.cast_studio.async_compile_routine_media",
            new=AsyncMock(return_value=mock_metadata),
        ) as mock_compile,
        patch(
            "custom_components.cast_studio.async_execute_background_actions",
            new=AsyncMock(),
        ) as mock_bg,
        patch(
            "custom_components.cast_studio.async_play_and_sync_routine",
            new=AsyncMock(return_value=dummy_play_task),
        ) as mock_play,
    ):
        call = ServiceCall(
            mock_hass, DOMAIN, "run_routine", {"routine_name": "morning"}
        )
        await run_handler(call)

        mock_compile.assert_awaited_once()
        mock_bg.assert_called_once()
        mock_play.assert_awaited_once()

        sensor = mock_hass.data[DOMAIN]["sensors"]["morning"]
        assert sensor.native_value == "playing"

        await dummy_play_task
        await asyncio.sleep(0.01)
        assert sensor.native_value == "ready"


@pytest.mark.asyncio
async def test_run_routine_stale_media_recompiles(
    mock_hass: MagicMock, sample_config: dict[str, Any], tmp_path: Path
) -> None:
    """Test run_routine recompiles when existing media is older than 24 hours."""
    await async_setup(mock_hass, sample_config)

    calls = mock_hass.services.async_register.call_args_list
    run_handler = next(c.args[2] for c in calls if c.args[1] == "run_routine")

    video_file = tmp_path / "morning.mp4"
    video_file.write_bytes(b"stale_video")
    meta_file = tmp_path / "morning.metadata.json"
    meta_file.write_text(json.dumps({"video_path": str(video_file)}), encoding="utf-8")

    stale_time = time.time() - STALE_SECONDS
    os.utime(meta_file, (stale_time, stale_time))
    os.utime(video_file, (stale_time, stale_time))

    mock_metadata = {
        "video_path": str(video_file),
        "duration": DURATION_15_0,
        "slide_timestamps": [],
    }
    dummy_play_task = asyncio.create_task(asyncio.sleep(0.01))

    with (
        patch(
            "custom_components.cast_studio.async_compile_routine_media",
            new=AsyncMock(return_value=mock_metadata),
        ) as mock_compile,
        patch(
            "custom_components.cast_studio.async_execute_background_actions",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.cast_studio.async_play_and_sync_routine",
            new=AsyncMock(return_value=dummy_play_task),
        ),
    ):
        call = ServiceCall(
            mock_hass, DOMAIN, "run_routine", {"routine_name": "morning"}
        )
        await run_handler(call)

        mock_compile.assert_awaited_once()


@pytest.mark.asyncio
async def test_programmatic_api_calls(
    mock_hass: MagicMock, sample_config: dict[str, Any], tmp_path: Path
) -> None:
    """Test calling async_pregenerate_routine and async_run_routine directly."""
    await async_setup(mock_hass, sample_config)

    mock_metadata = {
        "video_path": str(tmp_path / "morning.mp4"),
        "audio_path": None,
        "duration": DURATION_18_5,
        "slide_timestamps": [],
    }

    with patch(
        "custom_components.cast_studio.async_compile_routine_media",
        new=AsyncMock(return_value=mock_metadata),
    ):
        meta = await async_pregenerate_routine(mock_hass, "morning")
        assert meta["duration"] == DURATION_18_5

        with (
            patch(
                "custom_components.cast_studio.async_execute_background_actions",
                new=AsyncMock(),
            ),
            patch(
                "custom_components.cast_studio.async_play_and_sync_routine",
                new=AsyncMock(),
            ),
        ):
            run_meta = await async_run_routine(mock_hass, "morning")
            assert run_meta["duration"] == DURATION_18_5


@pytest.mark.asyncio
async def test_service_argument_validation(
    mock_hass: MagicMock, sample_config: dict[str, Any]
) -> None:
    """Test validation errors on missing or empty routine_name."""
    await async_setup(mock_hass, sample_config)

    calls = mock_hass.services.async_register.call_args_list
    pregen_handler = next(
        c.args[2] for c in calls if c.args[1] == "pregenerate_routine"
    )
    run_handler = next(c.args[2] for c in calls if c.args[1] == "run_routine")

    # Missing routine_name in pregenerate service
    with pytest.raises(ValueError, match="Missing or empty required service parameter"):
        await pregen_handler(ServiceCall(mock_hass, DOMAIN, "pregenerate_routine", {}))

    # Empty routine_name in run service
    with pytest.raises(ValueError, match="Missing or empty required service parameter"):
        await run_handler(
            ServiceCall(mock_hass, DOMAIN, "run_routine", {"routine_name": "  "})
        )

    # Missing in Python API calls
    with pytest.raises(ValueError, match="Missing or empty required parameter"):
        await async_pregenerate_routine(mock_hass, "")

    with pytest.raises(ValueError, match="Missing or empty required parameter"):
        await async_run_routine(mock_hass, "")


@pytest.mark.asyncio
async def test_sensor_playback_lifecycle_holding_state(
    mock_hass: MagicMock, sample_config: dict[str, Any]
) -> None:
    """Test that sensor holds 'playing' state while playback task is pending."""
    await async_setup(mock_hass, sample_config)
    calls = mock_hass.services.async_register.call_args_list
    run_handler = next(c.args[2] for c in calls if c.args[1] == "run_routine")

    loop = asyncio.get_running_loop()
    playback_event = asyncio.Event()

    async def controlled_playback() -> None:
        await playback_event.wait()

    playback_task = loop.create_task(controlled_playback())

    with (
        patch(
            "custom_components.cast_studio.async_compile_routine_media",
            new=AsyncMock(return_value={"video_path": "/fake.mp4", "duration": 10.0}),
        ),
        patch(
            "custom_components.cast_studio.async_execute_background_actions",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.cast_studio.async_play_and_sync_routine",
            new=AsyncMock(return_value=playback_task),
        ),
    ):
        call = ServiceCall(
            mock_hass, DOMAIN, "run_routine", {"routine_name": "morning"}
        )
        await run_handler(call)

        sensor = mock_hass.data[DOMAIN]["sensors"]["morning"]
        # Sensor MUST stay in playing state while playback is ongoing
        assert sensor.native_value == "playing"

        # Allow loop turns to ensure it doesn't prematurely drop to ready
        await asyncio.sleep(0.01)
        assert sensor.native_value == "playing"

        # Signal completion
        playback_event.set()
        await playback_task
        await asyncio.sleep(0.01)

        # Now sensor transitions to ready
        assert sensor.native_value == "ready"


def test_sensor_entity_state_transitions(mock_hass: MagicMock) -> None:
    """Test CastStudioRoutineSensor states, attributes, and options."""
    sensor = CastStudioRoutineSensor("evening", hass=mock_hass)
    assert sensor.entity_id == "sensor.cast_studio_evening"
    assert sensor.native_value == "idle"
    assert sensor.device_class == SensorDeviceClass.ENUM
    assert sensor.options == ["idle", "generating", "ready", "playing", "error"]
    assert sensor.name == "Evening"
    assert sensor.extra_state_attributes["active_task"] is None
    assert sensor.extra_state_attributes["duration"] is None

    # Transition to generating
    sensor.update_state("generating", active_task="pregenerate_routine")
    assert sensor.native_value == "generating"
    assert sensor.extra_state_attributes["active_task"] == "pregenerate_routine"

    # Transition to ready
    sensor.update_state(
        "ready",
        duration=DURATION_25_0,
        last_pregenerated="2026-10-17T05:00:00Z",
        video_path="/config/www/cast_studio/evening.mp4",
        slide_count=SLIDE_COUNT_THREE,
        active_task=None,
    )
    assert sensor.native_value == "ready"
    assert sensor.extra_state_attributes["duration"] == DURATION_25_0
    assert sensor.extra_state_attributes["last_pregenerated"] == "2026-10-17T05:00:00Z"
    assert (
        sensor.extra_state_attributes["video_path"]
        == "/config/www/cast_studio/evening.mp4"
    )
    assert sensor.extra_state_attributes["slide_count"] == SLIDE_COUNT_THREE
    assert sensor.extra_state_attributes["active_task"] is None

    # Transition to playing
    sensor.update_state("playing", active_task="run_routine")
    assert sensor.native_value == "playing"
    assert sensor.extra_state_attributes["active_task"] == "run_routine"

    # Transition back to ready
    sensor.update_state("ready", active_task=None)
    assert sensor.native_value == "ready"
    assert sensor.extra_state_attributes["active_task"] is None


def test_sensor_registry_helpers(mock_hass: MagicMock) -> None:
    """Test async_get_or_create_sensor and async_update_routine_sensor helpers."""
    mock_hass.data = {}
    sensor = async_get_or_create_sensor(mock_hass, "night")
    assert sensor.routine_name == "night"
    assert sensor.native_value == "idle"

    updated = async_update_routine_sensor(
        mock_hass, "night", "ready", duration=DURATION_15_0
    )
    assert updated is sensor
    assert updated.native_value == "ready"
    assert updated.extra_state_attributes["duration"] == DURATION_15_0


@pytest.mark.asyncio
async def test_reload_service(
    mock_hass: MagicMock, sample_config: dict[str, Any]
) -> None:
    """Test reload service resets routine sensors to idle and reloads config entries."""
    await async_setup(mock_hass, sample_config)

    sensor = mock_hass.data[DOMAIN]["sensors"]["morning"]
    sensor.update_state("playing", active_task="run_routine")
    assert sensor.native_value == "playing"

    calls = mock_hass.services.async_register.call_args_list
    reload_handler = next(c.args[2] for c in calls if c.args[1] == "reload")

    mock_entry = MagicMock(entry_id="test_entry_id")
    mock_hass.config_entries.async_entries.return_value = [mock_entry]
    mock_hass.config_entries.async_reload = AsyncMock()

    call = ServiceCall(mock_hass, DOMAIN, "reload", {})
    await reload_handler(call)

    assert sensor.native_value == "idle"
    mock_hass.config_entries.async_reload.assert_awaited_once_with("test_entry_id")

    # Also test async_reload directly
    sensor.update_state("error")
    await async_reload(mock_hass)
    assert sensor.native_value == "idle"


@pytest.mark.asyncio
async def test_compile_clip_service_success(
    mock_hass: MagicMock, sample_config: dict[str, Any], tmp_path: Path
) -> None:
    """Test compile_clip service triggers async_compile_static_clip and updates status sensor."""
    await async_setup(mock_hass, sample_config)

    calls = mock_hass.services.async_register.call_args_list
    compile_handler = next(c.args[2] for c in calls if c.args[1] == "compile_clip")

    mock_metadata = {
        "clip_name": "freezer_door",
        "routine_name": "freezer_door",
        "video_path": str(tmp_path / "static" / "freezer_door.mp4"),
        "audio_path": None,
        "duration": DURATION_5_0,
        "slide_timestamps": [{"timestamp": 0.0, "actions_on_display": []}],
        "static": True,
        "last_pregenerated": "2026-10-17T12:00:00Z",
    }

    step_config = {
        "title": "Freezer Door Open!",
        "message": "Attention: The kitchen freezer door has been left open.",
        "voice": "Pulcherrima",
    }

    with patch(
        "custom_components.cast_studio.async_compile_static_clip",
        new=AsyncMock(return_value=mock_metadata),
    ) as mock_compile_static:
        call = ServiceCall(
            mock_hass,
            DOMAIN,
            "compile_clip",
            {
                "clip_name": "freezer_door",
                "step_config": step_config,
                "output_dir": str(tmp_path),
            },
        )
        await compile_handler(call)

        mock_compile_static.assert_awaited_once_with(
            mock_hass,
            clip_name="freezer_door",
            step_config=step_config,
            output_dir=str(tmp_path),
        )

        sensor = mock_hass.data[DOMAIN]["sensors"]["freezer_door"]
        assert sensor.native_value == "ready"
        assert sensor.extra_state_attributes["duration"] == DURATION_5_0
        assert sensor.extra_state_attributes["video_path"] == str(
            tmp_path / "static" / "freezer_door.mp4"
        )
        assert sensor.extra_state_attributes["slide_count"] == 1
        assert sensor.extra_state_attributes["active_task"] is None

    # Test error handling updates sensor to error
    with patch(
        "custom_components.cast_studio.async_compile_static_clip",
        new=AsyncMock(side_effect=RuntimeError("FFmpeg encode error")),
    ):
        call = ServiceCall(
            mock_hass,
            DOMAIN,
            "compile_clip",
            {
                "clip_name": "freezer_door",
                "step_config": step_config,
                "output_dir": str(tmp_path),
            },
        )
        with pytest.raises(RuntimeError, match="FFmpeg encode error"):
            await compile_handler(call)

        assert sensor.native_value == "error"
        assert sensor.extra_state_attributes["active_task"] is None


@pytest.mark.asyncio
async def test_compile_clip_service_validation(
    mock_hass: MagicMock, sample_config: dict[str, Any]
) -> None:
    """Test validation errors on missing or invalid clip_name and step_config."""
    await async_setup(mock_hass, sample_config)

    calls = mock_hass.services.async_register.call_args_list
    compile_handler = next(c.args[2] for c in calls if c.args[1] == "compile_clip")

    # Missing clip_name in service call
    with pytest.raises(ValueError, match="clip_name"):
        await compile_handler(
            ServiceCall(
                mock_hass,
                DOMAIN,
                "compile_clip",
                {"step_config": {"title": "Test"}},
            )
        )

    # Empty clip_name in service call
    with pytest.raises(ValueError, match="clip_name"):
        await compile_handler(
            ServiceCall(
                mock_hass,
                DOMAIN,
                "compile_clip",
                {"clip_name": "   ", "step_config": {"title": "Test"}},
            )
        )

    # Missing step_config in service call
    with pytest.raises(ValueError, match="step_config"):
        await compile_handler(
            ServiceCall(
                mock_hass,
                DOMAIN,
                "compile_clip",
                {"clip_name": "freezer_door"},
            )
        )

    # Invalid non-dict step_config in service call
    with pytest.raises(ValueError, match="step_config"):
        await compile_handler(
            ServiceCall(
                mock_hass,
                DOMAIN,
                "compile_clip",
                {"clip_name": "freezer_door", "step_config": "not_a_dict"},
            )
        )

    # Python API direct calls validation
    with pytest.raises(ValueError, match="clip_name"):
        await async_compile_clip(mock_hass, "", {"title": "Test"})

    with pytest.raises(ValueError, match="clip_name"):
        await async_compile_clip(mock_hass, "   ", {"title": "Test"})

    with pytest.raises(ValueError, match="step_config"):
        await async_compile_clip(mock_hass, "freezer_door", None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="step_config"):
        await async_compile_clip(mock_hass, "freezer_door", "invalid")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_run_routine_finds_and_plays_static_clip_instantly(
    mock_hass: MagicMock, sample_config: dict[str, Any], tmp_path: Path
) -> None:
    """Test run_routine detects static clip and plays immediately without re-compiling even if stale."""
    await async_setup(mock_hass, sample_config)

    static_dir = tmp_path / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    video_file = static_dir / "freezer_alert.mp4"
    video_file.write_bytes(b"dummy_static_video")
    meta_file = static_dir / "freezer_alert.metadata.json"
    meta_content = {
        "clip_name": "freezer_alert",
        "routine_name": "freezer_alert",
        "video_path": str(video_file),
        "audio_path": None,
        "duration": DURATION_5_0,
        "slide_timestamps": [{"timestamp": 0.0, "actions_on_display": []}],
        "static": True,
    }
    meta_file.write_text(json.dumps(meta_content), encoding="utf-8")

    # Set mtime to 100,000s in the past (> 24 hours)
    stale_time = time.time() - 100000
    os.utime(video_file, (stale_time, stale_time))
    os.utime(meta_file, (stale_time, stale_time))

    dummy_play_task = asyncio.create_task(asyncio.sleep(0.01))

    with (
        patch(
            "custom_components.cast_studio.async_compile_routine_media",
            new=AsyncMock(),
        ) as mock_compile_routine,
        patch(
            "custom_components.cast_studio.async_compile_static_clip",
            new=AsyncMock(),
        ) as mock_compile_static,
        patch(
            "custom_components.cast_studio.async_execute_background_actions",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.cast_studio.async_play_and_sync_routine",
            new=AsyncMock(return_value=dummy_play_task),
        ) as mock_play,
    ):
        result_meta = await async_run_routine(
            mock_hass,
            "freezer_alert",
            target_player="media_player.kitchen_display",
            output_dir=str(tmp_path),
        )

        # Neither routine nor static compiler should be triggered
        mock_compile_routine.assert_not_called()
        mock_compile_static.assert_not_called()

        # Playback should be dispatched immediately
        mock_play.assert_awaited_once_with(
            mock_hass,
            "freezer_alert",
            "media_player.kitchen_display",
            meta_content,
            [],
        )

        assert result_meta["clip_name"] == "freezer_alert"
        assert result_meta["static"] is True

        sensor = mock_hass.data[DOMAIN]["sensors"]["freezer_alert"]
        assert sensor.native_value == "playing"

        await dummy_play_task
        await asyncio.sleep(0.01)
        assert sensor.native_value == "ready"


@pytest.mark.asyncio
async def test_run_routine_finds_static_video_without_metadata_and_plays(
    mock_hass: MagicMock, sample_config: dict[str, Any], tmp_path: Path
) -> None:
    """Test run_routine detects static mp4 without metadata and plays with fallback meta."""
    await async_setup(mock_hass, sample_config)

    static_dir = tmp_path / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    video_file = static_dir / "freezer_alert.mp4"
    video_file.write_bytes(b"dummy_static_video")

    stale_time = time.time() - 100000
    os.utime(video_file, (stale_time, stale_time))

    dummy_play_task = asyncio.create_task(asyncio.sleep(0.01))

    with (
        patch(
            "custom_components.cast_studio.async_compile_routine_media",
            new=AsyncMock(),
        ) as mock_compile_routine,
        patch(
            "custom_components.cast_studio.async_compile_static_clip",
            new=AsyncMock(),
        ) as mock_compile_static,
        patch(
            "custom_components.cast_studio.async_play_and_sync_routine",
            new=AsyncMock(return_value=dummy_play_task),
        ) as mock_play,
    ):
        result_meta = await async_run_routine(
            mock_hass,
            "freezer_alert",
            target_player="media_player.kitchen_display",
            output_dir=str(tmp_path),
        )

        mock_compile_routine.assert_not_called()
        mock_compile_static.assert_not_called()
        mock_play.assert_awaited_once()

        assert result_meta["clip_name"] == "freezer_alert"
        assert result_meta["routine_name"] == "freezer_alert"
        assert result_meta["video_path"] == str(video_file)
        assert result_meta["audio_path"] is None
        assert result_meta["duration"] == 0.0
        assert result_meta["static"] is True


@pytest.mark.asyncio
async def test_run_routine_with_static_config_compiles_static_on_demand(
    mock_hass: MagicMock, sample_config: dict[str, Any], tmp_path: Path
) -> None:
    """Test run_routine with static: True compiles static clip on-demand when not cached."""
    await async_setup(mock_hass, sample_config)

    mock_static_meta = {
        "clip_name": "door_alert",
        "routine_name": "door_alert",
        "video_path": str(tmp_path / "static" / "door_alert.mp4"),
        "audio_path": None,
        "duration": DURATION_4_0,
        "slide_timestamps": [],
        "static": True,
    }

    dummy_play_task = asyncio.create_task(asyncio.sleep(0.01))

    with (
        patch(
            "custom_components.cast_studio.async_compile_static_clip",
            new=AsyncMock(return_value=mock_static_meta),
        ) as mock_compile_static,
        patch(
            "custom_components.cast_studio.async_compile_routine_media",
            new=AsyncMock(),
        ) as mock_compile_routine,
        patch(
            "custom_components.cast_studio.async_play_and_sync_routine",
            new=AsyncMock(return_value=dummy_play_task),
        ),
    ):
        routine_config = {
            "static": True,
            "step_config": {
                "title": "Front Door Open",
                "message": "Front door has been opened.",
            },
        }
        res = await async_run_routine(
            mock_hass,
            "door_alert",
            routine_config=routine_config,
            output_dir=str(tmp_path),
            target_player="media_player.living_room_display",
        )

        mock_compile_static.assert_awaited_once_with(
            mock_hass,
            clip_name="door_alert",
            step_config=routine_config["step_config"],
            output_dir=str(tmp_path),
        )
        mock_compile_routine.assert_not_called()
        assert res["static"] is True


def test_resolve_cast_media_url_static_clip(mock_hass: MagicMock) -> None:
    """Verify resolve_cast_media_url converts static clip path in /config/www/ to HTTP URL."""
    from custom_components.cast_studio.orchestrator import resolve_cast_media_url

    with patch(
        "custom_components.cast_studio.orchestrator.get_url",
        return_value="http://192.168.1.50:8123",
    ):
        result = resolve_cast_media_url(
            mock_hass,
            "/config/www/cast_studio/static/freezer_alert.mp4",
        )
        assert result.startswith(
            "http://192.168.1.50:8123/local/cast_studio/static/freezer_alert.mp4?v="
        )


@pytest.mark.asyncio
async def test_run_routine_force_regenerate(
    mock_hass: MagicMock, sample_config: dict[str, Any], tmp_path: Path
) -> None:
    """Verify force_regenerate bypasses 24h fresh media check and compiles on demand."""
    await async_setup(mock_hass, sample_config)

    cached_duration = 10.0
    fresh_duration = 12.0
    meta_file = tmp_path / "welcome.metadata.json"
    video_file = tmp_path / "welcome.mp4"
    meta_file.write_text('{"video_path": "' + str(video_file) + '", "duration": 10.0}')
    video_file.write_text("mp4 content")

    mock_compile = AsyncMock(
        return_value={"video_path": str(video_file), "duration": fresh_duration}
    )
    with (
        patch(
            "custom_components.cast_studio.async_compile_routine_media",
            new=mock_compile,
        ),
        patch(
            "custom_components.cast_studio.async_play_and_sync_routine",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.cast_studio.async_execute_background_actions",
            new=AsyncMock(),
        ),
    ):
        # 1. Normal run with existing fresh media -> should NOT compile
        res1 = await async_run_routine(
            mock_hass,
            "welcome",
            routine_config={"output_dir": str(tmp_path)},
            output_dir=str(tmp_path),
        )
        assert res1["duration"] == cached_duration
        mock_compile.assert_not_called()

        # 2. Run with force_regenerate=True -> MUST compile fresh media
        res2 = await async_run_routine(
            mock_hass,
            "welcome",
            routine_config={"output_dir": str(tmp_path)},
            output_dir=str(tmp_path),
            force_regenerate=True,
        )
        assert res2["duration"] == fresh_duration
        mock_compile.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_routine_forwards_post_actions_to_metadata(
    mock_hass: MagicMock, sample_config: dict[str, Any], tmp_path: Path
) -> None:
    """Verify async_run_routine forwards post_briefing_actions from routine_config to metadata."""
    await async_setup(mock_hass, sample_config)

    video_file = tmp_path / "morning.mp4"
    video_file.write_bytes(b"video")
    meta_file = tmp_path / "morning.metadata.json"
    meta_content = {
        "video_path": str(video_file),
        "duration": 10.0,
        "slide_timestamps": [
            {"name": "greeting", "timestamp": 0.0},
            {"name": "fun_fact", "timestamp": 5.0},
        ],
        "last_pregenerated": datetime.now(UTC).isoformat(),
    }
    meta_file.write_text(json.dumps(meta_content), encoding="utf-8")

    dummy_play_task = asyncio.create_task(asyncio.sleep(0.01))
    post_action = {
        "service": "cast.show_lovelace_view",
        "target": {"entity_id": "media_player.bathroom_display"},
        "data": {"dashboard_path": "nest-display", "view_path": "morning"},
    }

    with (
        patch(
            "custom_components.cast_studio.async_compile_routine_media",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.cast_studio.async_execute_background_actions",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.cast_studio.async_play_and_sync_routine",
            new=AsyncMock(return_value=dummy_play_task),
        ) as mock_play,
    ):
        result_meta = await async_run_routine(
            mock_hass,
            "morning",
            target_player="media_player.bathroom_display",
            routine_config={
                "output_dir": str(tmp_path),
                "post_briefing_actions": [post_action],
            },
            output_dir=str(tmp_path),
        )

        assert "post_routine_actions" in result_meta
        assert result_meta["post_routine_actions"] == [post_action]

        mock_play.assert_awaited_once()
        passed_metadata = mock_play.await_args[0][3]
        assert passed_metadata.get("post_routine_actions") == [post_action]

        await dummy_play_task


@pytest.mark.asyncio
async def test_run_routine_service_call_forwards_top_level_post_routine_actions(
    mock_hass: MagicMock, sample_config: dict[str, Any], tmp_path: Path
) -> None:
    """Verify run_routine service handler forwards top-level post_routine_actions to routine_config."""
    await async_setup(mock_hass, sample_config)

    calls = mock_hass.services.async_register.call_args_list
    run_handler = next(c.args[2] for c in calls if c.args[1] == "run_routine")

    video_file = tmp_path / "morning.mp4"
    video_file.write_bytes(b"video")
    meta_file = tmp_path / "morning.metadata.json"
    meta_content = {
        "video_path": str(video_file),
        "duration": 10.0,
        "slide_timestamps": [{"timestamp": 0.0}],
        "last_pregenerated": datetime.now(UTC).isoformat(),
    }
    meta_file.write_text(json.dumps(meta_content), encoding="utf-8")

    dummy_play_task = asyncio.create_task(asyncio.sleep(0.01))
    post_action = {
        "service": "cast.show_lovelace_view",
        "target": {"entity_id": "media_player.bathroom_display"},
        "data": {"dashboard_path": "nest-display", "view_path": "morning"},
    }

    with (
        patch(
            "custom_components.cast_studio.async_compile_routine_media",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.cast_studio.async_execute_background_actions",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.cast_studio.async_play_and_sync_routine",
            new=AsyncMock(return_value=dummy_play_task),
        ) as mock_play,
    ):
        service_call = ServiceCall(
            mock_hass,
            DOMAIN,
            "run_routine",
            {
                "routine_name": "morning",
                "target_player": "media_player.bathroom_display",
                "output_dir": str(tmp_path),
                "post_routine_actions": [post_action],
            },
        )
        await run_handler(service_call)

        mock_play.assert_awaited_once()
        passed_metadata = mock_play.await_args[0][3]
        assert passed_metadata.get("post_routine_actions") == [post_action]

        await dummy_play_task


def test_is_media_ready_and_fresh_invalidates_mismatched_slide_count(
    tmp_path: Path,
) -> None:
    """Verify _is_media_ready_and_fresh returns (False, None) when slide count mismatches expected_step_count."""
    video_file = tmp_path / "morning_briefing.mp4"
    video_file.write_bytes(b"mp4")
    meta_file = tmp_path / "morning_briefing.metadata.json"
    meta_content = {
        "video_path": str(video_file),
        "duration": 10.0,
        "slide_timestamps": [{"name": "greeting", "timestamp": 0.0}],
    }
    meta_file.write_text(json.dumps(meta_content), encoding="utf-8")

    is_ready, meta = _is_media_ready_and_fresh(
        str(tmp_path),
        "morning_briefing",
        expected_step_count=6,
    )
    assert is_ready is False
    assert meta is None

    is_ready_match, meta_match = _is_media_ready_and_fresh(
        str(tmp_path),
        "morning_briefing",
        expected_step_count=1,
    )
    assert is_ready_match is True
    assert meta_match is not None


@pytest.mark.asyncio
async def test_run_routine_auto_builds_default_morning_briefing_steps(
    mock_hass: MagicMock,
    tmp_path: Path,
) -> None:
    """Verify async_run_routine auto-populates default morning steps when routines dict is empty."""
    await async_setup(mock_hass, {DOMAIN: {}})
    assert mock_hass.data[DOMAIN]["routines"] == {}

    mock_metadata = {
        "video_path": str(tmp_path / "morning_briefing.mp4"),
        "audio_path": None,
        "duration": DURATION_20_0,
        "slide_timestamps": [
            {"name": "greeting", "timestamp": 0.0},
            {"name": "fun_fact", "timestamp": 8.0},
        ],
    }
    dummy_play_task = asyncio.create_task(asyncio.sleep(0.01))

    with (
        patch(
            "custom_components.cast_studio.async_compile_routine_media",
            new=AsyncMock(return_value=mock_metadata),
        ) as mock_compile,
        patch(
            "custom_components.cast_studio.async_execute_background_actions",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.cast_studio.async_play_and_sync_routine",
            new=AsyncMock(return_value=dummy_play_task),
        ),
    ):
        await async_run_routine(
            mock_hass,
            routine_name="morning_briefing",
            target_player="media_player.bathroom_display",
            output_dir=str(tmp_path),
        )

        mock_compile.assert_awaited_once()
        compiled_config = mock_compile.await_args.kwargs["routine_config"]
        steps = compiled_config.get("steps", [])
        assert len(steps) > 0
        step_names = [s.get("name") for s in steps]
        assert "greeting" in step_names
        assert "weather" in step_names
        assert "fun_fact" in step_names
        assert "calendar" in step_names
        assert "todo" in step_names
        assert "commute" in step_names
        assert "scripture" in step_names

        await dummy_play_task


@pytest.mark.asyncio
async def test_run_routine_zero_config_invalidates_stale_single_slide_cache(
    mock_hass: MagicMock,
    tmp_path: Path,
) -> None:
    """Verify async_run_routine with no routine_config invalidates a 1-slide cached morning_briefing.metadata.json and recompiles."""
    await async_setup(mock_hass, {DOMAIN: {}})

    video_file = tmp_path / "morning_briefing.mp4"
    video_file.write_bytes(b"stale_mp4")
    meta_file = tmp_path / "morning_briefing.metadata.json"
    stale_meta = {
        "video_path": str(video_file),
        "duration": 10.0,
        "slide_timestamps": [{"name": "greeting", "timestamp": 0.0}],
        "last_pregenerated": datetime.now(UTC).isoformat(),
    }
    meta_file.write_text(json.dumps(stale_meta), encoding="utf-8")

    fresh_metadata = {
        "video_path": str(video_file),
        "audio_path": None,
        "duration": DURATION_20_0,
        "slide_timestamps": [
            {"name": "greeting", "timestamp": 0.0},
            {"name": "fun_fact", "timestamp": 8.0},
        ],
    }

    with (
        patch(
            "custom_components.cast_studio.async_compile_routine_media",
            new=AsyncMock(return_value=fresh_metadata),
        ) as mock_compile,
        patch(
            "custom_components.cast_studio.async_execute_background_actions",
            new=AsyncMock(),
        ),
    ):
        result = await async_run_routine(
            mock_hass,
            "morning_briefing",
            output_dir=str(tmp_path),
        )

        mock_compile.assert_awaited_once()
        assert result["duration"] == DURATION_20_0
        assert len(result["slide_timestamps"]) == 2


@pytest.mark.asyncio
async def test_async_setup_entry_stores_configured_output_dir_and_uses_as_default(
    mock_hass: MagicMock,
    tmp_path: Path,
) -> None:
    """Verify async_setup_entry stores CONF_OUTPUT_DIR in hass.data and service handlers use it."""
    from custom_components.cast_studio import async_setup_entry
    from custom_components.cast_studio.const import CONF_OUTPUT_DIR

    custom_out = str(tmp_path / "custom_cast_dir")
    mock_entry = MagicMock()
    mock_entry.data = {CONF_OUTPUT_DIR: custom_out}
    mock_entry.options = {}
    mock_hass.config_entries.async_forward_entry_setups = AsyncMock()

    await async_setup(mock_hass, {DOMAIN: {}})
    await async_setup_entry(mock_hass, mock_entry)

    assert mock_hass.data[DOMAIN]["output_dir"] == custom_out

    mock_meta = {
        "video_path": f"{custom_out}/morning.mp4",
        "duration": 5.0,
        "slide_timestamps": [{"name": "greeting", "timestamp": 0.0}],
    }
    with patch(
        "custom_components.cast_studio.async_compile_routine_media",
        new=AsyncMock(return_value=mock_meta),
    ) as mock_compile:
        await async_pregenerate_routine(
            mock_hass,
            routine_name="morning",
            routine_config={"steps": [{"name": "greeting", "visual": True}]},
        )
        assert mock_compile.await_args.kwargs["output_dir"] == custom_out


def test_discover_morning_entities_collects_multiple_commute_sensors(
    mock_hass: MagicMock,
) -> None:
    """Verify _discover_morning_entities collects all commute/travel_time sensors for comparison."""
    from custom_components.cast_studio import _build_default_morning_routine_config

    s_drive = MagicMock(entity_id="sensor.javan_commute_drive_to_work")
    s_metro = MagicMock(entity_id="sensor.javan_commute_metro")
    mock_hass.states.async_all = MagicMock(return_value=[s_drive, s_metro])

    cfg = _build_default_morning_routine_config(mock_hass, {"name": "Javan"})
    commute_step = next(s for s in cfg["steps"] if s["name"] == "commute")
    assert commute_step["entity_ids"] == [
        "sensor.javan_commute_drive_to_work",
        "sensor.javan_commute_metro",
    ]


def test_sensor_unrecorded_attributes() -> None:
    """Verify CastStudioRoutineSensor marks video_path and audio_path as unrecorded attributes."""
    from custom_components.cast_studio.sensor import CastStudioRoutineSensor

    assert "video_path" in CastStudioRoutineSensor._unrecorded_attributes
    assert "audio_path" in CastStudioRoutineSensor._unrecorded_attributes


@pytest.mark.asyncio
async def test_run_routine_accepts_media_player_and_entity_id_aliases(
    mock_hass: MagicMock, sample_config: dict[str, Any], tmp_path: Path
) -> None:
    """Verify run_routine service call accepts 'media_player' and 'entity_id' as target_player aliases."""
    await async_setup(mock_hass, sample_config)

    calls = mock_hass.services.async_register.call_args_list
    run_handler = next(c.args[2] for c in calls if c.args[1] == "run_routine")

    video_file = tmp_path / "morning.mp4"
    video_file.write_bytes(b"video")
    meta_file = tmp_path / "morning.metadata.json"
    meta_content = {
        "video_path": str(video_file),
        "duration": 10.0,
        "slide_timestamps": [{"timestamp": 0.0}],
        "last_pregenerated": datetime.now(UTC).isoformat(),
    }
    meta_file.write_text(json.dumps(meta_content), encoding="utf-8")

    dummy_play_task = asyncio.create_task(asyncio.sleep(0.01))

    with (
        patch(
            "custom_components.cast_studio.async_compile_routine_media",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.cast_studio.async_execute_background_actions",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.cast_studio.async_play_and_sync_routine",
            new=AsyncMock(return_value=dummy_play_task),
        ) as mock_play,
    ):
        service_call_1 = ServiceCall(
            mock_hass,
            DOMAIN,
            "run_routine",
            {
                "routine_name": "morning",
                "media_player": "media_player.bathroom_display",
                "output_dir": str(tmp_path),
            },
        )
        await run_handler(service_call_1)
        mock_play.assert_awaited_once()
        assert mock_play.await_args[0][2] == "media_player.bathroom_display"
        mock_play.reset_mock()

        service_call_2 = ServiceCall(
            mock_hass,
            DOMAIN,
            "run_routine",
            {
                "routine_name": "morning",
                "entity_id": ["media_player.kitchen_display"],
                "output_dir": str(tmp_path),
            },
        )
        await run_handler(service_call_2)
        mock_play.assert_awaited_once()
        assert mock_play.await_args[0][2] == "media_player.kitchen_display"

        await dummy_play_task
