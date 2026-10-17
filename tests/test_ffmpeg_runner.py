"""Tests for FFmpeg clip muxing and concatenation runner."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.automation_generator.ffmpeg_runner import (
    FFmpegExecutionError,
    async_run_ffmpeg,
    build_concat_command,
    build_slide_clip_command,
    write_concat_file,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_build_slide_clip_command():
    """Verify build_slide_clip_command generates proper ffmpeg CLI flags verbatim."""
    cmd = build_slide_clip_command("slide.jpg", "audio.wav", "clip.mp4")
    assert "ffmpeg" in cmd[0]
    assert "-loop" in cmd
    assert "slide.jpg" in cmd
    assert "audio.wav" in cmd
    assert "-c:v" in cmd
    assert "libx264" in cmd
    assert "-pix_fmt" in cmd
    assert "yuv420p" in cmd
    assert cmd[-1] == "clip.mp4"


def test_build_concat_command():
    """Verify build_concat_command generates proper ffmpeg concat flags verbatim."""
    cmd = build_concat_command("clips.txt", "final.mp4")
    assert "-f" in cmd
    assert "concat" in cmd
    assert "-c" in cmd
    assert "copy" in cmd
    assert cmd[-1] == "final.mp4"


def test_build_slide_clip_command_detailed_arguments(tmp_path: Path):
    """Verify all exact CLI arguments for slide muxing including tune and audio bitrate."""
    image_file = str(tmp_path / "slide_0.jpg")
    audio_file = str(tmp_path / "audio_0.wav")
    output_file = str(tmp_path / "output_0.mp4")

    cmd = build_slide_clip_command(
        image_file,
        audio_file,
        output_file,
        ffmpeg_bin="/usr/bin/ffmpeg",
    )
    assert cmd[0] == "/usr/bin/ffmpeg"
    assert "-y" in cmd
    assert "-tune" in cmd
    tune_idx = cmd.index("-tune")
    assert cmd[tune_idx + 1] == "stillimage"
    assert "-c:a" in cmd
    ca_idx = cmd.index("-c:a")
    assert cmd[ca_idx + 1] == "aac"
    assert "-b:a" in cmd
    ba_idx = cmd.index("-b:a")
    assert cmd[ba_idx + 1] == "192k"
    assert "-shortest" in cmd
    assert cmd[-1] == output_file


def test_build_slide_clip_command_validation():
    """Verify validation on empty or whitespace inputs."""
    with pytest.raises(ValueError, match="image_path"):
        build_slide_clip_command("", "audio.wav", "clip.mp4")
    with pytest.raises(ValueError, match="audio_path"):
        build_slide_clip_command("slide.jpg", "   ", "clip.mp4")
    with pytest.raises(ValueError, match="output_clip_path"):
        build_slide_clip_command("slide.jpg", "audio.wav", "")


def test_build_concat_command_detailed_arguments(tmp_path: Path):
    """Verify safe 0 flag, overwrite flag, and custom ffmpeg binary."""
    concat_file = str(tmp_path / "concat.txt")
    final_file = str(tmp_path / "final.mp4")

    cmd = build_concat_command(
        concat_file,
        final_file,
        ffmpeg_bin="/opt/homebrew/bin/ffmpeg",
    )
    assert cmd[0] == "/opt/homebrew/bin/ffmpeg"
    assert "-y" in cmd
    assert "-safe" in cmd
    safe_idx = cmd.index("-safe")
    assert cmd[safe_idx + 1] == "0"
    assert "-i" in cmd
    i_idx = cmd.index("-i")
    assert cmd[i_idx + 1] == concat_file
    assert cmd[-1] == final_file


def test_build_concat_command_validation():
    """Verify validation on empty inputs for build_concat_command."""
    with pytest.raises(ValueError, match="concat_list_file"):
        build_concat_command("", "final.mp4")
    with pytest.raises(ValueError, match="output_mp4_path"):
        build_concat_command("concat.txt", "   ")


def test_write_concat_file(tmp_path: Path):
    """Verify writing FFmpeg concat demuxer manifest file."""
    list_file = tmp_path / "scratch" / "clips.txt"
    clips = [
        str(tmp_path / "clip 1.mp4"),
        str(tmp_path / "clip'2.mp4"),
        str(tmp_path / "clip3.mp4"),
    ]
    result_path = write_concat_file(clips, str(list_file))
    assert result_path == str(list_file)
    assert list_file.is_file()

    content = list_file.read_text(encoding="utf-8")
    expected_lines = [
        f"file '{clips[0]}'",
        f"file '{tmp_path}/clip'\\''2.mp4'",
        f"file '{clips[2]}'",
    ]
    assert content == "\n".join(expected_lines) + "\n"


def test_write_concat_file_validation(tmp_path: Path):
    """Verify write_concat_file validates inputs."""
    with pytest.raises(ValueError, match="clip_paths"):
        write_concat_file([], str(tmp_path / "test.txt"))
    with pytest.raises(ValueError, match="concat_file_path"):
        write_concat_file(["clip1.mp4"], "")


@pytest.mark.asyncio
async def test_async_run_ffmpeg_success():
    """Verify successful execution returns True when exit code is 0."""
    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(return_value=(b"stdout info", b""))

    cmd = ["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"]
    hass = MagicMock()

    with patch(
        "custom_components.automation_generator.ffmpeg_runner._spawn_process",
        new=AsyncMock(return_value=mock_process),
    ) as mock_spawn:
        result = await async_run_ffmpeg(hass, cmd)
        assert result is True
        mock_spawn.assert_called_once_with(cmd)


@pytest.mark.asyncio
async def test_async_run_ffmpeg_failure_returns_false():
    """Verify non-zero exit code logs error and returns False by default."""
    mock_process = AsyncMock()
    mock_process.returncode = 1
    mock_process.communicate = AsyncMock(
        return_value=(b"", b"Error initializing output stream")
    )

    hass = MagicMock()
    cmd = ["ffmpeg", "-i", "bad.jpg"]

    with patch(
        "custom_components.automation_generator.ffmpeg_runner._spawn_process",
        new=AsyncMock(return_value=mock_process),
    ):
        result = await async_run_ffmpeg(hass, cmd)
        assert result is False


@pytest.mark.asyncio
async def test_async_run_ffmpeg_failure_raises_error():
    """Verify non-zero exit code raises FFmpegExecutionError when raise_on_error=True."""
    mock_process = AsyncMock()
    mock_process.returncode = 127
    mock_process.communicate = AsyncMock(return_value=(b"", b"Unrecognized option"))

    hass = MagicMock()
    cmd = ["ffmpeg", "-invalid"]

    with (
        patch(
            "custom_components.automation_generator.ffmpeg_runner._spawn_process",
            new=AsyncMock(return_value=mock_process),
        ),
        pytest.raises(FFmpegExecutionError, match="Unrecognized option"),
    ):
        await async_run_ffmpeg(hass, cmd, raise_on_error=True)


@pytest.mark.asyncio
async def test_async_run_ffmpeg_file_not_found():
    """Verify FileNotFoundError handling when binary is missing."""
    hass = MagicMock()
    cmd = ["nonexistent_ffmpeg"]

    with patch(
        "custom_components.automation_generator.ffmpeg_runner._spawn_process",
        side_effect=FileNotFoundError("No such file or directory"),
    ):
        result = await async_run_ffmpeg(hass, cmd)
        assert result is False

        with pytest.raises(FFmpegExecutionError, match="not found"):
            await async_run_ffmpeg(hass, cmd, raise_on_error=True)


@pytest.mark.asyncio
async def test_async_run_ffmpeg_spawn_generic_exception():
    """Verify generic exception during process spawn."""
    hass = MagicMock()
    cmd = ["bad_exec"]

    with patch(
        "custom_components.automation_generator.ffmpeg_runner._spawn_process",
        side_effect=PermissionError("Permission denied"),
    ):
        result = await async_run_ffmpeg(hass, cmd)
        assert result is False

        with pytest.raises(FFmpegExecutionError, match="Failed to spawn"):
            await async_run_ffmpeg(hass, cmd, raise_on_error=True)


@pytest.mark.asyncio
async def test_async_run_ffmpeg_empty_command():
    """Verify empty command handling."""
    hass = MagicMock()
    result = await async_run_ffmpeg(hass, [])
    assert result is False

    with pytest.raises(FFmpegExecutionError, match="empty"):
        await async_run_ffmpeg(hass, [], raise_on_error=True)


@pytest.mark.asyncio
async def test_async_run_ffmpeg_timeout():
    """Verify process timeout kills process and handles error."""
    mock_process = AsyncMock()
    mock_process.communicate = AsyncMock(side_effect=TimeoutError())
    mock_process.kill = MagicMock()
    mock_process.wait = AsyncMock()

    hass = MagicMock()
    cmd = ["ffmpeg", "-slow"]

    with patch(
        "custom_components.automation_generator.ffmpeg_runner._spawn_process",
        new=AsyncMock(return_value=mock_process),
    ):
        result = await async_run_ffmpeg(hass, cmd, timeout=0.1)
        assert result is False
        mock_process.kill.assert_called_once()
        mock_process.wait.assert_awaited_once()

    with (
        patch(
            "custom_components.automation_generator.ffmpeg_runner._spawn_process",
            new=AsyncMock(return_value=mock_process),
        ),
        pytest.raises(FFmpegExecutionError, match="timed out"),
    ):
        await async_run_ffmpeg(hass, cmd, timeout=0.1, raise_on_error=True)


@pytest.mark.asyncio
async def test_async_run_ffmpeg_cancellation():
    """Verify asyncio cancellation terminates child process and re-raises CancelledError."""
    mock_process = AsyncMock()
    mock_process.communicate = AsyncMock(side_effect=asyncio.CancelledError())
    mock_process.kill = MagicMock()
    mock_process.wait = AsyncMock()

    hass = MagicMock()
    cmd = ["ffmpeg", "-stream"]

    with (
        patch(
            "custom_components.automation_generator.ffmpeg_runner._spawn_process",
            new=AsyncMock(return_value=mock_process),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await async_run_ffmpeg(hass, cmd)
    mock_process.kill.assert_called_once()
    mock_process.wait.assert_awaited_once()
