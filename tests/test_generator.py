"""Tests for the media generator engine."""

from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cast_studio.const import CONF_COMMUTE_DESTINATION, DOMAIN
from custom_components.cast_studio.generator import (
    GeneratorError,
    RoutineConfigError,
    _ensure_encouragement_signoff,
    _extract_entity_context,
    _is_unconfigured_optional_step,
    async_compile_routine_media,
    async_compile_static_clip,
    async_extract_commute_data,
    async_extract_entity_context,
    async_extract_weather_data,
    async_fetch_commute_map,
    async_resolve_google_maps_api_key,
    compute_weather_advisory,
    get_audio_duration,
    is_step_image_dynamic,
)

TWO_STEPS = 2
MIN_FALLBACK_DURATION = 3.0
SAMPLE_RATE_16K = 16000
EXPECTED_WAV_DURATION = 2.5


@pytest.mark.asyncio
async def test_compile_routine_media_flow(tmp_path: Path):
    """Test full compilation pipeline matching brief verbatim."""
    hass = MagicMock()
    routine_config = {
        "visuals": True,
        "steps": [
            {"name": "greeting", "visual": True, "voice": "Pulcherrima"},
            {"name": "weather", "visual": True, "voice": "Pulcherrima"},
        ],
    }
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}

    with (
        patch(
            "custom_components.cast_studio.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.cast_studio.generator.async_generate_step_content",
            new=AsyncMock(
                return_value={"visual_card": {"title": "T"}, "narration_script": "S"}
            ),
        ),
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.cast_studio.generator.render_slide",
            return_value="slide.jpg",
        ),
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await async_compile_routine_media(
            hass, "morning", routine_config, recipient, str(tmp_path)
        )
        assert "video_path" in result
        assert "slide_timestamps" in result
        assert len(result["slide_timestamps"]) == TWO_STEPS
        assert "duration" in result
        assert result["duration"] > 0


@pytest.mark.asyncio
async def test_compile_routine_media_writes_metadata_json(tmp_path: Path):
    """Test that companion metadata JSON file is written to output_dir."""
    hass = MagicMock()
    routine_config = {
        "visuals": True,
        "steps": [
            {
                "name": "greeting",
                "visual": True,
                "voice": "Pulcherrima",
                "voice_personality": "upbeat_energetic",
                "actions_on_display": [{"action": "light.turn_on"}],
            },
            {
                "name": "weather",
                "visual": True,
                "voice": "Enceladus",
                "voice_personality": "news_anchor",
            },
        ],
    }
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}

    with (
        patch(
            "custom_components.cast_studio.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.cast_studio.generator.async_generate_step_content",
            new=AsyncMock(
                return_value={
                    "visual_card": {"title": "Slide Title"},
                    "narration_script": "Spoken text",
                }
            ),
        ),
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.cast_studio.generator.render_slide",
            return_value="slide.jpg",
        ),
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await async_compile_routine_media(
            hass, "morning", routine_config, recipient, str(tmp_path)
        )

        metadata_file = tmp_path / "morning.metadata.json"
        assert metadata_file.exists()

        loaded_metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        assert loaded_metadata["video_path"] == str(tmp_path / "morning.mp4")
        assert loaded_metadata["duration"] == result["duration"]
        assert len(loaded_metadata["slide_timestamps"]) == TWO_STEPS
        assert loaded_metadata["slide_timestamps"][0]["name"] == "greeting"
        assert loaded_metadata["slide_timestamps"][0]["actions_on_display"] == [
            {"action": "light.turn_on"}
        ]
        assert loaded_metadata["slide_timestamps"][1]["name"] == "weather"
        assert loaded_metadata["slide_timestamps"][1]["timestamp"] > 0


@pytest.mark.asyncio
async def test_compile_routine_media_filters_non_visual_steps(tmp_path: Path):
    """Test that visual compilation filters out steps where visual is False."""
    hass = MagicMock()
    routine_config = {
        "visuals": True,
        "steps": [
            {"name": "intro", "visual": True},
            {
                "name": "coffee",
                "visual": False,
                "actions": [{"action": "switch.turn_on"}],
            },
            {"name": "news", "visual": True},
        ],
    }
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}

    mock_generate = AsyncMock(
        return_value={"visual_card": {"title": "T"}, "narration_script": "S"}
    )
    with (
        patch(
            "custom_components.cast_studio.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.cast_studio.generator.async_generate_step_content",
            mock_generate,
        ),
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.cast_studio.generator.render_slide",
            return_value="slide.jpg",
        ),
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await async_compile_routine_media(
            hass, "morning", routine_config, recipient, str(tmp_path)
        )
        assert len(result["slide_timestamps"]) == TWO_STEPS
        # mock_generate should only be called for visual steps
        assert mock_generate.call_count == TWO_STEPS
        step_names = [call.args[1]["name"] for call in mock_generate.call_args_list]
        assert "coffee" not in step_names
        assert "intro" in step_names
        assert "news" in step_names


@pytest.mark.asyncio
async def test_compile_routine_media_purges_scratchpad_in_finally(tmp_path: Path):
    """Test that scratchpad directory is purged in finally block even on ffmpeg error."""
    hass = MagicMock()
    routine_config = {
        "visuals": True,
        "steps": [{"name": "intro", "visual": True}],
    }
    recipient = {"name": "Javan"}

    with (
        patch(
            "custom_components.cast_studio.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.cast_studio.generator.async_generate_step_content",
            new=AsyncMock(
                return_value={"visual_card": {"title": "T"}, "narration_script": "S"}
            ),
        ),
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.cast_studio.generator.render_slide",
            return_value="slide.jpg",
        ),
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=False),  # Simulating FFmpeg failure
        ),
        patch(
            "custom_components.cast_studio.generator.cleanup_scratch_dir"
        ) as mock_cleanup,
    ):
        with pytest.raises(GeneratorError):
            await async_compile_routine_media(
                hass, "failing_routine", routine_config, recipient, str(tmp_path)
            )

        # Cleanup MUST be called even on failure
        mock_cleanup.assert_called_once()
        assert mock_cleanup.call_args[0][0] == "failing_routine"


@pytest.mark.asyncio
async def test_compile_routine_media_audio_only_mode(tmp_path: Path):
    """Test audio-only mode when visuals is False."""
    hass = MagicMock()
    routine_config = {
        "visuals": False,
        "steps": [
            {"name": "step1", "voice": "Pulcherrima"},
            {"name": "step2", "voice": "Enceladus"},
        ],
    }
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}

    mock_render = MagicMock()
    with (
        patch(
            "custom_components.cast_studio.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.cast_studio.generator.async_generate_step_content",
            new=AsyncMock(
                return_value={"visual_card": {}, "narration_script": "Audio text"}
            ),
        ),
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.cast_studio.generator.render_slide",
            mock_render,
        ),
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await async_compile_routine_media(
            hass, "podcast", routine_config, recipient, str(tmp_path)
        )
        # render_slide must NOT be called in audio-only mode
        mock_render.assert_not_called()
        assert "audio_path" in result
        assert result["audio_path"] == str(tmp_path / "podcast.mp3")
        assert result["video_path"] is None
        assert "slide_timestamps" in result
        assert len(result["slide_timestamps"]) == TWO_STEPS

        metadata_file = tmp_path / "podcast.metadata.json"
        assert metadata_file.exists()
        loaded = json.loads(metadata_file.read_text(encoding="utf-8"))
        assert loaded["audio_path"] == str(tmp_path / "podcast.mp3")


@pytest.mark.asyncio
async def test_compile_routine_media_empty_steps_error(tmp_path: Path):
    """Test that missing or empty steps raise RoutineConfigError."""
    hass = MagicMock()
    recipient = {"name": "Javan"}

    with pytest.raises(RoutineConfigError, match="no steps"):
        await async_compile_routine_media(
            hass, "empty", {"steps": []}, recipient, str(tmp_path)
        )

    with pytest.raises(RoutineConfigError, match="No visual steps"):
        await async_compile_routine_media(
            hass,
            "no_visuals",
            {"visuals": True, "steps": [{"name": "act", "visual": False}]},
            recipient,
            str(tmp_path),
        )


@pytest.mark.asyncio
async def test_compile_routine_media_creates_nonexistent_output_dir(tmp_path: Path):
    """Test that compilation creates output_dir if it does not yet exist."""
    hass = MagicMock()
    routine_config = {
        "visuals": True,
        "steps": [
            {"name": "greeting", "visual": True, "voice": "Pulcherrima"},
        ],
    }
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}
    nonexistent_dir = tmp_path / "deep" / "nested" / "automations"
    assert not nonexistent_dir.exists()

    with (
        patch(
            "custom_components.cast_studio.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.cast_studio.generator.async_generate_step_content",
            new=AsyncMock(
                return_value={"visual_card": {"title": "T"}, "narration_script": "S"}
            ),
        ),
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.cast_studio.generator.render_slide",
            return_value="slide.jpg",
        ),
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await async_compile_routine_media(
            hass, "morning", routine_config, recipient, str(nonexistent_dir)
        )
        assert nonexistent_dir.exists()
        assert nonexistent_dir.is_dir()
        assert result["video_path"] == str(nonexistent_dir / "morning.mp4")
        assert (nonexistent_dir / "morning.metadata.json").exists()


def test_get_audio_duration_with_real_wav(tmp_path: Path):
    """Test get_audio_duration computes accurate duration from WAV file."""
    wav_path = tmp_path / "test.wav"
    num_frames = int(SAMPLE_RATE_16K * EXPECTED_WAV_DURATION)

    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE_16K)
        wf.writeframes(b"\x00\x00" * num_frames)

    computed_duration = get_audio_duration(str(wav_path))
    assert pytest.approx(computed_duration, 0.01) == EXPECTED_WAV_DURATION


def test_get_audio_duration_fallback():
    """Test get_audio_duration fallback when file does not exist."""
    duration = get_audio_duration(
        "/nonexistent/dummy.wav", fallback_text="This is a test narration"
    )
    assert duration >= MIN_FALLBACK_DURATION


def test_extract_entity_context_all_domains():
    """Test _extract_entity_context extracts and formats HA states from multiple domains."""
    hass = MagicMock()

    mock_weather = MagicMock(
        state="partlycloudy",
        attributes={"temperature": 72, "temperature_unit": "°F", "forecast": "dry"},
    )
    mock_calendar = MagicMock(
        state="on",
        attributes={"message": "Team Standup", "start_time": "09:00:00"},
    )
    mock_todo = MagicMock(
        state="4",
        attributes={},
    )
    mock_sensor = MagicMock(
        state="23.5",
        attributes={"unit_of_measurement": "kWh"},
    )

    state_map = {
        "weather.home": mock_weather,
        "calendar.work": mock_calendar,
        "todo.groceries": mock_todo,
        "sensor.power": mock_sensor,
    }
    hass.states.get = MagicMock(side_effect=state_map.get)

    step = {
        "name": "daily_overview",
        "entity_ids": [
            "weather.home",
            "calendar.work",
            "todo.groceries",
            "sensor.power",
        ],
        "context": "Focus on high priority tasks",
    }

    ctx = _extract_entity_context(hass, step)
    assert "Focus on high priority tasks" in ctx
    assert "Weather (weather.home): Condition: partlycloudy, Temperature: 72°F" in ctx
    assert (
        "Calendar (calendar.work): State: on, Summary: Team Standup, Start: 09:00:00"
        in ctx
    )
    assert "To-do (todo.groceries): 4 tasks pending" in ctx
    assert "Sensor (sensor.power): 23.5 kWh" in ctx


def test_is_unconfigured_optional_step_matrix():
    """Test _is_unconfigured_optional_step identifies unconfigured vs valid steps."""
    hass = MagicMock()
    mock_state = MagicMock(state="sunny")
    hass.states.get = MagicMock(
        side_effect=lambda eid: mock_state if eid == "weather.home" else None
    )

    # 1. Non-entity step: should not be skipped
    assert not _is_unconfigured_optional_step(hass, {"name": "greeting"})

    # 2. Entity step with entity_id: None and no context: should be skipped
    assert _is_unconfigured_optional_step(hass, {"name": "todo", "entity_id": None})

    # 3. Entity step with entity_id: "" and no context: should be skipped
    assert _is_unconfigured_optional_step(hass, {"name": "todo", "entity_id": ""})

    # 4. Entity step with missing entity and no context: should be skipped
    assert _is_unconfigured_optional_step(
        hass, {"name": "scripture", "entity_id": "sensor.missing"}
    )

    # 5. Entity step with missing entity BUT has fallback context: should NOT be skipped
    assert not _is_unconfigured_optional_step(
        hass,
        {
            "name": "scripture",
            "entity_id": "sensor.missing",
            "context": "Proverbs 3:5",
        },
    )

    # 6. Entity step with missing entity BUT has actions: should NOT be skipped
    assert not _is_unconfigured_optional_step(
        hass,
        {
            "name": "todo",
            "entity_id": None,
            "actions": [{"action": "light.turn_on"}],
        },
    )

    # 7. Entity step with valid state in HA: should NOT be skipped
    assert not _is_unconfigured_optional_step(
        hass, {"name": "weather", "entity_id": "weather.home"}
    )


@pytest.mark.asyncio
async def test_compile_routine_skips_unconfigured_optional_step(tmp_path: Path):
    """Test that optional unconfigured entity steps are skipped during compilation."""
    hass = MagicMock()
    hass.states.get = MagicMock(
        return_value=None
    )  # task list entity not found in states

    routine_config = {
        "visuals": True,
        "steps": [
            {"name": "greeting", "visual": True, "voice": "Pulcherrima"},
            {"name": "todo", "visual": True, "entity_id": None},  # should be skipped
            {"name": "outro", "visual": True, "voice": "Pulcherrima"},
        ],
    }
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}

    with (
        patch(
            "custom_components.cast_studio.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.cast_studio.generator.async_generate_step_content",
            new=AsyncMock(
                return_value={"visual_card": {"title": "T"}, "narration_script": "S"}
            ),
        ) as mock_generate,
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.cast_studio.generator.render_slide",
            return_value="slide.jpg",
        ),
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await async_compile_routine_media(
            hass, "morning", routine_config, recipient, str(tmp_path)
        )
        # Only greeting and outro should be compiled (2 steps, todo skipped)
        assert len(result["slide_timestamps"]) == TWO_STEPS
        assert mock_generate.call_count == TWO_STEPS
        compiled_step_names = [s["name"] for s in result["slide_timestamps"]]
        assert "todo" not in compiled_step_names
        assert "greeting" in compiled_step_names
        assert "outro" in compiled_step_names


@pytest.mark.asyncio
async def test_compile_routine_injects_entity_context(tmp_path: Path):
    """Test that entity state is extracted and injected into step context before AI generation."""
    hass = MagicMock()
    mock_weather_state = MagicMock(
        state="sunny",
        attributes={"temperature": 75, "temperature_unit": "°F"},
    )
    hass.states.get = MagicMock(
        side_effect=lambda eid: mock_weather_state if eid == "weather.home" else None
    )

    routine_config = {
        "visuals": True,
        "steps": [
            {
                "name": "weather",
                "visual": True,
                "entity_id": "weather.home",
                "context": "Keep it brief",
            },
        ],
    }
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}

    with (
        patch(
            "custom_components.cast_studio.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.cast_studio.generator.async_generate_step_content",
            new=AsyncMock(
                return_value={"visual_card": {"title": "T"}, "narration_script": "S"}
            ),
        ) as mock_generate,
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.cast_studio.generator.render_slide",
            return_value="slide.jpg",
        ),
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=True),
        ),
    ):
        await async_compile_routine_media(
            hass, "morning", routine_config, recipient, str(tmp_path)
        )
        assert mock_generate.call_count == 1
        call_step = mock_generate.call_args[0][1]
        assert "Keep it brief" in call_step["context"]
        assert "Weather (weather.home): Condition: sunny" in call_step["context"]
        assert "Temperature: 75°F" in call_step["context"]


@pytest.mark.asyncio
async def test_compile_static_clip_success(tmp_path: Path):
    """Test compiling a static reusable clip and writing companion metadata."""
    hass = MagicMock()
    step_config = {
        "title": "Freezer Door Open!",
        "message": "Attention: The kitchen freezer door has been left open.",
        "voice": "Pulcherrima",
        "actions": [{"action": "light.turn_on"}],
    }

    with (
        patch(
            "custom_components.cast_studio.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.cast_studio.generator.render_slide",
            return_value="slide.jpg",
        ),
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await async_compile_static_clip(
            hass, "freezer_door", step_config, output_dir=str(tmp_path)
        )

        assert result["clip_name"] == "freezer_door"
        assert result["routine_name"] == "freezer_door"
        assert result["static"] is True
        assert result["video_path"] == str(tmp_path / "static" / "freezer_door.mp4")
        assert result["audio_path"] is None
        assert result["duration"] > 0
        assert len(result["slide_timestamps"]) == 1
        assert result["slide_timestamps"][0]["name"] == "freezer_door"
        assert result["slide_timestamps"][0]["actions_on_display"] == [
            {"action": "light.turn_on"}
        ]

        metadata_file = tmp_path / "static" / "freezer_door.metadata.json"
        assert metadata_file.exists()
        loaded = json.loads(metadata_file.read_text(encoding="utf-8"))
        assert loaded == result


@pytest.mark.asyncio
async def test_compile_static_clip_scratch_cleanup_on_error(tmp_path: Path):
    """Test scratchpad directory cleanup even when FFmpeg or synthesis fails."""
    hass = MagicMock()
    step_config = {
        "title": "Alert",
        "message": "Alert message",
    }

    with (
        patch(
            "custom_components.cast_studio.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.cast_studio.generator.render_slide",
            return_value="slide.jpg",
        ),
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "custom_components.cast_studio.generator.cleanup_scratch_dir"
        ) as mock_cleanup,
    ):
        with pytest.raises(GeneratorError, match="FFmpeg clip compilation failed"):
            await async_compile_static_clip(
                hass, "alert_clip", step_config, output_dir=str(tmp_path)
            )

        mock_cleanup.assert_called_once()
        assert mock_cleanup.call_args[0][0] == "static_alert_clip"


@pytest.mark.asyncio
async def test_compile_static_clip_invalid_name(tmp_path: Path):
    """Assert RoutineConfigError for empty or traversal names."""
    hass = MagicMock()
    step_config = {"message": "Test"}

    with pytest.raises(RoutineConfigError, match="empty or whitespace"):
        await async_compile_static_clip(hass, "", step_config, output_dir=str(tmp_path))

    with pytest.raises(RoutineConfigError, match="empty or whitespace"):
        await async_compile_static_clip(
            hass, "   ", step_config, output_dir=str(tmp_path)
        )

    with pytest.raises(RoutineConfigError, match="path traversal"):
        await async_compile_static_clip(
            hass, "../escape", step_config, output_dir=str(tmp_path)
        )

    with pytest.raises(RoutineConfigError, match="path traversal"):
        await async_compile_static_clip(
            hass, "sub/dir", step_config, output_dir=str(tmp_path)
        )


@pytest.mark.asyncio
async def test_compile_static_clip_invokes_ai_when_no_narration(tmp_path: Path):
    """Test that AI content generation is invoked if no narration/message/text is provided."""
    hass = MagicMock()
    step_config = {
        "title": "AI Alert",
    }
    mock_ai_generate = AsyncMock(
        return_value={
            "visual_card": {"title": "AI Alert", "subtitle": "Warning"},
            "narration_script": "This is an AI generated narration.",
        }
    )

    with (
        patch(
            "custom_components.cast_studio.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.cast_studio.generator.async_generate_step_content",
            mock_ai_generate,
        ),
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.cast_studio.generator.render_slide",
            return_value="slide.jpg",
        ),
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await async_compile_static_clip(
            hass, "ai_alert", step_config, output_dir=str(tmp_path)
        )
        assert mock_ai_generate.call_count == 1
        assert result["clip_name"] == "ai_alert"
        assert result["static"] is True


def test_extract_entity_context_scripture_sensor():
    """Test that scripture and text attributes are extracted for daily scripture sensors."""
    hass = MagicMock()
    state_mock = MagicMock()
    state_mock.state = "2026-10-17"
    state_mock.attributes = {
        "scripture": "Proverbs 7:25",
        "text": "Do not let your heart turn aside to her paths.",
    }
    hass.states.get.return_value = state_mock
    step = {"entity_id": "sensor.jw_library_daily_text_today"}
    context = _extract_entity_context(hass, step)
    assert "Proverbs 7:25" in context
    assert "Do not let your heart turn aside to her paths." in context


def test_extract_entity_context_empty_calendar():
    """Test calendar in off state with no events reports schedule is clear."""
    hass = MagicMock()
    state_mock = MagicMock()
    state_mock.state = "off"
    state_mock.attributes = {}
    hass.states.get.return_value = state_mock
    step = {"entity_id": "calendar.personal"}
    context = _extract_entity_context(hass, step)
    assert (
        "Calendar (calendar.personal): No scheduled meetings today. The user's schedule is completely clear."
        in context
    )
    assert "State: off" not in context


@pytest.mark.asyncio
async def test_compile_routine_media_explicit_narration_and_layout(tmp_path: Path):
    """Test routine step with explicit narration bypasses AI and preserves icon layout."""
    hass = MagicMock()
    routine_config = {
        "visuals": True,
        "steps": [
            {
                "name": "greeting",
                "visual": True,
                "title": "Good Morning",
                "subtitle": "Javan",
                "layout": "icon",
                "icon": "welcome_avatar.png",
                "narration": "Good morning Javan! Today is a great day.",
            }
        ],
    }
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}

    mock_ai = AsyncMock()
    mock_synth = AsyncMock(return_value="audio.wav")
    mock_render = MagicMock(return_value="slide.jpg")

    with (
        patch(
            "custom_components.cast_studio.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.cast_studio.generator.async_generate_step_content",
            mock_ai,
        ),
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            mock_synth,
        ),
        patch(
            "custom_components.cast_studio.generator.render_slide",
            mock_render,
        ),
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=True),
        ),
    ):
        await async_compile_routine_media(
            hass, "morning", routine_config, recipient, str(tmp_path)
        )
        mock_ai.assert_not_called()
        assert "Jay-ven" in mock_synth.call_args.args[1]
        rendered_card = mock_render.call_args.args[0]
        assert rendered_card["title"] == "Good Morning"
        assert rendered_card["subtitle"] == "Javan"
        assert rendered_card["layout"] == "icon"
        assert rendered_card["icon"] == "welcome_avatar.png"


@pytest.mark.asyncio
async def test_compile_routine_media_visual_slide_padding_and_duration(tmp_path: Path):
    """Test visual slides apply intermediate (0.5s) and final (2.5s) padding and pass duration/pad_seconds to build_slide_clip_command."""
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    routine_config = {
        "visuals": True,
        "steps": [
            {"name": "greeting", "visual": True, "narration": "Hello there."},
            {"name": "outro", "visual": True, "narration": "Have a great day."},
            {"name": "unconfigured_tail", "visual": True, "entity_id": None},
        ],
    }
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}

    with (
        patch(
            "custom_components.cast_studio.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.cast_studio.generator.get_audio_duration",
            return_value=4.0,
        ),
        patch(
            "custom_components.cast_studio.generator.render_slide",
            return_value="slide.jpg",
        ),
        patch(
            "custom_components.cast_studio.generator.build_slide_clip_command",
            return_value=["ffmpeg"],
        ) as mock_build_clip,
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await async_compile_routine_media(
            hass, "morning", routine_config, recipient, str(tmp_path)
        )

    assert len(result["slide_timestamps"]) == TWO_STEPS
    assert result["slide_timestamps"][0]["name"] == "greeting"
    assert result["slide_timestamps"][0]["duration"] == 4.5
    assert result["slide_timestamps"][0]["start_time"] == 0.0
    assert result["slide_timestamps"][0]["end_time"] == 4.5

    assert result["slide_timestamps"][1]["name"] == "outro"
    assert result["slide_timestamps"][1]["duration"] == 6.5
    assert result["slide_timestamps"][1]["start_time"] == 4.5
    assert result["slide_timestamps"][1]["end_time"] == 11.0
    assert result["duration"] == 11.0

    assert mock_build_clip.call_count == TWO_STEPS
    assert mock_build_clip.call_args_list[0].kwargs == {
        "duration": 4.5,
        "pad_seconds": 0.5,
    }
    assert mock_build_clip.call_args_list[1].kwargs == {
        "duration": 6.5,
        "pad_seconds": 2.5,
    }


@pytest.mark.asyncio
async def test_async_extract_entity_context_calendar_get_events():
    """Test async_extract_entity_context fetches today's events via calendar.get_events even when calendar state is off."""
    hass = MagicMock()
    cal_state = MagicMock(state="off", attributes={})
    hass.states.get = MagicMock(return_value=cal_state)
    hass.services.async_call = AsyncMock(
        return_value={
            "calendar.personal": {
                "events": [
                    {
                        "summary": "Team Standup",
                        "start": "2026-10-19T09:00:00",
                    }
                ]
            }
        }
    )

    step = {"name": "calendar", "entity_id": "calendar.personal"}
    context = await async_extract_entity_context(hass, step)
    assert "Team Standup" in context
    assert "2026-10-19T09:00:00" in context
    assert "No scheduled meetings today" not in context


@pytest.mark.asyncio
async def test_async_extract_entity_context_todo_get_items():
    """Test async_extract_entity_context fetches pending task summaries via todo.get_items."""
    hass = MagicMock()
    todo_state = MagicMock(state="2", attributes={})
    hass.states.get = MagicMock(return_value=todo_state)
    hass.services.async_call = AsyncMock(
        return_value={
            "todo.javan_s_list": {
                "items": [
                    {"summary": "50 push-ups"},
                    {"summary": "Pay rent"},
                ]
            }
        }
    )

    step = {"name": "todo", "entity_id": "todo.javan_s_list"}
    context = await async_extract_entity_context(hass, step)
    assert "50 push-ups" in context
    assert "Pay rent" in context
    assert "2 tasks pending" in context


def test_extract_entity_context_daily_text_companion_commentary():
    """Test _extract_entity_context includes companion _comment sensor text for Daily Text."""
    hass = MagicMock()
    main_state = MagicMock(
        state="2026-10-19",
        attributes={
            "scripture": "Proverbs 3:5",
            "text": "Trust in Jehovah",
        },
    )
    comment_state = MagicMock(
        state="2026-10-19",
        attributes={
            "text": "When we rely on Jehovah's wisdom...",
        },
    )
    state_map = {
        "sensor.jw_library_daily_text_today": main_state,
        "sensor.jw_library_daily_text_today_comment": comment_state,
    }
    hass.states.get = MagicMock(side_effect=state_map.get)

    step = {"name": "scripture", "entity_id": "sensor.jw_library_daily_text_today"}
    context = _extract_entity_context(hass, step)
    assert "Proverbs 3:5" in context
    assert "Trust in Jehovah" in context
    assert "Commentary: When we rely on Jehovah's wisdom..." in context


@pytest.mark.asyncio
async def test_fun_fact_step_not_skipped_when_entity_unconfigured():
    """Test fun_fact step is not skipped when entity_id is empty and injects default fallback prompt."""
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=None)

    step = {"name": "fun_fact", "visual": True, "entity_id": ""}
    assert not _is_unconfigured_optional_step(hass, step)

    context = await async_extract_entity_context(hass, step)
    assert context == "Share an engaging, surprising morning fun fact."


def test_fun_fact_unavailable_entity_with_attributes_falls_back_to_default_prompt():
    """Test fun_fact step whose entity is unavailable with attributes (e.g. friendly_name) falls back to default fun fact prompt."""
    hass = MagicMock()
    state_mock = MagicMock(
        state="unavailable",
        attributes={"friendly_name": "Morning Trivia"},
    )
    hass.states.get = MagicMock(return_value=state_mock)

    step = {"name": "fun_fact", "visual": True, "entity_id": "sensor.morning_trivia"}
    context = _extract_entity_context(hass, step)
    assert context == "Share an engaging, surprising morning fun fact."


@pytest.mark.asyncio
async def test_national_day_step_not_skipped_when_entity_unconfigured():
    """Test national_day step is not skipped when entity_id is empty and injects default fallback prompt."""
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=None)

    step = {"name": "national_day", "visual": True, "entity_id": ""}
    assert not _is_unconfigured_optional_step(hass, step)

    context = await async_extract_entity_context(hass, step)
    assert context == "Share today's official National Day celebration and its history."


def test_national_day_unavailable_entity_with_attributes_falls_back_to_default_prompt():
    """Test national_day step whose entity is unavailable with attributes falls back to default prompt."""
    hass = MagicMock()
    state_mock = MagicMock(
        state="unavailable",
        attributes={"friendly_name": "National Day Sensor"},
    )
    hass.states.get = MagicMock(return_value=state_mock)

    step = {
        "name": "national_day",
        "visual": True,
        "entity_id": "sensor.national_day",
    }
    context = _extract_entity_context(hass, step)
    assert context == "Share today's official National Day celebration and its history."


@pytest.mark.asyncio
async def test_async_extract_entity_context_weather_get_forecasts() -> None:
    """Test async_extract_entity_context queries weather.get_forecasts (HA 2024.6+) for daily high/low."""
    hass = MagicMock()
    weather_state = MagicMock(
        state="sunny",
        attributes={"temperature": 68, "temperature_unit": "°F", "humidity": 45},
    )
    hass.states.get = MagicMock(return_value=weather_state)
    hass.services.async_call = AsyncMock(
        return_value={
            "weather.home": {
                "forecast": [
                    {
                        "condition": "sunny",
                        "temperature": 80,
                        "templow": 58,
                        "precipitation_probability": 10,
                    },
                    {
                        "condition": "partlycloudy",
                        "temperature": 76,
                        "templow": 55,
                    },
                ]
            }
        }
    )

    step = {"name": "weather", "entity_id": "weather.home"}
    context = await async_extract_entity_context(hass, step)
    assert "Weather (weather.home): Condition: sunny" in context
    assert "Temperature: 68°F" in context
    assert "High: 80°F" in context
    assert "Low: 58°F" in context


def test_extract_entity_context_multi_sensor_commute_comparison() -> None:
    """Test _extract_entity_context compares multiple commute sensors and states which is faster."""
    hass = MagicMock()
    drive_state = MagicMock(
        state="31",
        attributes={
            "unit_of_measurement": "min",
            "friendly_name": "Drive to Work",
        },
    )
    metro_state = MagicMock(
        state="44",
        attributes={
            "unit_of_measurement": "min",
            "friendly_name": "Metro",
        },
    )
    state_map = {
        "sensor.javan_commute_drive_to_work": drive_state,
        "sensor.javan_commute_metro": metro_state,
    }
    hass.states.get = MagicMock(side_effect=state_map.get)

    step = {
        "name": "commute",
        "entity_ids": [
            "sensor.javan_commute_drive_to_work",
            "sensor.javan_commute_metro",
        ],
    }
    context = _extract_entity_context(hass, step)
    assert "Drive to Work" in context
    assert "Metro" in context
    assert (
        "Commute Comparison: Drive to Work (31 min) is 13 minutes faster than Metro (44 min)."
        in context
    )


def test_get_executable_steps_public_helper() -> None:
    """Test get_executable_steps filters non-visual and unconfigured optional steps."""
    from custom_components.cast_studio.generator import get_executable_steps

    hass = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    cfg = {
        "visuals": True,
        "steps": [
            {"name": "greeting", "visual": True},
            {"name": "lights", "visual": False},
            {"name": "fun_fact", "visual": True, "entity_id": ""},
            {"name": "todo", "visual": True, "entity_id": ""},
        ],
    }
    steps = get_executable_steps(hass, cfg)
    assert [s["name"] for s in steps] == ["greeting", "fun_fact"]


@pytest.mark.asyncio
async def test_resolve_step_visual_assets_weather_conditions() -> None:
    """Test weather step maps condition strings to respective bundled photos."""
    from custom_components.cast_studio.generator import (
        _async_resolve_step_visual_assets,
    )

    hass = MagicMock()
    ai_creds = {"platform": "conversation_agent"}

    # Rainy condition
    card_rain = {"weather_condition": "rainy"}
    await _async_resolve_step_visual_assets(
        hass, {"type": "weather"}, "weather", card_rain, ai_creds
    )
    assert card_rain["image_path"] == "weather_rainy.jpg"
    assert card_rain["layout"] == "photo"

    # Snowy condition
    card_snow = {"weather_condition": "snow"}
    await _async_resolve_step_visual_assets(
        hass, {"type": "weather"}, "weather", card_snow, ai_creds
    )
    assert card_snow["image_path"] == "weather_snow.jpg"

    # Cloudy condition
    card_cloud = {"weather_condition": "partlycloudy"}
    await _async_resolve_step_visual_assets(
        hass, {"type": "weather"}, "weather", card_cloud, ai_creds
    )
    assert card_cloud["image_path"] == "weather_cloudy.jpg"

    # Sunny / clear default
    card_sunny = {"weather_condition": "sunny"}
    await _async_resolve_step_visual_assets(
        hass, {"type": "weather"}, "weather", card_sunny, ai_creds
    )
    assert card_sunny["image_path"] == "weather_sunny.jpg"


@pytest.mark.asyncio
async def test_resolve_step_visual_assets_standard_morning_topics() -> None:
    """Test standard morning topics map to bundled photo assets."""
    from custom_components.cast_studio.generator import (
        _async_resolve_step_visual_assets,
    )

    hass = MagicMock()
    ai_creds = {"platform": "conversation_agent"}

    card_cal = {}
    await _async_resolve_step_visual_assets(
        hass, {"type": "calendar"}, "calendar", card_cal, ai_creds
    )
    assert card_cal["image_path"] == "calendar_agenda.jpg"
    assert card_cal["layout"] == "photo"

    card_todo = {}
    await _async_resolve_step_visual_assets(
        hass, {"type": "todo"}, "todo", card_todo, ai_creds
    )
    assert card_todo["image_path"] == "calendar_agenda.jpg"

    card_commute = {}
    await _async_resolve_step_visual_assets(
        hass, {"type": "commute"}, "commute", card_commute, ai_creds
    )
    assert card_commute["image_path"] == "commute_traffic.jpg"

    card_scripture = {}
    await _async_resolve_step_visual_assets(
        hass, {"type": "scripture"}, "scripture", card_scripture, ai_creds
    )
    assert card_scripture["image_path"] == "bible_scripture.jpg"


@pytest.mark.asyncio
async def test_resolve_step_visual_assets_ai_image_generation_and_fallback(
    tmp_path: Path,
) -> None:
    """Test AI image generation for greeting and fun fact with fallback on error."""
    from custom_components.cast_studio.generator import (
        _async_resolve_step_visual_assets,
    )

    hass = MagicMock()
    ai_creds = {"platform": "gemini", "api_key": "test_key"}
    scratch_dir = str(tmp_path)

    # Greeting with successful AI image generation
    generated_path = str(tmp_path / "bg_greeting.png")
    Path(generated_path).write_bytes(b"fake_image_bytes")

    card_greeting = {}
    with patch(
        "custom_components.cast_studio.generator.async_generate_ai_image",
        new=AsyncMock(return_value=generated_path),
    ) as mock_gen:
        await _async_resolve_step_visual_assets(
            hass,
            {"type": "greeting"},
            "greeting",
            card_greeting,
            ai_creds,
            scratch_dir=scratch_dir,
        )
        assert mock_gen.call_count == 1
        assert "coffee" in mock_gen.call_args.args[1]
        assert card_greeting["image_path"] == generated_path
        assert card_greeting["layout"] == "photo"

    # Fun fact with fallback when AI generation returns None
    card_fun_fact = {"title": "National Pancake Day"}
    with patch(
        "custom_components.cast_studio.generator.async_generate_ai_image",
        new=AsyncMock(return_value=None),
    ):
        await _async_resolve_step_visual_assets(
            hass,
            {"type": "fun_fact"},
            "fun_fact",
            card_fun_fact,
            ai_creds,
            scratch_dir=scratch_dir,
        )
        assert card_fun_fact["image_path"] == "fun_fact.jpg"
        assert card_fun_fact["layout"] == "photo"


@pytest.mark.asyncio
async def test_async_compile_single_step_verbatim_scripture_bypasses_llm(
    tmp_path: Path,
) -> None:
    """Verify scripture step produces verbatim narration from daily text sensors and bypasses LLM."""
    from types import SimpleNamespace

    from custom_components.cast_studio.generator import _async_compile_single_step

    fake_states = {
        "sensor.jw_library_daily_text_today": SimpleNamespace(
            attributes={
                "scripture": "1 John 4:8",
                "text": "Whoever does not love has not come to know God, because God is love.",
            }
        ),
        "sensor.jw_library_daily_text_today_comment": SimpleNamespace(
            attributes={"text": "Love is Jehovah's preeminent quality."}
        ),
    }
    mock_hass = MagicMock()
    mock_hass.states.get.side_effect = fake_states.get

    step = {
        "name": "scripture",
        "type": "scripture",
        "entity_id": "sensor.jw_library_daily_text_today",
    }
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}
    ai_creds = {"platform": "conversation_agent"}

    audio_target = str(tmp_path / "audio.wav")
    slide_target = str(tmp_path / "slide.jpg")
    clip_target = str(tmp_path / "clip.mp4")

    # Write dummy audio wav so duration extraction succeeds
    with wave.open(audio_target, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(b"\x00\x00" * 24000)

    with (
        patch(
            "custom_components.cast_studio.generator.async_generate_step_content",
            new=AsyncMock(),
        ) as mock_llm,
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value=audio_target),
        ) as mock_synth,
        patch(
            "custom_components.cast_studio.generator.render_slide",
            return_value=slide_target,
        ) as mock_render,
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=True),
        ),
    ):
        await _async_compile_single_step(
            mock_hass,
            step,
            "scripture",
            recipient,
            ai_creds,
            is_visual=True,
            default_pad_seconds=0.5,
            audio_target_path=audio_target,
            slide_target_path=slide_target,
            clip_target_path=clip_target,
        )

        # 1. LLM summarization must NOT be called
        mock_llm.assert_not_called()

        # 2. Narration sent to TTS must be verbatim matching script.read_daily_text
        mock_synth.assert_called_once()
        synth_script = mock_synth.call_args.args[1]
        assert "Today's text is from 1 John 4:8" in synth_script
        assert (
            "It reads: Whoever does not love has not come to know God" in synth_script
        )
        assert "Love is Jehovah's preeminent quality." in synth_script

        # 3. Slide rendered must have citation subtitle and scripture bullet point
        mock_render.assert_called_once()
        rendered_card = mock_render.call_args.args[0]
        assert rendered_card["title"] == "Daily Scripture"
        assert rendered_card["subtitle"] == "1 John 4:8"
        assert (
            rendered_card["bullet_points"][0]
            == '"Whoever does not love has not come to know God, because God is love."'
        )
        assert rendered_card["image_path"] == "bible_scripture.jpg"
        assert rendered_card["layout"] == "photo"


def test_is_step_image_dynamic_matrix() -> None:
    """Verify is_step_image_dynamic rules for all slide types and explicit overrides."""
    from custom_components.cast_studio.generator import is_step_image_dynamic

    # 1. Default rule: scripture / daily_text / bible and weather are static
    assert is_step_image_dynamic({"name": "scripture"}, "scripture") is False
    assert is_step_image_dynamic({"type": "daily_text"}, "step_05") is False
    assert is_step_image_dynamic({"name": "bible"}, "bible") is False
    assert is_step_image_dynamic({"name": "weather"}, "weather") is False

    # 2. Default rule: strict allowlist (greeting, fun_fact, national_day)
    assert is_step_image_dynamic({"name": "greeting"}, "greeting") is True
    assert is_step_image_dynamic({"name": "national_day"}, "national_day") is True
    assert is_step_image_dynamic({"name": "fun_fact"}, "fun_fact") is True
    assert (
        is_step_image_dynamic({"name": "daily_encouragement"}, "daily_encouragement")
        is False
    )
    assert is_step_image_dynamic({"name": "agenda"}, "agenda") is False
    assert is_step_image_dynamic({"name": "calendar"}, "calendar") is False
    assert is_step_image_dynamic({"name": "todo"}, "todo") is False
    assert is_step_image_dynamic({"name": "commute"}, "commute") is False

    # 3. Explicit dynamic_image boolean overrides
    assert (
        is_step_image_dynamic({"name": "scripture", "dynamic_image": True}, "scripture")
        is True
    )
    assert (
        is_step_image_dynamic({"name": "greeting", "dynamic_image": False}, "greeting")
        is False
    )
    assert (
        is_step_image_dynamic({"name": "weather", "dynamic_image": True}, "weather")
        is True
    )

    # 4. Explicit image_mode string overrides
    assert (
        is_step_image_dynamic(
            {"name": "scripture", "image_mode": "dynamic"}, "scripture"
        )
        is True
    )
    assert (
        is_step_image_dynamic({"name": "agenda", "image_mode": "static"}, "agenda")
        is False
    )

    # 5. Explicit static / static_image boolean flags
    assert (
        is_step_image_dynamic({"name": "fun_fact", "static": True}, "fun_fact") is False
    )
    assert (
        is_step_image_dynamic({"name": "commute", "static_image": True}, "commute")
        is False
    )

    # 6. Explicit pre-existing local image_path
    assert (
        is_step_image_dynamic(
            {"name": "greeting", "image_path": "assets/custom.jpg"}, "greeting"
        )
        is False
    )


@pytest.mark.asyncio
async def test_async_resolve_step_visual_assets_dynamic_generation(
    tmp_path: Path,
) -> None:
    """Verify dynamic AI image generation for non-scripture slides and static resolution for scripture."""
    from custom_components.cast_studio.generator import (
        _async_resolve_step_visual_assets,
    )

    mock_hass = MagicMock()
    ai_creds = {"platform": "gemini", "api_key": "test_key"}
    scratch = str(tmp_path)

    def _mock_gen_img(_h: Any, _prompt: str, path: str, *, ai_creds: Any = None) -> str:
        Path(path).write_bytes(b"img")
        return path

    # 1. Greeting: dynamic generation via Gemini Imagen
    card_greeting: dict[str, Any] = {}
    with patch(
        "custom_components.cast_studio.generator.async_generate_ai_image",
        new=AsyncMock(side_effect=_mock_gen_img),
    ) as mock_gen:
        await _async_resolve_step_visual_assets(
            mock_hass,
            {"name": "greeting"},
            "greeting",
            card_greeting,
            ai_creds,
            scratch_dir=scratch,
        )
        assert mock_gen.call_count == 1
        assert "coffee" in mock_gen.call_args.args[1]
        assert card_greeting["image_type"] == "dynamic"
        assert card_greeting["dynamic_image"] is True
        assert card_greeting["image_path"].endswith("bg_greeting.png")
        assert card_greeting["layout"] == "photo"

    # 2. Weather: dynamic generation with weather condition in prompt (explicit dynamic override)
    card_weather: dict[str, Any] = {"weather_condition": "rainy"}
    with patch(
        "custom_components.cast_studio.generator.async_generate_ai_image",
        new=AsyncMock(side_effect=_mock_gen_img),
    ) as mock_gen:
        await _async_resolve_step_visual_assets(
            mock_hass,
            {"name": "weather", "dynamic_image": True},
            "weather",
            card_weather,
            ai_creds,
            scratch_dir=scratch,
        )
        assert mock_gen.call_count == 1
        assert "rainy" in mock_gen.call_args.args[1]
        assert card_weather["image_type"] == "dynamic"
        assert card_weather["dynamic_image"] is True
        assert card_weather["image_path"].endswith("bg_weather.png")

    # 3. Agenda: dynamic generation with planner workspace prompt
    card_agenda: dict[str, Any] = {}
    with patch(
        "custom_components.cast_studio.generator.async_generate_ai_image",
        new=AsyncMock(side_effect=_mock_gen_img),
    ) as mock_gen:
        await _async_resolve_step_visual_assets(
            mock_hass,
            {"name": "agenda", "dynamic_image": True},
            "agenda",
            card_agenda,
            ai_creds,
            scratch_dir=scratch,
        )
        assert mock_gen.call_count == 1
        assert "planner" in mock_gen.call_args.args[1]
        assert card_agenda["image_type"] == "dynamic"
        assert card_agenda["dynamic_image"] is True
        assert card_agenda["image_path"].endswith("bg_agenda.png")

    # 4. Scripture: static asset without calling async_generate_ai_image
    card_scripture: dict[str, Any] = {}
    with patch(
        "custom_components.cast_studio.generator.async_generate_ai_image",
        new=AsyncMock(),
    ) as mock_gen:
        await _async_resolve_step_visual_assets(
            mock_hass,
            {"name": "scripture"},
            "scripture",
            card_scripture,
            ai_creds,
            scratch_dir=scratch,
        )
        mock_gen.assert_not_called()
        assert card_scripture["image_type"] == "static"
        assert card_scripture["dynamic_image"] is False
        assert card_scripture["image_path"] == "bible_scripture.jpg"
        assert card_scripture["layout"] == "photo"


@pytest.mark.asyncio
async def test_async_resolve_step_visual_assets_fallback_to_static_on_ai_failure(
    tmp_path: Path,
) -> None:
    """Verify fallback to bundled static assets and image_type='static' when AI generation fails."""
    from custom_components.cast_studio.generator import (
        _async_resolve_step_visual_assets,
    )

    mock_hass = MagicMock()
    ai_creds = {"platform": "gemini", "api_key": "test_key"}
    scratch = str(tmp_path)

    # 1. Greeting falls back to greeting_coffee.jpg
    card_greeting: dict[str, Any] = {}
    with patch(
        "custom_components.cast_studio.generator.async_generate_ai_image",
        new=AsyncMock(return_value=None),
    ):
        await _async_resolve_step_visual_assets(
            mock_hass,
            {"name": "greeting"},
            "greeting",
            card_greeting,
            ai_creds,
            scratch_dir=scratch,
        )
        assert card_greeting["image_type"] == "static"
        assert card_greeting["dynamic_image"] is False
        assert card_greeting["image_path"] == "greeting_coffee.jpg"

    # 2. Weather falls back to condition asset (e.g. weather_rainy.jpg)
    card_weather: dict[str, Any] = {"weather_condition": "rainy"}
    with patch(
        "custom_components.cast_studio.generator.async_generate_ai_image",
        new=AsyncMock(return_value=None),
    ):
        await _async_resolve_step_visual_assets(
            mock_hass,
            {"name": "weather"},
            "weather",
            card_weather,
            ai_creds,
            scratch_dir=scratch,
        )
        assert card_weather["image_type"] == "static"
        assert card_weather["dynamic_image"] is False
        assert card_weather["image_path"] == "weather_rainy.jpg"

    # 3. Agenda falls back to calendar_agenda.jpg
    card_agenda: dict[str, Any] = {}
    with patch(
        "custom_components.cast_studio.generator.async_generate_ai_image",
        new=AsyncMock(return_value=None),
    ):
        await _async_resolve_step_visual_assets(
            mock_hass,
            {"name": "agenda"},
            "agenda",
            card_agenda,
            ai_creds,
            scratch_dir=scratch,
        )
        assert card_agenda["image_type"] == "static"
        assert card_agenda["dynamic_image"] is False
        assert card_agenda["image_path"] == "calendar_agenda.jpg"


@pytest.mark.asyncio
async def test_agenda_step_extracts_both_calendar_and_todo_context() -> None:
    """Verify agenda step extracts both calendar events and todo items."""
    from custom_components.cast_studio.generator import async_extract_entity_context

    def _mock_call(
        domain: str, _service: Any, _data: Any, **_kwargs: Any
    ) -> dict[str, Any]:
        if domain == "calendar":
            return {
                "calendar.work": {
                    "events": [{"summary": "Team Sync", "start": "10:00"}]
                }
            }
        return {"todo.tasks": {"items": [{"summary": "Buy groceries"}]}}

    mock_hass = MagicMock()
    mock_hass.services.async_call = AsyncMock(side_effect=_mock_call)

    agenda_step = {
        "name": "agenda",
        "calendar_entities": ["calendar.work"],
        "todo_entity": "todo.tasks",
    }
    context = await async_extract_entity_context(mock_hass, agenda_step)
    assert "Calendar (calendar.work):" in context
    assert "Team Sync" in context
    assert "To-do (todo.tasks):" in context
    assert "Buy groceries" in context


@pytest.mark.asyncio
async def test_async_compile_routine_media_timestamps_include_image_type_and_dynamic_flag(
    tmp_path: Path,
) -> None:
    """Verify slide_timestamps metadata records image_type, dynamic_image, and image_path per slide."""
    from custom_components.cast_studio.generator import async_compile_routine_media

    mock_hass = MagicMock()
    mock_hass.states.get = MagicMock(return_value=None)
    mock_hass.services.async_call = AsyncMock(return_value=None)

    routine_config = {
        "visuals": True,
        "steps": [
            {"name": "greeting", "visual": True},
            {"name": "scripture", "visual": True},
        ],
    }
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}
    test_wav = str(tmp_path / "test.wav")
    test_jpg = str(tmp_path / "test.jpg")

    def _mock_gen_img(_h: Any, _prompt: str, path: str, *, ai_creds: Any = None) -> str:
        Path(path).write_bytes(b"img")
        return path

    with (
        patch(
            "custom_components.cast_studio.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "test_key"}),
        ),
        patch(
            "custom_components.cast_studio.generator.async_generate_step_content",
            new=AsyncMock(
                return_value={"narration_script": "Hello", "visual_card": {}}
            ),
        ),
        patch(
            "custom_components.cast_studio.generator.async_generate_ai_image",
            new=AsyncMock(side_effect=_mock_gen_img),
        ),
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value=test_wav),
        ),
        patch(
            "custom_components.cast_studio.generator.get_audio_duration",
            return_value=5.0,
        ),
        patch(
            "custom_components.cast_studio.generator.render_slide",
            return_value=test_jpg,
        ),
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=True),
        ),
    ):
        meta = await async_compile_routine_media(
            mock_hass,
            "test_routine",
            routine_config,
            recipient,
            output_dir=str(tmp_path),
        )

        slides = meta["slide_timestamps"]
        assert len(slides) == 2

        # 1. Greeting slide is dynamic
        assert slides[0]["name"] == "greeting"
        assert slides[0]["image_type"] == "dynamic"
        assert slides[0]["dynamic_image"] is True
        assert slides[0]["image_path"].endswith("bg_greeting.png")

        # 2. Scripture slide is static
        assert slides[1]["name"] == "scripture"
        assert slides[1]["image_type"] == "static"
        assert slides[1]["dynamic_image"] is False
        assert slides[1]["image_path"] == "bible_scripture.jpg"


@pytest.mark.asyncio
async def test_async_extract_weather_data_hourly_and_daily() -> None:
    mock_hass = MagicMock()
    mock_hass.states.get.return_value = MagicMock(
        state="partlycloudy",
        attributes={
            "temperature": 68,
            "humidity": 48,
            "wind_speed": 8,
            "precipitation_probability": 10,
            "friendly_name": "Home",
        },
    )

    async def mock_async_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
        domain = args[0] if len(args) > 0 else kwargs.get("domain")
        service = args[1] if len(args) > 1 else kwargs.get("service")
        data = args[2] if len(args) > 2 else kwargs.get("data", {})
        if domain == "weather" and service == "get_forecasts":
            ftype = (data or {}).get("type")
            if ftype == "hourly":
                return {
                    "weather.home": {
                        "forecast": [
                            {
                                "datetime": "2026-10-19T20:00:00",
                                "temperature": 68,
                                "condition": "partlycloudy",
                            },
                            {
                                "datetime": "2026-10-19T22:00:00",
                                "temperature": 70,
                                "condition": "sunny",
                            },
                            {
                                "datetime": "2026-10-20T01:00:00",
                                "temperature": 73,
                                "condition": "sunny",
                            },
                            {
                                "datetime": "2026-10-20T04:00:00",
                                "temperature": 74,
                                "condition": "partlycloudy",
                            },
                            {
                                "datetime": "2026-10-20T07:00:00",
                                "temperature": 67,
                                "condition": "cloudy",
                            },
                        ]
                    }
                }
            if ftype == "daily":
                return {
                    "weather.home": {
                        "forecast": [
                            {
                                "datetime": "2026-10-19",
                                "temperature": 74,
                                "templow": 54,
                                "condition": "partlycloudy",
                            },
                            {
                                "datetime": "2026-10-20",
                                "temperature": 72,
                                "templow": 52,
                                "condition": "sunny",
                            },
                        ]
                    }
                }
        return {}

    mock_hass.services.async_call = AsyncMock(side_effect=mock_async_call)

    weather_data = await async_extract_weather_data(mock_hass, "weather.home")
    assert weather_data["current_temp"] == 68
    assert weather_data["high"] == 74
    assert weather_data["low"] == 54
    assert len(weather_data["forecast_points"]) >= 5
    assert weather_data["forecast_points"][0]["temperature"] == 68


def test_is_step_image_dynamic_exempts_weather() -> None:
    assert is_step_image_dynamic({"name": "weather", "visual": True}) is False
    assert is_step_image_dynamic({"name": "fun_fact", "visual": True}) is True
    assert is_step_image_dynamic({"name": "scripture", "visual": True}) is False


@pytest.mark.asyncio
async def test_async_extract_weather_data_hourly_only_fallback() -> None:
    mock_hass = MagicMock()
    mock_hass.states.get.return_value = MagicMock(
        state="rainy",
        attributes={
            "temperature": 62,
            "humidity": 80,
            "wind_speed": 12,
            "precipitation_probability": 90,
            "friendly_name": "Cabin",
        },
    )

    async def mock_async_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
        domain = args[0] if len(args) > 0 else kwargs.get("domain")
        service = args[1] if len(args) > 1 else kwargs.get("service")
        data = args[2] if len(args) > 2 else kwargs.get("data", {})
        if domain == "weather" and service == "get_forecasts":
            ftype = (data or {}).get("type")
            if ftype == "hourly":
                return {
                    "weather.cabin": {
                        "forecast": [
                            {
                                "datetime": "2026-10-19T20:00:00",
                                "temperature": 60,
                                "condition": "rainy",
                            },
                            {
                                "datetime": "2026-10-19T22:00:00",
                                "temperature": 65,
                                "condition": "rainy",
                            },
                            {
                                "datetime": "2026-10-20T01:00:00",
                                "temperature": 55,
                                "condition": "cloudy",
                            },
                        ]
                    }
                }
            if ftype == "daily":
                return {}
        return {}

    mock_hass.services.async_call = AsyncMock(side_effect=mock_async_call)

    weather_data = await async_extract_weather_data(mock_hass, "weather.cabin")
    assert weather_data["current_temp"] == 62
    assert weather_data["high"] == 65
    assert weather_data["low"] == 55
    assert len(weather_data["forecast_points"]) == 3
    assert weather_data["forecast_points"][0]["time"] == "8 PM"


@pytest.mark.asyncio
async def test_async_extract_weather_data_daily_only_fallback() -> None:
    mock_hass = MagicMock()
    mock_hass.states.get.return_value = MagicMock(
        state="sunny",
        attributes={
            "temperature": 75,
            "humidity": 30,
            "wind_speed": 5,
            "friendly_name": "Beach",
        },
    )

    async def mock_async_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
        domain = args[0] if len(args) > 0 else kwargs.get("domain")
        service = args[1] if len(args) > 1 else kwargs.get("service")
        data = args[2] if len(args) > 2 else kwargs.get("data", {})
        if domain == "weather" and service == "get_forecasts":
            ftype = (data or {}).get("type")
            if ftype == "hourly":
                return {}
            if ftype == "daily":
                return {
                    "weather.beach": {
                        "forecast": [
                            {
                                "datetime": "2026-10-19",
                                "temperature": 82,
                                "templow": 64,
                                "condition": "sunny",
                            },
                        ]
                    }
                }
        return {}

    mock_hass.services.async_call = AsyncMock(side_effect=mock_async_call)

    weather_data = await async_extract_weather_data(mock_hass, "weather.beach")
    assert weather_data["current_temp"] == 75
    assert weather_data["high"] == 82
    assert weather_data["low"] == 64
    assert len(weather_data["forecast_points"]) == 1
    assert weather_data["forecast_points"][0]["time"] == "Today"


@pytest.mark.asyncio
async def test_async_extract_entity_context_attaches_weather_data() -> None:
    mock_hass = MagicMock()
    mock_hass.states.get.return_value = MagicMock(
        state="partlycloudy",
        attributes={
            "temperature": 68,
            "humidity": 48,
            "wind_speed": 8,
            "precipitation_probability": 10,
            "friendly_name": "Home",
        },
    )

    async def mock_async_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
        domain = args[0] if len(args) > 0 else kwargs.get("domain")
        service = args[1] if len(args) > 1 else kwargs.get("service")
        if domain == "weather" and service == "get_forecasts":
            return {
                "weather.home": {
                    "forecast": [
                        {
                            "datetime": "2026-10-19",
                            "temperature": 74,
                            "templow": 54,
                            "condition": "partlycloudy",
                        },
                    ]
                }
            }
        return {}

    mock_hass.services.async_call = AsyncMock(side_effect=mock_async_call)

    step = {"name": "weather", "entity_id": "weather.home"}
    context = await async_extract_entity_context(mock_hass, step)
    assert "Weather (weather.home): Condition: partlycloudy" in context
    assert "card" in step
    assert "weather_data" in step["card"]
    assert step["card"]["weather_data"]["current_temp"] == 68
    assert step["card"]["weather_data"]["high"] == 74
    assert step["card"]["weather_data"]["low"] == 54


async def test_async_extract_entity_context_attaches_structured_agenda_data() -> None:
    """Verify structured agenda_data is extracted with calendar events, todo items, and date_str."""
    from custom_components.cast_studio.generator import async_extract_entity_context

    def _mock_call(
        domain: str, service: Any, data: Any, **kwargs: Any
    ) -> dict[str, Any]:
        if domain == "calendar":
            return {
                "calendar.personal": {
                    "events": [
                        {
                            "summary": "Team Architecture Alignment",
                            "start": "2026-10-19T10:00:00",
                            "end": "2026-10-19T11:00:00",
                            "location": "Room A",
                        }
                    ]
                }
            }
        if domain == "todo":
            return {
                "todo.daily": {
                    "items": [
                        {"summary": "Review pull request"},
                        {"summary": "Prescription refill reminder"},
                    ]
                }
            }
        return {}

    mock_hass = MagicMock()
    mock_hass.services.async_call = AsyncMock(side_effect=_mock_call)

    step = {
        "name": "agenda",
        "calendar_entities": ["calendar.personal"],
        "todo_entity": "todo.daily",
    }
    context = await async_extract_entity_context(mock_hass, step)
    assert "calendar.personal" in context
    assert "Team Architecture Alignment" in context
    assert "Review pull request" in context

    assert "card" in step
    assert "agenda_data" in step["card"]
    agenda_data = step["card"]["agenda_data"]
    assert "date_str" in agenda_data
    assert len(agenda_data["calendar_events"]) == 1
    assert agenda_data["calendar_events"][0] == {
        "time": "10:00 AM – 11:00 AM",  # noqa: RUF001
        "title": "Team Architecture Alignment",
        "location": "Room A",
    }
    assert len(agenda_data["todo_items"]) == 2
    assert agenda_data["todo_items"] == [
        "Review pull request",
        "Prescription refill reminder",
    ]


async def test_async_extract_weather_data_includes_temperature_unit() -> None:
    """Verify async_extract_weather_data returns temperature_unit."""
    mock_hass = MagicMock()
    mock_hass.states.get.return_value = MagicMock(
        state="sunny",
        attributes={"temperature": 72, "temperature_unit": "°F"},
    )
    mock_hass.services.async_call = AsyncMock(return_value={})

    data = await async_extract_weather_data(mock_hass, "weather.home")
    assert data.get("temperature_unit") == "°F"


@pytest.mark.asyncio
async def test_format_event_time_range_utc_timezone_converted_to_local(
    hass: HomeAssistant,
) -> None:
    """Verify UTC ISO timestamps in calendar events are converted to local time."""
    from custom_components.cast_studio.generator import _format_event_time_range

    await hass.config.async_set_time_zone("America/New_York")

    # 14:00 UTC on Oct 19 is 10:00 EDT (UTC-4)
    # 15:30 UTC on Oct 19 is 11:30 EDT (UTC-4)
    formatted = _format_event_time_range(
        "2026-10-19T14:00:00Z",
        "2026-10-19T15:30:00+00:00",
    )
    assert formatted == "10:00 AM \u2013 11:30 AM"


@pytest.mark.asyncio
async def test_async_extract_entity_context_calendar_utc_converted_to_local(
    hass: HomeAssistant,
) -> None:
    """Verify UTC ISO timestamps in calendar.get_events are formatted in local time."""
    await hass.config.async_set_time_zone("America/New_York")

    mock_hass = MagicMock()
    mock_hass.states.get.return_value = MagicMock(state="on", attributes={})
    mock_hass.services.async_call = AsyncMock(
        return_value={
            "calendar.personal": {
                "events": [
                    {
                        "summary": "Sprint Planning",
                        "start": "2026-10-19T14:00:00Z",
                        "end": "2026-10-19T15:00:00Z",
                    }
                ]
            }
        }
    )

    step = {
        "name": "agenda",
        "calendar_entities": ["calendar.personal"],
    }
    await async_extract_entity_context(mock_hass, step)
    agenda_events = step["card"]["agenda_data"]["calendar_events"]
    assert len(agenda_events) == 1
    # 14:00 - 15:00 UTC is 10:00 AM - 11:00 AM EDT
    assert agenda_events[0]["time"] == "10:00 AM \u2013 11:00 AM"


@pytest.mark.asyncio
async def test_async_extract_weather_data_utc_hourly_converted_to_local(
    hass: HomeAssistant,
) -> None:
    """Verify UTC ISO timestamps in hourly forecast points are converted to local time."""
    await hass.config.async_set_time_zone("America/New_York")

    mock_hass = MagicMock()
    mock_hass.states.get.return_value = MagicMock(
        state="sunny",
        attributes={"temperature": 70, "temperature_unit": "°F"},
    )
    mock_hass.services.async_call = AsyncMock(
        return_value={
            "weather.home": {
                "forecast": [
                    {
                        "datetime": "2026-10-19T14:00:00Z",
                        "temperature": 72,
                        "condition": "sunny",
                    },
                    {
                        "datetime": "2026-10-19T15:00:00+00:00",
                        "temperature": 74,
                        "condition": "sunny",
                    },
                ]
            }
        }
    )

    data = await async_extract_weather_data(mock_hass, "weather.home")
    # 14:00 UTC is 10 AM EDT, 15:00 UTC is 11 AM EDT
    assert data["forecast_points"][0]["time"] == "10 AM"
    assert data["forecast_points"][1]["time"] == "11 AM"


@pytest.mark.asyncio
async def test_async_extract_weather_data_temp_unit_fallback_to_hass_config() -> None:
    """Verify temperature unit falls back to hass.config.units.temperature_unit when not in state attrs."""
    mock_hass = MagicMock()
    mock_hass.states.get.return_value = MagicMock(
        state="sunny",
        attributes={"temperature": 21},  # no temperature_unit in attrs
    )
    mock_hass.config.units.temperature_unit = "°C"
    mock_hass.services.async_call = AsyncMock(return_value={})

    data = await async_extract_weather_data(mock_hass, "weather.home")
    assert data["temperature_unit"] == "°C"


@pytest.mark.asyncio
async def test_async_compile_single_step_weather_data_overwrites_llm_hallucination(
    tmp_path: Path,
) -> None:
    """Verify live HA weather_data in step['card'] overwrites any dummy weather_data returned by LLM."""
    from custom_components.cast_studio.generator import _async_compile_single_step

    mock_hass = MagicMock()
    live_weather = {
        "current_temp": 72,
        "condition": "sunny",
        "condition_text": "Sunny",
        "forecast_points": [{"time": "12 PM", "temp": 75}],
    }
    hallucinated_weather = {
        "current_temp": 999,
        "condition": "blizzard",
        "condition_text": "Hallucinated Blizzard",
    }

    step = {
        "name": "weather",
        "type": "weather",
        "card": {
            "weather_data": live_weather,
        },
    }
    recipient = {"name": "Javan"}
    ai_creds = {"platform": "conversation_agent"}

    audio_target = str(tmp_path / "audio.wav")
    slide_target = str(tmp_path / "slide.jpg")
    clip_target = str(tmp_path / "clip.mp4")

    with wave.open(audio_target, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(b"\x00\x00" * 24000)

    with (
        patch(
            "custom_components.cast_studio.generator.async_generate_step_content",
            new=AsyncMock(
                return_value={
                    "visual_card": {
                        "title": "Weather Today",
                        "weather_data": hallucinated_weather,
                    },
                    "narration_script": "Here is the weather forecast.",
                }
            ),
        ),
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value=audio_target),
        ),
        patch(
            "custom_components.cast_studio.generator.render_slide",
            return_value=slide_target,
        ) as mock_render,
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=True),
        ),
    ):
        await _async_compile_single_step(
            mock_hass,
            step,
            "weather",
            recipient,
            ai_creds,
            is_visual=True,
            default_pad_seconds=0.5,
            audio_target_path=audio_target,
            slide_target_path=slide_target,
            clip_target_path=clip_target,
        )

        assert mock_render.called
        rendered_card = mock_render.call_args[0][0]
        # Verified HA weather data must have overwritten the LLM hallucination
        assert rendered_card["weather_data"] == live_weather
        assert rendered_card["weather_data"]["current_temp"] == 72


@pytest.mark.asyncio
async def test_extract_commute_data_multi_sensor_comparison(
    hass: HomeAssistant, tmp_path: Path
):
    """Test multi-sensor commute extraction comparing driving and transit."""
    hass.states.async_set(
        "zone.test_office", "0", {"latitude": 40.1, "longitude": -75.1}
    )
    hass.states.async_set(
        "sensor.javan_commute_drive_to_work",
        "35",
        {
            "destination": "zone.test_office",
            "mode": "driving",
            "unit_of_measurement": "min",
        },
    )
    hass.states.async_set(
        "sensor.javan_commute_metro",
        "44",
        {"line": "Silver Line", "unit_of_measurement": "min"},
    )
    step = {
        "name": "commute",
        "type": "commute",
        "commute_sensors": [
            "sensor.javan_commute_drive_to_work",
            "sensor.javan_commute_metro",
        ],
    }

    with patch(
        "custom_components.cast_studio.generator.async_fetch_commute_map",
        return_value={
            "summary": "I-66 W",
            "distance": "24.5 mi",
            "map_image_path": str(tmp_path / "map.png"),
        },
    ):
        data = await async_extract_commute_data(hass, step, mode_override="fastest")

    assert data["selected_mode"] == "driving"
    assert data["drive_time"] == 35
    assert data["transit_time"] == 44
    assert data["diff_minutes"] == 9
    assert data["faster_mode"] == "driving"
    assert data["drive_summary"] == "I-66 W"
    assert data["transit_summary"] == "Silver Line"


@pytest.mark.asyncio
async def test_extract_commute_data_single_mode_fallback(hass: HomeAssistant):
    """Test commute extraction when only driving sensor exists."""
    hass.states.async_set(
        "sensor.javan_commute_drive_to_work", "28", {"unit_of_measurement": "min"}
    )
    step = {
        "name": "commute",
        "type": "commute",
        "commute_sensors": ["sensor.javan_commute_drive_to_work"],
    }

    data = await async_extract_commute_data(hass, step)
    assert data["selected_mode"] == "driving"
    assert data["drive_time"] == 28
    assert data["transit_time"] is None
    assert data["diff_minutes"] is None
    assert data["distance"] == ""


def _set_both_commute_sensors(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "sensor.javan_commute_drive_to_work", "35", {"unit_of_measurement": "min"}
    )
    hass.states.async_set(
        "sensor.javan_commute_metro",
        "44",
        {"line": "Silver Line", "unit_of_measurement": "min"},
    )


@pytest.mark.asyncio
async def test_extract_commute_data_singular_and_string_sensor_keys(
    hass: HomeAssistant,
):
    """Singular commute_sensor and comma-separated strings resolve to full entity ids."""
    _set_both_commute_sensors(hass)

    singular = await async_extract_commute_data(
        hass,
        {"name": "commute", "commute_sensor": "sensor.javan_commute_drive_to_work"},
    )
    assert singular["drive_time"] == 35
    assert singular["transit_time"] is None

    as_string = await async_extract_commute_data(
        hass,
        {
            "name": "commute",
            "commute_sensors": (
                "sensor.javan_commute_drive_to_work, sensor.javan_commute_metro"
            ),
        },
    )
    assert as_string["drive_time"] == 35
    assert as_string["transit_time"] == 44


@pytest.mark.asyncio
async def test_extract_entity_context_routine_transportation_mode_override(
    hass: HomeAssistant,
):
    """A routine-level transportation_mode on the step selects the commute mode."""
    _set_both_commute_sensors(hass)
    step = {
        "name": "commute",
        "type": "commute",
        "commute_sensors": [
            "sensor.javan_commute_drive_to_work",
            "sensor.javan_commute_metro",
        ],
        "transportation_mode": "transit",
    }

    await async_extract_entity_context(hass, step)

    assert step["card"]["commute_data"]["selected_mode"] == "transit"


@pytest.mark.asyncio
async def test_compile_single_step_forwards_commute_data(
    hass: HomeAssistant, tmp_path: Path
):
    """Test that _async_compile_single_step forwards commute_data into card_data."""
    from custom_components.cast_studio.generator import _async_compile_single_step

    mock_hass = MagicMock(spec=hass)
    live_commute = {
        "selected_mode": "driving",
        "drive_time": 30,
        "transit_time": 45,
        "diff_minutes": 15,
        "faster_mode": "driving",
        "drive_summary": "I-66 W",
        "transit_summary": "Silver Line",
        "summary": "via I-66 W",
        "distance": "20.0 mi",
        "eta": "8:30 AM",
        "traffic_condition": "typical",
        "map_image_path": str(tmp_path / "map.png"),
    }
    step = {
        "name": "commute",
        "type": "commute",
        "card": {
            "commute_data": live_commute,
            "title": "Old LLM Title",
        },
    }
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}
    ai_creds = {"platform": "mock"}

    audio_target = str(tmp_path / "audio.wav")
    slide_target = str(tmp_path / "slide.jpg")
    clip_target = str(tmp_path / "clip.mp4")

    with (
        patch(
            "custom_components.cast_studio.generator.async_generate_step_content",
            new=AsyncMock(
                return_value={
                    "visual_card": {
                        "title": "Hallucinated Commute",
                        "commute_data": {"selected_mode": "walking", "drive_time": 999},
                    },
                    "narration_script": "Commute looks fine.",
                }
            ),
        ),
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value=audio_target),
        ),
        patch(
            "custom_components.cast_studio.generator.render_slide",
            return_value=slide_target,
        ) as mock_render,
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=True),
        ),
    ):
        await _async_compile_single_step(
            mock_hass,
            step,
            "commute",
            recipient,
            ai_creds,
            is_visual=True,
            default_pad_seconds=0.5,
            audio_target_path=audio_target,
            slide_target_path=slide_target,
            clip_target_path=clip_target,
        )

        assert mock_render.called
        rendered_card = mock_render.call_args[0][0]
        assert rendered_card["commute_data"] == live_commute
        assert rendered_card["commute_data"]["drive_time"] == 30


@pytest.mark.asyncio
async def test_resolve_google_maps_api_key_from_config_entry(hass: HomeAssistant):
    """Test resolving Google Maps API key from CastStudio config entry."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.cast_studio.const import CONF_MAPS_API_KEY, DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        options={CONF_MAPS_API_KEY: "entry_maps_key_123"},
        data={},
    )
    entry.add_to_hass(hass)

    key = await async_resolve_google_maps_api_key(hass)
    assert key == "entry_maps_key_123"


@pytest.mark.asyncio
async def test_resolve_google_maps_api_key_from_secrets_yaml(
    hass: HomeAssistant, tmp_path: Path
):
    """Test resolving Google Maps API key from secrets.yaml."""
    secrets_file = tmp_path / "secrets.yaml"
    secrets_file.write_text("google_maps_api_key: secret_gmaps_key_456\n")

    with patch.object(hass.config, "path", return_value=str(secrets_file)):
        key = await async_resolve_google_maps_api_key(hass)
    assert key == "secret_gmaps_key_456"


@pytest.mark.asyncio
async def test_resolve_google_maps_api_key_from_travel_time_entry(hass: HomeAssistant):
    """Test resolving Google Maps API key from google_travel_time entry."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain="google_travel_time",
        data={"api_key": "travel_time_key_789"},
    )
    entry.add_to_hass(hass)

    key = await async_resolve_google_maps_api_key(hass)
    assert key == "travel_time_key_789"


@pytest.mark.asyncio
async def test_resolve_google_maps_api_key_none(hass: HomeAssistant):
    """Test resolving Google Maps API key returns None when none configured."""
    key = await async_resolve_google_maps_api_key(hass)
    assert key is None


@pytest.mark.asyncio
async def test_fetch_commute_map_success(hass: HomeAssistant, tmp_path: Path):
    """Test fetching Directions overview and Static Maps dark route image."""
    api_key = "test_key_abc"
    origin = (40.0, -75.0)
    destination = (40.1, -75.1)

    mock_dir_response = {
        "status": "OK",
        "routes": [
            {
                "summary": "I-66 W",
                "overview_polyline": {"points": "encoded_polyline_points"},
                "legs": [
                    {
                        "distance": {"text": "24.5 mi"},
                        "duration": {"text": "35 mins", "value": 2100},
                    }
                ],
            }
        ],
    }
    dummy_image_bytes = b"\x89PNG\r\n\x1a\nfake_png_data"

    mock_resp_dir = AsyncMock()
    mock_resp_dir.status = 200
    mock_resp_dir.json = AsyncMock(return_value=mock_dir_response)

    mock_resp_static = AsyncMock()
    mock_resp_static.status = 200
    mock_resp_static.read = AsyncMock(return_value=dummy_image_bytes)

    class MockGetContext:
        def __init__(self, resp: Any) -> None:
            self.resp = resp

        async def __aenter__(self) -> Any:
            return self.resp

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: object,
        ) -> None:
            pass

    def mock_get(url: str, params: Any = None) -> MockGetContext:
        if "directions" in url:
            assert params["origin"] == f"{origin[0]},{origin[1]}"
            assert params["destination"] == f"{destination[0]},{destination[1]}"
            assert params["mode"] == "driving"
            assert params["key"] == api_key
            return MockGetContext(mock_resp_dir)
        if "staticmap" in url:
            # Check params contain dark mode styling and polyline
            param_dict = dict(params) if isinstance(params, list) else params
            assert "path" in param_dict or any(p[0] == "path" for p in params)
            return MockGetContext(mock_resp_static)
        msg = f"Unexpected url {url}"
        raise ValueError(msg)

    mock_session = MagicMock()
    mock_session.get.side_effect = mock_get

    with patch(
        "custom_components.cast_studio.generator.async_get_clientsession",
        return_value=mock_session,
    ):
        result = await async_fetch_commute_map(
            hass, origin, destination, "driving", api_key, str(tmp_path)
        )

    assert result is not None
    assert result["summary"] == "I-66 W"
    assert result["distance"] == "24.5 mi"
    assert result["duration"] == "35 mins"
    assert result["polyline"] == "encoded_polyline_points"
    saved_path = Path(result["map_image_path"])
    assert saved_path.is_file()
    assert saved_path.read_bytes() == dummy_image_bytes


@pytest.mark.asyncio
async def test_fetch_commute_map_directions_error(hass: HomeAssistant, tmp_path: Path):
    """Test fetch commute map gracefully returns None on Directions error."""
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(
        return_value={"status": "REQUEST_DENIED", "error_message": "Invalid key"}
    )

    class MockGetContext:
        async def __aenter__(self) -> Any:
            return mock_resp

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: object,
        ) -> None:
            pass

    mock_session = MagicMock()
    mock_session.get.return_value = MockGetContext()

    with patch(
        "custom_components.cast_studio.generator.async_get_clientsession",
        return_value=mock_session,
    ):
        result = await async_fetch_commute_map(
            hass, (40.0, -75.0), (40.1, -75.1), "driving", "bad_key", str(tmp_path)
        )

    assert result is None


@pytest.mark.asyncio
async def test_fetch_commute_map_missing_key(hass: HomeAssistant, tmp_path: Path):
    """Test fetch commute map returns None when api_key is missing."""
    result = await async_fetch_commute_map(
        hass, (40.0, -75.0), (40.1, -75.1), "driving", "", str(tmp_path)
    )
    assert result is None


@pytest.mark.asyncio
async def test_extract_commute_data_transit_faster(hass: HomeAssistant, tmp_path: Path):
    """Test commute extraction when transit is faster than driving."""
    hass.states.async_set("sensor.javan_commute_drive", "45", {"mode": "driving"})
    hass.states.async_set("sensor.javan_commute_transit", "32", {"line": "Orange Line"})
    step = {
        "name": "commute",
        "type": "commute",
        "commute_sensors": [
            "sensor.javan_commute_drive",
            "sensor.javan_commute_transit",
        ],
    }

    with patch(
        "custom_components.cast_studio.generator.async_fetch_commute_map",
        return_value={
            "summary": "Orange Line",
            "distance": "18.2 mi",
            "map_image_path": str(tmp_path / "map.png"),
        },
    ):
        data = await async_extract_commute_data(hass, step)

    assert data["selected_mode"] == "transit"
    assert data["faster_mode"] == "transit"
    assert data["diff_minutes"] == 13
    assert data["drive_time"] == 45
    assert data["transit_time"] == 32
    assert data["transit_summary"] == "Orange Line"


@pytest.mark.asyncio
async def test_extract_commute_data_mode_override_driving(
    hass: HomeAssistant, tmp_path: Path
):
    """Test mode_override forces driving even when transit is faster."""
    hass.states.async_set("sensor.javan_commute_drive", "45", {"mode": "driving"})
    hass.states.async_set("sensor.javan_commute_transit", "32", {"line": "Orange Line"})
    step = {
        "name": "commute",
        "type": "commute",
        "commute_sensors": [
            "sensor.javan_commute_drive",
            "sensor.javan_commute_transit",
        ],
    }

    with patch(
        "custom_components.cast_studio.generator.async_fetch_commute_map",
        return_value={
            "summary": "I-66 W",
            "distance": "22.0 mi",
            "map_image_path": str(tmp_path / "map.png"),
        },
    ):
        data = await async_extract_commute_data(hass, step, mode_override="driving")

    assert data["selected_mode"] == "driving"
    assert data["faster_mode"] == "transit"
    assert data["diff_minutes"] == 13


@pytest.mark.asyncio
async def test_extract_commute_data_transit_only_fallback(hass: HomeAssistant):
    """Test commute extraction when only transit sensor is present."""
    hass.states.async_set("sensor.commute_metro", "30", {"line": "Silver Line"})
    step = {
        "name": "commute",
        "type": "commute",
        "commute_sensors": ["sensor.commute_metro"],
    }

    data = await async_extract_commute_data(hass, step)
    assert data["selected_mode"] == "transit"
    assert data["faster_mode"] == "transit"
    assert data["transit_time"] == 30
    assert data["drive_time"] is None
    assert data["diff_minutes"] is None
    assert data["transit_summary"] == "Silver Line"


@pytest.mark.asyncio
async def test_async_extract_entity_context_commute_integration(
    hass: HomeAssistant, tmp_path: Path
):
    """Test async_extract_entity_context extracts and attaches commute_data."""
    hass.states.async_set(
        "zone.test_office", "0", {"latitude": 40.1, "longitude": -75.1}
    )
    hass.states.async_set(
        "sensor.javan_commute_drive_to_work",
        "35",
        {"destination": "zone.test_office", "mode": "driving"},
    )
    hass.states.async_set(
        "sensor.javan_commute_metro",
        "44",
        {"line": "Silver Line"},
    )
    step = {
        "name": "commute",
        "type": "commute",
        "commute_sensors": [
            "sensor.javan_commute_drive_to_work",
            "sensor.javan_commute_metro",
        ],
    }

    with patch(
        "custom_components.cast_studio.generator.async_fetch_commute_map",
        return_value={
            "summary": "I-66 W",
            "distance": "24.5 mi",
            "map_image_path": str(tmp_path / "map.png"),
        },
    ):
        ctx = await async_extract_entity_context(hass, step)

    assert "card" in step
    assert "commute_data" in step["card"]
    assert step["card"]["commute_data"]["selected_mode"] == "driving"
    assert step["card"]["commute_data"]["drive_time"] == 35
    assert "Commute Comparison" in ctx


@pytest.mark.parametrize(
    ("kwargs", "expected_fragment"),
    [
        ({"condition": "rainy", "current_temp": 60}, "umbrella"),
        ({"condition": "sunny", "current_temp": 70, "precip_prob": 40}, "umbrella"),
        ({"condition": "cloudy", "current_temp": 45}, "coat"),
        ({"condition": "cloudy", "current_temp": 60, "high": 52}, "coat"),
        ({"condition": "sunny", "current_temp": 62, "high": 70}, "light jacket"),
        ({"condition": "sunny", "current_temp": 72, "low": 48}, "light jacket"),
        ({"condition": "sunny", "current_temp": 90}, "Dress light"),
        ({"condition": "sunny", "current_temp": 75, "high": 88}, "Dress light"),
        ({"condition": "sunny", "current_temp": 74, "high": 78}, "Comfortable"),
        ({"condition": "sunny", "current_temp": 90, "aqi": 150}, "air quality"),
        ({"condition": "rainy", "current_temp": 50, "aqi": 101}, "air quality"),
        (
            {"condition": "sunny", "current_temp": 30, "temperature_unit": "°C"},
            "Dress light",
        ),
        (
            {"condition": "sunny", "current_temp": 5, "temperature_unit": "°C"},
            "coat",
        ),
    ],
)
def test_compute_weather_advisory(kwargs: dict, expected_fragment: str) -> None:
    """Advisory covers rain, cold, cool, hot, comfortable, AQI and Celsius input."""
    assert expected_fragment.lower() in compute_weather_advisory(**kwargs).lower()


def test_compute_weather_advisory_no_data_returns_empty() -> None:
    """With no usable weather data no advice is invented."""
    assert compute_weather_advisory(condition="unknown") == ""
    assert (
        compute_weather_advisory(condition="unavailable", current_temp="unavailable")
        == ""
    )


def test_compute_weather_advisory_aqi_boundary_is_exclusive() -> None:
    """AQI of exactly 100 is not flagged; 101 is."""
    ok = compute_weather_advisory(condition="sunny", current_temp=72, aqi=100)
    bad = compute_weather_advisory(condition="sunny", current_temp=72, aqi=101)
    assert "air quality" not in ok.lower()
    assert "air quality" in bad.lower()


@pytest.mark.asyncio
async def test_extract_weather_data_actionable_advisory(hass: HomeAssistant) -> None:
    """async_extract_weather_data attaches an actionable advisory string."""
    hass.states.async_set(
        "weather.home",
        "rainy",
        {"temperature": 58, "precipitation_probability": 80},
    )
    data = await async_extract_weather_data(hass, "weather.home")
    assert "umbrella" in data["advisory"].lower()


@pytest.mark.asyncio
async def test_extract_weather_data_reads_aqi_from_weather_attributes(
    hass: HomeAssistant,
) -> None:
    """A weather entity exposing air_quality_index above 100 yields the AQI advisory."""
    hass.states.async_set(
        "weather.home",
        "sunny",
        {"temperature": 72, "air_quality_index": 160},
    )
    data = await async_extract_weather_data(hass, "weather.home")
    assert "air quality" in data["advisory"].lower()


@pytest.mark.asyncio
async def test_entity_context_for_weather_includes_advisory(
    hass: HomeAssistant,
) -> None:
    """The LLM context for a weather step carries the advisory to be spoken."""
    hass.states.async_set(
        "weather.home",
        "rainy",
        {"temperature": 58, "precipitation_probability": 80},
    )
    step = {"name": "weather", "entity_id": "weather.home"}
    context = await async_extract_entity_context(hass, step)
    assert "umbrella" in context.lower()


def test_is_step_image_dynamic_allowlist() -> None:
    """Dynamic Imagen: greeting, fun_fact, national_day only."""
    for name in ("greeting", "fun_fact", "national_day"):
        assert is_step_image_dynamic({"name": name}) is True
    for name in ("daily_encouragement", "weather", "agenda", "commute", "scripture"):
        assert is_step_image_dynamic({"name": name}) is False
    # Everything else (including custom steps) is static by default.
    assert is_step_image_dynamic({"name": "wind_down"}) is False


def test_daily_encouragement_never_skipped_when_scripture_absent() -> None:
    """daily_encouragement is never skipped, even when its scripture entity is absent."""
    from custom_components.cast_studio.generator import _is_unconfigured_optional_step

    mock_hass = MagicMock()
    mock_hass.states.get.return_value = None
    step = {"name": "daily_encouragement", "entity_id": "sensor.missing_daily_text"}
    assert _is_unconfigured_optional_step(mock_hass, step) is False
    assert (
        _is_unconfigured_optional_step(mock_hass, {"name": "daily_encouragement"})
        is False
    )


@pytest.mark.asyncio
async def test_national_day_image_prompt_includes_holiday_name(tmp_path: Path) -> None:
    """The national_day Imagen prompt is built from the day's holiday name."""
    from custom_components.cast_studio.generator import (
        _async_resolve_step_visual_assets,
    )

    def _gen(_h: Any, _prompt: str, path: str, *, ai_creds: Any = None) -> str:
        Path(path).write_bytes(b"img")
        return path

    card: dict[str, Any] = {"title": "National Pancake Day"}
    with patch(
        "custom_components.cast_studio.generator.async_generate_ai_image",
        new=AsyncMock(side_effect=_gen),
    ) as mock_gen:
        await _async_resolve_step_visual_assets(
            MagicMock(),
            {"name": "national_day"},
            "national_day",
            card,
            {"platform": "gemini", "api_key": "k"},
            scratch_dir=str(tmp_path),
        )
    assert "National Pancake Day" in mock_gen.call_args.args[1]
    assert card["image_type"] == "dynamic"


def test_step_prompt_guidelines_encouragement_and_weather_advice() -> None:
    """Prompt guidance asks for weather advice and a jargon-free encouragement."""
    from custom_components.cast_studio.ai_client import STEP_PROMPT_GUIDELINES

    weather = STEP_PROMPT_GUIDELINES["weather"].lower()
    assert "umbrella" in weather
    assert "jacket" in weather
    enc = STEP_PROMPT_GUIDELINES["daily_encouragement"]
    assert "blessed" in enc  # instructs the model to avoid the word
    assert "do not" in enc.lower() or "never" in enc.lower()
    assert "exactly 2" in enc.lower()
    assert "have a great day" not in STEP_PROMPT_GUIDELINES["scripture"].lower()


@pytest.mark.asyncio
async def test_compile_single_step_sets_daily_encouragement_signoff(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """The renderer card gets a deterministic 'Have a great day, <name>!' sign-off."""
    from custom_components.cast_studio.generator import _async_compile_single_step

    mock_hass = MagicMock(spec=hass)
    step = {"name": "daily_encouragement", "type": "daily_encouragement"}
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}
    audio_target = str(tmp_path / "audio.wav")
    slide_target = str(tmp_path / "slide.jpg")

    with (
        patch(
            "custom_components.cast_studio.generator.async_generate_step_content",
            new=AsyncMock(
                return_value={
                    "visual_card": {
                        "title": "Take it one step at a time.",
                        "bullet_points": ["One", "Two"],
                        "host_signoff": "Have a blessed day!",
                    },
                    "narration_script": "Take it one step at a time.",
                }
            ),
        ),
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value=audio_target),
        ),
        patch(
            "custom_components.cast_studio.generator.render_slide",
            return_value=slide_target,
        ) as mock_render,
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.cast_studio.generator.async_generate_ai_image",
            new=AsyncMock(return_value=None),
        ),
    ):
        await _async_compile_single_step(
            mock_hass,
            step,
            "daily_encouragement",
            recipient,
            {"platform": "mock"},
            is_visual=True,
            default_pad_seconds=0.5,
            audio_target_path=audio_target,
            slide_target_path=slide_target,
            clip_target_path=str(tmp_path / "clip.mp4"),
        )

    card = mock_render.call_args[0][0]
    assert card["host_signoff"] == "Have a great day, Javan!"


async def _run_compile_step(
    tmp_path: Path,
    step: dict[str, Any],
    llm_content: dict[str, Any],
    recipient: dict[str, Any],
    ai_creds: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """Run the real _async_compile_single_step with a mocked LLM; return card, TTS text."""
    from custom_components.cast_studio.generator import _async_compile_single_step

    mock_hass = MagicMock()
    mock_hass.states.get.return_value = None
    audio_target = str(tmp_path / "audio.wav")
    slide_target = str(tmp_path / "slide.jpg")
    tts = AsyncMock(return_value=audio_target)

    def _img(_h: Any, _prompt: str, path: str, *, ai_creds: Any = None) -> str:
        Path(path).write_bytes(b"img")
        return path

    with (
        patch(
            "custom_components.cast_studio.generator.async_generate_step_content",
            new=AsyncMock(return_value=llm_content),
        ),
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech", new=tts
        ),
        patch(
            "custom_components.cast_studio.generator.render_slide",
            return_value=slide_target,
        ) as mock_render,
        patch(
            "custom_components.cast_studio.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.cast_studio.generator.async_generate_ai_image",
            new=AsyncMock(side_effect=_img),
        ),
        patch(
            "custom_components.cast_studio.generator.get_audio_duration",
            return_value=3.0,
        ),
    ):
        await _async_compile_single_step(
            mock_hass,
            step,
            step["name"],
            recipient,
            ai_creds or {"platform": "mock"},
            is_visual=True,
            default_pad_seconds=0.5,
            audio_target_path=audio_target,
            slide_target_path=slide_target,
            clip_target_path=str(tmp_path / "clip.mp4"),
        )
    return mock_render.call_args[0][0], tts.call_args[0][1]


@pytest.mark.asyncio
async def test_compile_single_step_preserves_weather_advisory(tmp_path: Path) -> None:
    """The weather advisory on the step card reaches the rendered card_data."""
    step = {
        "name": "weather",
        "type": "weather",
        "card": {"weather_data": {"advisory": "Pack an umbrella for rain today"}},
    }
    card, _ = await _run_compile_step(
        tmp_path,
        step,
        {"visual_card": {"title": "Weather"}, "narration_script": "Rainy today."},
        {"name": "Javan"},
    )
    assert card["weather_data"]["advisory"] == "Pack an umbrella for rain today"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("narration", "expected"),
    [
        ("Be patient today.", "Be patient today. Have a great day, Jay-ven!"),
        (
            "Be patient today. Have a wonderful day!",
            "Be patient today. Have a great day, Jay-ven!",
        ),
        (
            "Be patient today. Have a great day, Jay-ven!",
            "Be patient today. Have a great day, Jay-ven!",
        ),
    ],
)
async def test_daily_encouragement_narration_always_ends_with_signoff(
    tmp_path: Path, narration: str, expected: str
) -> None:
    """Spoken narration is forced to end with the sign-off (phonetic name for TTS)."""
    card, spoken = await _run_compile_step(
        tmp_path,
        {"name": "daily_encouragement", "type": "daily_encouragement"},
        {
            "visual_card": {
                "title": "Be patient",
                "host_signoff": "Have a blessed day!",
            },
            "narration_script": narration,
        },
        {"name": "Javan", "phonetic_name": "Jay-ven"},
    )
    assert spoken == expected
    assert card["host_signoff"] == "Have a great day, Javan!"


@pytest.mark.asyncio
async def test_daily_encouragement_without_scripture_runs_general_path(
    tmp_path: Path,
) -> None:
    """No/unavailable scripture sensor: step still runs, gets sign-off and backdrop."""
    from types import SimpleNamespace

    from custom_components.cast_studio.generator import (
        NO_DAILY_TEXT_CONTEXT,
        async_extract_entity_context,
        get_executable_steps,
    )

    unavailable = {
        "sensor.daily_text": SimpleNamespace(
            entity_id="sensor.daily_text", state="unavailable", attributes={}
        )
    }
    for step in (
        {"name": "daily_encouragement", "visual": True},
        {
            "name": "daily_encouragement",
            "visual": True,
            "entity_id": "sensor.daily_text",
        },
    ):
        hass = MagicMock()
        hass.states.get.side_effect = unavailable.get
        assert get_executable_steps(hass, {"visuals": True, "steps": [step]}) == [step]
        assert await async_extract_entity_context(hass, dict(step)) == (
            NO_DAILY_TEXT_CONTEXT
        )

    card, spoken = await _run_compile_step(
        tmp_path,
        {"name": "daily_encouragement", "type": "daily_encouragement"},
        {
            "visual_card": {"title": "Make today a good one"},
            "narration_script": "A small kind act can change your whole day.",
        },
        {"name": "Javan"},
        {"platform": "gemini", "api_key": "k"},
    )
    assert spoken.endswith("Have a great day, Javan!")
    assert card["host_signoff"] == "Have a great day, Javan!"
    # No AI backdrop for daily_encouragement (local layout in the renderer).
    assert card["image_type"] == "static"


@pytest.mark.asyncio
async def test_daily_encouragement_with_scripture_uses_scripture_context() -> None:
    """With a live scripture sensor the real daily text is passed to the LLM."""
    from types import SimpleNamespace

    from custom_components.cast_studio.generator import async_extract_entity_context

    hass = MagicMock()
    hass.states.get.side_effect = {
        "sensor.daily_text": SimpleNamespace(
            entity_id="sensor.daily_text",
            state="1 John 4:8",
            attributes={"text": "God is love"},
        )
    }.get
    ctx = await async_extract_entity_context(
        hass, {"name": "daily_encouragement", "entity_id": "sensor.daily_text"}
    )
    assert "1 John 4:8" in ctx or "God is love" in ctx
    assert "No daily scripture" not in ctx


def test_daily_encouragement_prompt_requires_exact_signoff() -> None:
    """The LLM prompt tells the model the exact closing line with the real name."""
    from custom_components.cast_studio.ai_client import _build_prompt

    prompt = _build_prompt(
        {"name": "daily_encouragement", "type": "daily_encouragement"},
        {"name": "Javan", "phonetic_name": "Jay-ven"},
    )
    assert '"Have a great day, Jay-ven!"' in prompt
    assert "under 30 words" in prompt


@pytest.mark.asyncio
async def test_greeting_image_regenerated_on_every_run(tmp_path: Path) -> None:
    """Greeting never reuses a previous image: each compile calls Imagen again."""
    from custom_components.cast_studio.generator import (
        _async_resolve_step_visual_assets,
    )

    def _gen(_h: Any, _prompt: str, path: str, *, ai_creds: Any = None) -> str:
        Path(path).write_bytes(b"img")
        return path

    with patch(
        "custom_components.cast_studio.generator.async_generate_ai_image",
        new=AsyncMock(side_effect=_gen),
    ) as mock_gen:
        for _ in range(2):
            card: dict[str, Any] = {}
            await _async_resolve_step_visual_assets(
                MagicMock(),
                {"name": "greeting"},
                "greeting",
                card,
                {"platform": "gemini", "api_key": "k"},
                scratch_dir=str(tmp_path),
            )
            assert card["image_type"] == "dynamic"
    assert mock_gen.call_count == 2


@pytest.mark.asyncio
async def test_fun_fact_image_prompt_grounded_in_fact_content(tmp_path: Path) -> None:
    """The fun_fact Imagen prompt is built from the generated fact, not a theme."""
    from custom_components.cast_studio.generator import (
        _async_resolve_step_visual_assets,
    )

    def _gen(_h: Any, _prompt: str, path: str, *, ai_creds: Any = None) -> str:
        Path(path).write_bytes(b"img")
        return path

    card: dict[str, Any] = {
        "title": "Octopuses have three hearts",
        "subtitle": "Fun Fact",
        "bullet_points": ["Two pump blood to the gills"],
    }
    with patch(
        "custom_components.cast_studio.generator.async_generate_ai_image",
        new=AsyncMock(side_effect=_gen),
    ) as mock_gen:
        await _async_resolve_step_visual_assets(
            MagicMock(),
            {"name": "fun_fact", "subtitle": "Fun Fact"},
            "fun_fact",
            card,
            {"platform": "gemini", "api_key": "k"},
            scratch_dir=str(tmp_path),
            narration="Did you know an octopus has three hearts?",
        )
    prompt = mock_gen.call_args.args[1]
    assert "octopus" in prompt.lower()
    assert "gills" in prompt.lower()
    assert "Fun Fact" not in prompt

    # Falls back to the narration when the card carries no title or bullets.
    card2: dict[str, Any] = {}
    with patch(
        "custom_components.cast_studio.generator.async_generate_ai_image",
        new=AsyncMock(side_effect=_gen),
    ) as mock_gen:
        await _async_resolve_step_visual_assets(
            MagicMock(),
            {"name": "fun_fact"},
            "fun_fact",
            card2,
            {"platform": "gemini", "api_key": "k"},
            scratch_dir=str(tmp_path),
            narration="Octopus hearts are fascinating.",
        )
    assert "octopus" in mock_gen.call_args.args[1].lower()


# --- Final-review fixes: commute destination, fetch failures, ETA, sign-off ---

FAKE_HOME = (40.0, -75.0)
FAKE_WORK = (40.1, -75.1)


class _FakeGetContext:
    """Async context manager returned by a fake aiohttp session.get()."""

    def __init__(self, resp: Any = None, exc: BaseException | None = None) -> None:
        self.resp = resp
        self.exc = exc

    async def __aenter__(self) -> Any:
        if self.exc is not None:
            raise self.exc
        return self.resp

    async def __aexit__(self, *_args: object) -> None:
        return None


def _directions_ok_response() -> AsyncMock:
    resp = AsyncMock()
    resp.status = 200
    resp.json = AsyncMock(
        return_value={
            "status": "OK",
            "routes": [
                {
                    "summary": "Test Pike",
                    "overview_polyline": {"points": "abc"},
                    "legs": [
                        {
                            "distance": {"text": "10 mi"},
                            "duration": {"text": "20 mins", "value": 1200},
                        }
                    ],
                }
            ],
        }
    )
    return resp


def _set_fake_home(hass: HomeAssistant) -> None:
    hass.states.async_remove("zone.home")
    hass.config.latitude, hass.config.longitude = FAKE_HOME


@pytest.mark.asyncio
async def test_extract_commute_data_without_destination_skips_map_fetch(
    hass: HomeAssistant,
) -> None:
    """No configured destination: no Google call, procedural map (C1)."""
    _set_fake_home(hass)
    hass.states.async_set("sensor.commute_drive", "30", {"mode": "driving"})
    step = {"name": "commute", "commute_sensors": ["sensor.commute_drive"]}

    with patch(
        "custom_components.cast_studio.generator.async_fetch_commute_map",
        new=AsyncMock(return_value={"map_image_path": "/nope.png"}),
    ) as mock_fetch:
        data = await async_extract_commute_data(hass, step)

    mock_fetch.assert_not_called()
    assert data["map_image_path"] is None
    assert data["drive_time"] == 30


@pytest.mark.asyncio
async def test_extract_commute_data_uses_configured_destination_zone(
    hass: HomeAssistant,
) -> None:
    """The commute_destination_zone option selects the destination zone (C1)."""
    _set_fake_home(hass)
    hass.states.async_set(
        "zone.fake_office", "0", {"latitude": 40.1, "longitude": -75.1}
    )
    MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={CONF_COMMUTE_DESTINATION: "zone.fake_office"},
    ).add_to_hass(hass)
    hass.states.async_set("sensor.commute_drive", "30", {"mode": "driving"})
    step = {"name": "commute", "commute_sensors": ["sensor.commute_drive"]}

    with patch(
        "custom_components.cast_studio.generator.async_fetch_commute_map",
        new=AsyncMock(return_value=None),
    ) as mock_fetch:
        await async_extract_commute_data(hass, step)

    mock_fetch.assert_awaited_once()
    args = mock_fetch.await_args.args
    assert args[1] == FAKE_HOME
    assert args[2] == FAKE_WORK


@pytest.mark.asyncio
async def test_extract_commute_data_destination_lat_lon_attribute(
    hass: HomeAssistant,
) -> None:
    """A "lat,lon" sensor destination attribute is accepted (C1)."""
    _set_fake_home(hass)
    hass.states.async_set(
        "sensor.commute_drive", "30", {"mode": "driving", "destination": "40.1,-75.1"}
    )
    step = {"name": "commute", "commute_sensors": ["sensor.commute_drive"]}

    with patch(
        "custom_components.cast_studio.generator.async_fetch_commute_map",
        new=AsyncMock(return_value=None),
    ) as mock_fetch:
        await async_extract_commute_data(hass, step)

    assert mock_fetch.await_args.args[2] == FAKE_WORK


@pytest.mark.asyncio
async def test_extract_commute_data_step_destination_and_zone_home_origin(
    hass: HomeAssistant,
) -> None:
    """Step destination_zone wins; zone.home is the origin when it has coords."""
    hass.states.async_set("zone.home", "0", {"latitude": 40.0, "longitude": -75.0})
    hass.states.async_set("zone.step_dest", "0", {"latitude": 40.2, "longitude": -75.2})
    hass.states.async_set("zone.work", "0", {"latitude": 40.1, "longitude": -75.1})
    hass.states.async_set("sensor.commute_drive", "30", {"mode": "driving"})
    step = {
        "name": "commute",
        "commute_sensors": ["sensor.commute_drive"],
        "destination_zone": "zone.step_dest",
    }

    with patch(
        "custom_components.cast_studio.generator.async_fetch_commute_map",
        new=AsyncMock(return_value=None),
    ) as mock_fetch:
        await async_extract_commute_data(hass, step)

    assert mock_fetch.await_args.args[1] == FAKE_HOME
    assert mock_fetch.await_args.args[2] == (40.2, -75.2)


@pytest.mark.asyncio
async def test_fetch_commute_map_requests_size_within_google_limit(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Static Maps is asked for 512x300 at scale 2, i.e. exactly 1024x600 (I1)."""
    static_resp = AsyncMock()
    static_resp.status = 200
    static_resp.read = AsyncMock(return_value=b"png")
    seen: dict[str, Any] = {}

    def fake_get(url: str, params: Any = None) -> _FakeGetContext:
        if "directions" in url:
            return _FakeGetContext(_directions_ok_response())
        seen["params"] = params
        return _FakeGetContext(static_resp)

    session = MagicMock()
    session.get.side_effect = fake_get
    with patch(
        "custom_components.cast_studio.generator.async_get_clientsession",
        return_value=session,
    ):
        result = await async_fetch_commute_map(
            hass, FAKE_HOME, FAKE_WORK, "driving", "k", str(tmp_path)
        )

    assert result is not None
    params = dict(p for p in seen["params"] if p[0] in ("size", "scale"))
    assert params == {"size": "512x300", "scale": "2"}
    width, height = (int(v) for v in params["size"].split("x"))
    assert width <= 640
    assert height <= 640


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [TimeoutError(), aiohttp.ClientConnectionError("boom")],
    ids=["timeout", "network_error"],
)
async def test_fetch_commute_map_directions_timeout_or_network_error(
    hass: HomeAssistant, tmp_path: Path, exc: BaseException
) -> None:
    """A Directions timeout or network error returns None (I6)."""
    session = MagicMock()
    session.get.return_value = _FakeGetContext(exc=exc)
    with patch(
        "custom_components.cast_studio.generator.async_get_clientsession",
        return_value=session,
    ):
        result = await async_fetch_commute_map(
            hass, FAKE_HOME, FAKE_WORK, "driving", "k", str(tmp_path)
        )
    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "static_ctx",
    [
        "http_500",
        "timeout",
    ],
)
async def test_fetch_commute_map_static_maps_failure_after_directions_ok(
    hass: HomeAssistant, tmp_path: Path, static_ctx: str
) -> None:
    """Static Maps non-200 or timeout after Directions succeeds returns None (I6)."""
    static_resp = AsyncMock()
    static_resp.status = 500
    static_resp.read = AsyncMock(return_value=b"")

    def fake_get(url: str, params: Any = None) -> _FakeGetContext:
        if "directions" in url:
            return _FakeGetContext(_directions_ok_response())
        if static_ctx == "timeout":
            return _FakeGetContext(exc=TimeoutError())
        return _FakeGetContext(static_resp)

    session = MagicMock()
    session.get.side_effect = fake_get
    with patch(
        "custom_components.cast_studio.generator.async_get_clientsession",
        return_value=session,
    ):
        result = await async_fetch_commute_map(
            hass, FAKE_HOME, FAKE_WORK, "driving", "k", str(tmp_path)
        )
    assert result is None
    assert not any((tmp_path / "cache").glob("*.png"))


@pytest.mark.asyncio
async def test_extract_commute_data_eta_uses_ha_timezone(
    hass: HomeAssistant, freezer: Any
) -> None:
    """The clock ETA is formatted in HA's configured local timezone (I6)."""
    await hass.config.async_set_time_zone("America/Los_Angeles")
    freezer.move_to("2026-10-20 14:00:00+00:00")  # 07:00 PDT
    hass.states.async_set("sensor.commute_drive", "35", {"mode": "driving"})
    step = {"name": "commute", "commute_sensors": ["sensor.commute_drive"]}

    data = await async_extract_commute_data(hass, step)

    assert data["eta"] == "7:35 AM"
    assert data["eta_is_relative"] is False


@pytest.mark.asyncio
async def test_extract_commute_data_relative_eta_for_pregeneration(
    hass: HomeAssistant,
) -> None:
    """Pregenerated compiles get a relative trip length, not a clock time (I4)."""
    hass.states.async_set("sensor.commute_drive", "35", {"mode": "driving"})
    step = {
        "name": "commute",
        "commute_sensors": ["sensor.commute_drive"],
        "relative_eta": True,
    }

    ctx = await async_extract_entity_context(hass, step)
    data = step["card"]["commute_data"]

    assert data["eta"] == "35 min trip"
    assert data["eta_is_relative"] is True
    assert "about 35 minutes" in ctx
    assert "AM" not in ctx
    assert "PM" not in ctx


@pytest.mark.asyncio
async def test_extract_entity_context_live_commute_hint_has_clock_eta(
    hass: HomeAssistant, freezer: Any
) -> None:
    """Live compiles tell the narration the same clock ETA the HUD shows (I4)."""
    await hass.config.async_set_time_zone("America/Los_Angeles")
    freezer.move_to("2026-10-20 14:00:00+00:00")
    hass.states.async_set("sensor.commute_drive", "35", {"mode": "driving"})
    step = {"name": "commute", "commute_sensors": ["sensor.commute_drive"]}

    ctx = await async_extract_entity_context(hass, step)

    assert "Commute (tell the listener): driving" in ctx
    assert "arriving around 7:35 AM" in ctx


@pytest.mark.asyncio
async def test_extract_commute_data_all_sensors_unavailable_has_no_eta(
    hass: HomeAssistant,
) -> None:
    """Both sensors unavailable: no '0 min' ETA and no narration hint (I3)."""
    hass.states.async_set("sensor.commute_drive", "unavailable", {"mode": "driving"})
    hass.states.async_set("sensor.commute_metro", "unknown", {"line": "Test Line"})
    step = {
        "name": "commute",
        "commute_sensors": ["sensor.commute_drive", "sensor.commute_metro"],
    }

    ctx = await async_extract_entity_context(hass, step)
    data = step["card"]["commute_data"]

    assert data["eta"] == ""
    assert data["selected_time"] is None
    assert data["drive_time"] is None
    assert data["transit_time"] is None
    assert "Commute (tell the listener)" not in ctx


@pytest.mark.asyncio
async def test_compile_routine_media_relative_eta_marks_steps(tmp_path: Path) -> None:
    """relative_eta=True is propagated onto every compiled step (I4)."""
    seen_steps: list[dict[str, Any]] = []

    async def fake_ctx(_hass: Any, step: dict[str, Any]) -> str:
        seen_steps.append(dict(step))
        return ""

    hass = MagicMock()
    routine_config = {"visuals": False, "steps": [{"name": "commute"}]}
    with (
        patch(
            "custom_components.cast_studio.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "custom_components.cast_studio.generator.get_executable_steps",
            return_value=routine_config["steps"],
        ),
        patch(
            "custom_components.cast_studio.generator.async_extract_entity_context",
            new=fake_ctx,
        ),
        patch(
            "custom_components.cast_studio.generator._async_compile_single_step",
            new=AsyncMock(return_value=("a.wav", 1.0, 0.0, {})),
        ),
        patch(
            "custom_components.cast_studio.generator._async_finalize_routine_media",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "custom_components.cast_studio.generator._async_run_in_executor",
            new=AsyncMock(return_value=str(tmp_path)),
        ),
    ):
        await async_compile_routine_media(
            hass,
            "morning",
            routine_config,
            {"name": "A"},
            str(tmp_path),
            relative_eta=True,
        )
        await async_compile_routine_media(
            hass, "morning", routine_config, {"name": "A"}, str(tmp_path)
        )

    assert seen_steps[0].get("relative_eta") is True
    assert "relative_eta" not in seen_steps[1]


@pytest.mark.parametrize(
    ("narration", "expected"),
    [
        (
            "Take it slow. I hope you have a wonderful day!",
            "Take it slow. Have a great day, Jay-ven!",
        ),
        (
            "Keep moving, and have a good day!",
            "Keep moving, and have a good day! Have a great day, Jay-ven!",
        ),
        (
            "Be kind. I hope you have a great day, Javan!",
            "Be kind. Have a great day, Jay-ven!",
        ),
        (
            "Be kind. Have a great day, Javan!",
            "Be kind. Have a great day, Jay-ven!",
        ),
        ("Be kind. Have a blessed day ahead.", "Be kind. Have a great day, Jay-ven!"),
        (
            "Rest when you need to",
            "Rest when you need to. Have a great day, Jay-ven!",
        ),
        (
            "Remember that you have a day full of chances. Go well.",
            "Remember that you have a day full of chances. Go well. "
            "Have a great day, Jay-ven!",
        ),
        (
            "Be kind. Have a great day, Jay-ven!",
            "Be kind. Have a great day, Jay-ven!",
        ),
    ],
)
def test_ensure_encouragement_signoff_never_cuts_a_sentence(
    narration: str, expected: str
) -> None:
    """Only a whole generic send-off sentence is replaced; nothing is cut (I5)."""
    result = _ensure_encouragement_signoff(
        narration, {"name": "Javan", "phonetic_name": "Jay-ven"}
    )
    assert result == expected
    # Every sentence of the result is whole: no dangling "you." / "and." fragments.
    assert " you." not in result
    assert " and." not in result
    assert result.count("Have a great day, Jay-ven!") == 1
