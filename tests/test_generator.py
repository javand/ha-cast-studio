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
