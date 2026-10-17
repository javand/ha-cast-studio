"""Tests for the media generator engine."""

from __future__ import annotations

import json
import wave
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.automation_generator.generator import (
    GeneratorError,
    RoutineConfigError,
    _extract_entity_context,
    _is_unconfigured_optional_step,
    async_compile_routine_media,
    get_audio_duration,
)

if TYPE_CHECKING:
    from pathlib import Path

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
            "custom_components.automation_generator.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.automation_generator.generator.async_generate_step_content",
            new=AsyncMock(
                return_value={"visual_card": {"title": "T"}, "narration_script": "S"}
            ),
        ),
        patch(
            "custom_components.automation_generator.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.automation_generator.generator.render_slide",
            return_value="slide.jpg",
        ),
        patch(
            "custom_components.automation_generator.generator.async_run_ffmpeg",
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
            "custom_components.automation_generator.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.automation_generator.generator.async_generate_step_content",
            new=AsyncMock(
                return_value={
                    "visual_card": {"title": "Slide Title"},
                    "narration_script": "Spoken text",
                }
            ),
        ),
        patch(
            "custom_components.automation_generator.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.automation_generator.generator.render_slide",
            return_value="slide.jpg",
        ),
        patch(
            "custom_components.automation_generator.generator.async_run_ffmpeg",
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
            "custom_components.automation_generator.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.automation_generator.generator.async_generate_step_content",
            mock_generate,
        ),
        patch(
            "custom_components.automation_generator.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.automation_generator.generator.render_slide",
            return_value="slide.jpg",
        ),
        patch(
            "custom_components.automation_generator.generator.async_run_ffmpeg",
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
            "custom_components.automation_generator.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.automation_generator.generator.async_generate_step_content",
            new=AsyncMock(
                return_value={"visual_card": {"title": "T"}, "narration_script": "S"}
            ),
        ),
        patch(
            "custom_components.automation_generator.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.automation_generator.generator.render_slide",
            return_value="slide.jpg",
        ),
        patch(
            "custom_components.automation_generator.generator.async_run_ffmpeg",
            new=AsyncMock(return_value=False),  # Simulating FFmpeg failure
        ),
        patch(
            "custom_components.automation_generator.generator.cleanup_scratch_dir"
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
            "custom_components.automation_generator.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.automation_generator.generator.async_generate_step_content",
            new=AsyncMock(
                return_value={"visual_card": {}, "narration_script": "Audio text"}
            ),
        ),
        patch(
            "custom_components.automation_generator.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.automation_generator.generator.render_slide",
            mock_render,
        ),
        patch(
            "custom_components.automation_generator.generator.async_run_ffmpeg",
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
            "custom_components.automation_generator.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.automation_generator.generator.async_generate_step_content",
            new=AsyncMock(
                return_value={"visual_card": {"title": "T"}, "narration_script": "S"}
            ),
        ),
        patch(
            "custom_components.automation_generator.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.automation_generator.generator.render_slide",
            return_value="slide.jpg",
        ),
        patch(
            "custom_components.automation_generator.generator.async_run_ffmpeg",
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
    hass.states.get = MagicMock(side_effect=lambda eid: state_map.get(eid))

    step = {
        "name": "daily_overview",
        "entity_ids": ["weather.home", "calendar.work", "todo.groceries", "sensor.power"],
        "context": "Focus on high priority tasks",
    }

    ctx = _extract_entity_context(hass, step)
    assert "Focus on high priority tasks" in ctx
    assert "Weather (weather.home): Condition: partlycloudy, Temperature: 72°F" in ctx
    assert "Calendar (calendar.work): State: on, Summary: Team Standup, Start: 09:00:00" in ctx
    assert "To-do (todo.groceries): 4 tasks pending" in ctx
    assert "Sensor (sensor.power): 23.5 kWh" in ctx


def test_is_unconfigured_optional_step_matrix():
    """Test _is_unconfigured_optional_step identifies unconfigured vs valid steps."""
    hass = MagicMock()
    mock_state = MagicMock(state="sunny")
    hass.states.get = MagicMock(side_effect=lambda eid: mock_state if eid == "weather.home" else None)

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
    hass.states.get = MagicMock(return_value=None)  # task list entity not found in states

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
            "custom_components.automation_generator.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.automation_generator.generator.async_generate_step_content",
            new=AsyncMock(
                return_value={"visual_card": {"title": "T"}, "narration_script": "S"}
            ),
        ) as mock_generate,
        patch(
            "custom_components.automation_generator.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.automation_generator.generator.render_slide",
            return_value="slide.jpg",
        ),
        patch(
            "custom_components.automation_generator.generator.async_run_ffmpeg",
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
            "custom_components.automation_generator.generator.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
        patch(
            "custom_components.automation_generator.generator.async_generate_step_content",
            new=AsyncMock(
                return_value={"visual_card": {"title": "T"}, "narration_script": "S"}
            ),
        ) as mock_generate,
        patch(
            "custom_components.automation_generator.generator.async_synthesize_speech",
            new=AsyncMock(return_value="audio.wav"),
        ),
        patch(
            "custom_components.automation_generator.generator.render_slide",
            return_value="slide.jpg",
        ),
        patch(
            "custom_components.automation_generator.generator.async_run_ffmpeg",
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
