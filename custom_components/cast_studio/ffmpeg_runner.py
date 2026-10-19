"""
Native FFmpeg clip muxing and concatenation runner.

Builds CLI commands to mux static image slides and audio clips into H.264/AAC MP4s,
concatenates clips without re-encoding, and executes FFmpeg asynchronously.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError

from .const import LOGGER

if TYPE_CHECKING:
    from collections.abc import Sequence

    from homeassistant.core import HomeAssistant


class FFmpegExecutionError(HomeAssistantError, RuntimeError):
    """Raised when an FFmpeg subprocess execution fails."""


def build_slide_clip_command(
    image_path: str,
    audio_path: str,
    output_clip_path: str,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """
    Build FFmpeg command to mux static slide image and audio into an H.264/AAC clip.

    Args:
        image_path: Path to the static JPEG image slide.
        audio_path: Path to the TTS audio file (e.g. WAV).
        output_clip_path: Destination path for the rendered MP4 clip.
        ffmpeg_bin: Path or binary name for ffmpeg (default: 'ffmpeg').

    Returns:
        List of command-line arguments for FFmpeg execution.

    Raises:
        ValueError: If any path argument is empty or whitespace.

    """
    if not image_path or not str(image_path).strip():
        msg = "image_path cannot be empty or whitespace"
        raise ValueError(msg)
    if not audio_path or not str(audio_path).strip():
        msg = "audio_path cannot be empty or whitespace"
        raise ValueError(msg)
    if not output_clip_path or not str(output_clip_path).strip():
        msg = "output_clip_path cannot be empty or whitespace"
        raise ValueError(msg)

    return [
        ffmpeg_bin,
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-i",
        str(audio_path),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-tune",
        "stillimage",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "44100",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_clip_path),
    ]


def build_concat_command(
    concat_list_file: str,
    output_mp4_path: str,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """
    Build FFmpeg command to concatenate clips listed in concat manifest.

    Args:
        concat_list_file: Path to text file listing clips in ffmpeg concat format.
        output_mp4_path: Destination path for the final concatenated MP4.
        ffmpeg_bin: Path or binary name for ffmpeg (default: 'ffmpeg').

    Returns:
        List of command-line arguments for FFmpeg execution.

    Raises:
        ValueError: If concat_list_file or output_mp4_path is empty or whitespace.

    """
    if not concat_list_file or not str(concat_list_file).strip():
        msg = "concat_list_file cannot be empty or whitespace"
        raise ValueError(msg)
    if not output_mp4_path or not str(output_mp4_path).strip():
        msg = "output_mp4_path cannot be empty or whitespace"
        raise ValueError(msg)

    return [
        ffmpeg_bin,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_file),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_mp4_path),
    ]


def write_concat_file(
    clip_paths: Sequence[str],
    concat_file_path: str,
) -> str:
    """
    Write an FFmpeg concat demuxer manifest file listing clip paths.

    Args:
        clip_paths: Sequence of clip paths to concatenate in order.
        concat_file_path: Path where the manifest text file will be saved.

    Returns:
        The path of the created concat file.

    Raises:
        ValueError: If clip_paths is empty or concat_file_path is invalid.

    """
    if not clip_paths:
        msg = "clip_paths cannot be empty"
        raise ValueError(msg)
    if not concat_file_path or not str(concat_file_path).strip():
        msg = "concat_file_path cannot be empty or whitespace"
        raise ValueError(msg)

    lines: list[str] = []
    for path in clip_paths:
        # In FFmpeg concat demuxer files, single quotes inside paths are escaped.
        escaped_path = str(path).replace("'", "'\\''")
        lines.append(f"file '{escaped_path}'")

    target_file = Path(concat_file_path)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(target_file)


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    """Safely terminate a running subprocess."""
    try:
        process.kill()
        await process.wait()
    except ProcessLookupError:
        pass
    except Exception:  # noqa: BLE001
        LOGGER.debug("Process kill exception ignored")


async def _spawn_process(cmd: list[str]) -> asyncio.subprocess.Process:
    """Spawn asynchronous subprocess."""
    return await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def async_run_ffmpeg(
    hass: HomeAssistant | None,
    command: Sequence[str],
    *,
    timeout: float | None = None,  # noqa: ASYNC109
    raise_on_error: bool = False,
) -> bool:
    """
    Execute an FFmpeg command asynchronously via asyncio.create_subprocess_exec.

    Captures stdout and stderr, logs errors, and terminates subprocess on timeout.

    Args:
        hass: HomeAssistant instance (optional).
        command: List of CLI arguments starting with the binary name.
        timeout: Maximum seconds to wait for execution before terminating.
        raise_on_error: If True, raises FFmpegExecutionError on failure.

    Returns:
        True if the subprocess exited with returncode 0, False otherwise.

    Raises:
        FFmpegExecutionError: If raise_on_error is True and execution fails.

    """
    if hass is not None:
        LOGGER.debug("Executing FFmpeg in Home Assistant context")

    if not command:
        LOGGER.error("FFmpeg command list cannot be empty")
        if raise_on_error:
            msg = "FFmpeg command list cannot be empty"
            raise FFmpegExecutionError(msg)
        return False

    cmd = [str(arg) for arg in command]

    try:
        process = await _spawn_process(cmd)
    except FileNotFoundError as err:
        LOGGER.error("FFmpeg binary '%s' not found: %s", cmd[0], err)
        if raise_on_error:
            msg = f"FFmpeg binary '{cmd[0]}' not found: {err}"
            raise FFmpegExecutionError(msg) from err
        return False
    except Exception as err:
        LOGGER.error("Failed to spawn FFmpeg process: %s", err)
        if raise_on_error:
            msg = f"Failed to spawn FFmpeg process: {err}"
            raise FFmpegExecutionError(msg) from err
        return False

    try:
        async with asyncio.timeout(timeout):
            _stdout, stderr = await process.communicate()
    except TimeoutError as err:
        LOGGER.error("FFmpeg process timed out after %s seconds: %s", timeout, cmd)
        await _terminate_process(process)
        if raise_on_error:
            msg = f"FFmpeg process timed out after {timeout} seconds"
            raise FFmpegExecutionError(msg) from err
        return False
    except asyncio.CancelledError:
        LOGGER.warning("FFmpeg process execution cancelled: %s", cmd)
        await _terminate_process(process)
        raise

    if process.returncode != 0:
        stderr_text = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
        LOGGER.error(
            "FFmpeg execution failed with returncode %s: %s",
            process.returncode,
            stderr_text,
        )
        if raise_on_error:
            msg = (
                f"FFmpeg execution failed with returncode "
                f"{process.returncode}: {stderr_text}"
            )
            raise FFmpegExecutionError(msg)
        return False

    LOGGER.debug("FFmpeg command finished successfully: %s", " ".join(cmd))
    return True
