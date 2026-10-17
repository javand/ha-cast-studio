"""
Core Generator Engine for ha-automation-generator.

Coordinates data ingestion, slide rendering via Pillow in executor,
TTS speech synthesis with per-step voice and personality conditioning,
native FFmpeg clip muxing and concatenation, companion metadata recording,
and intermediate scratchpad lifecycle management in a finally block.
"""

from __future__ import annotations

import functools
import inspect
import json
import wave
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .ai_client import async_discover_ai_credentials, async_generate_step_content
from .const import DEFAULT_HOST_VOICE, DEFAULT_OUTPUT_DIR, LOGGER
from .ffmpeg_runner import (
    async_run_ffmpeg,
    build_concat_command,
    build_slide_clip_command,
    write_concat_file,
)
from .slide_renderer import render_slide
from .storage_manager import (
    cleanup_scratch_dir,
    get_routine_scratch_dir,
    prune_stale_files,
)
from .tts_client import async_synthesize_speech

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant


class GeneratorError(Exception):
    """Base exception for generator operations."""


class RoutineConfigError(GeneratorError, ValueError):
    """Raised when routine configuration is invalid or has no steps."""


def get_audio_duration(
    audio_path: str,
    fallback_text: str | None = None,
) -> float:
    """
    Determine audio duration in seconds from WAV header or estimate from text.

    Args:
        audio_path: Path to audio file.
        fallback_text: Narration script for estimating duration if file is unavailable.

    Returns:
        Duration in seconds as a float rounded to 2 decimal places.

    """
    path = Path(audio_path)
    if path.is_file():
        try:
            with wave.open(str(path), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate > 0:
                    return round(frames / float(rate), 2)
        except (wave.Error, OSError, EOFError) as err:
            LOGGER.debug("Could not parse audio duration from %s: %s", audio_path, err)

    if fallback_text:
        words = len(fallback_text.split())
        return round(max(3.0, words / 2.5), 2)

    return 5.0


def build_audio_concat_command(
    concat_list_file: str,
    output_audio_path: str,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """
    Build FFmpeg command to concatenate audio clips into output file.

    Args:
        concat_list_file: Manifest path listing audio files.
        output_audio_path: Output audio file path (.mp3 or .wav).
        ffmpeg_bin: Path or name of FFmpeg binary.

    Returns:
        List of command-line arguments.

    """
    if output_audio_path.endswith(".mp3"):
        return [
            ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list_file),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(output_audio_path),
        ]
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
        str(output_audio_path),
    ]


async def _async_run_in_executor(
    hass: HomeAssistant | None,
    func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Execute synchronous blocking function in Home Assistant executor if available."""
    if hass is not None:
        executor_func = getattr(hass, "async_add_executor_job", None)
        if callable(executor_func):
            target: Callable[..., Any] = (
                functools.partial(func, **kwargs) if kwargs else func
            )
            res = executor_func(target, *args)
            if inspect.isawaitable(res):
                return await res
    return func(*args, **kwargs)


def _ensure_directory(directory: Path) -> None:
    """Ensure directory and parents exist synchronously."""
    directory.mkdir(parents=True, exist_ok=True)


def _write_json_file(file_path: Path, data: dict[str, Any]) -> None:
    """Write dictionary to JSON file synchronously."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


async def async_compile_routine_media(  # noqa: PLR0912, PLR0915
    hass: HomeAssistant,
    routine_name: str,
    routine_config: dict[str, Any],
    recipient: dict[str, Any],
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    """
    Compile multimedia routine into MP4 or audio file with companion metadata.

    Args:
        hass: HomeAssistant instance.
        routine_name: Name identifier of the routine (e.g. 'morning').
        routine_config: Routine definition containing steps, visuals flag, etc.
        recipient: Profile containing real 'name' and 'phonetic_name'.
        output_dir: Destination directory for compiled media and metadata.

    Returns:
        Metadata dictionary with media path, duration, and slide_timestamps.

    Raises:
        RoutineConfigError: If routine configuration is invalid or missing steps.
        GeneratorError: If media generation or FFmpeg compilation fails.

    """
    all_steps = routine_config.get("steps", [])
    if not all_steps:
        msg = f"Routine '{routine_name}' configuration contains no steps"
        raise RoutineConfigError(msg)

    is_visual = routine_config.get("visuals", True)
    if is_visual:
        steps_to_process = [s for s in all_steps if s.get("visual", False) is True]
        if not steps_to_process:
            msg = f"No visual steps found in routine '{routine_name}' configuration"
            raise RoutineConfigError(msg)
    else:
        steps_to_process = [s for s in all_steps if not s.get("background_only", False)]
        if not steps_to_process:
            msg = f"No audio steps found in routine '{routine_name}' configuration"
            raise RoutineConfigError(msg)

    # 1. Prune stale output files older than 24 hours
    try:
        await _async_run_in_executor(hass, prune_stale_files, output_dir)
    except Exception as err:  # noqa: BLE001
        LOGGER.warning("Stale file pruning failed in %s: %s", output_dir, err)

    # 2. Allocate scratchpad directory
    scratch_base = routine_config.get("scratch_base")
    scratch_dir = get_routine_scratch_dir(routine_name, base_tmp=scratch_base)

    try:
        # 3. Discover AI credentials
        ai_creds = await async_discover_ai_credentials(hass)

        slide_timestamps: list[dict[str, Any]] = []
        clip_paths: list[str] = []
        audio_paths: list[str] = []
        current_offset = 0.0

        for idx, step in enumerate(steps_to_process):
            step_name = step.get("name", f"step_{idx:02d}")

            # 4. Generate structured step content
            content = await async_generate_step_content(hass, step, recipient, ai_creds)
            card_data = content.get("visual_card", {})
            narration_script = content.get("narration_script", "")

            # 5. Determine voice and personality conditioning
            voice = step.get("voice", DEFAULT_HOST_VOICE)
            personality = (
                step.get("voice_personality")
                or step.get("personality")
                or "news_anchor"
            )

            # 6. Synthesize TTS speech
            step_wav_path = str(Path(scratch_dir) / f"audio_{idx:02d}.wav")
            synth_audio_path = await async_synthesize_speech(
                hass,
                narration_script,
                voice,
                personality,
                step_wav_path,
                creds=ai_creds,
            )
            effective_audio_path = synth_audio_path or step_wav_path

            # 7. Calculate step duration
            step_duration_override = step.get("duration")
            if step_duration_override is not None:
                duration = float(step_duration_override)
            else:
                duration = get_audio_duration(
                    effective_audio_path, fallback_text=narration_script
                )

            # 8. Record slide timestamp & display action binding
            slide_info: dict[str, Any] = {
                "name": step_name,
                "step_name": step_name,
                "timestamp": round(current_offset, 2),
                "start_time": round(current_offset, 2),
                "duration": round(duration, 2),
                "end_time": round(current_offset + duration, 2),
                "actions_on_display": step.get(
                    "actions_on_display", step.get("actions", [])
                ),
            }
            slide_timestamps.append(slide_info)
            current_offset += duration

            if is_visual:
                # 9a. Render visual slide via executor
                slide_img_target = str(Path(scratch_dir) / f"slide_{idx:02d}.jpg")
                slide_img_path = await _async_run_in_executor(
                    hass,
                    render_slide,
                    card_data,
                    slide_img_target,
                    recipient.get("phonetic_name"),
                )

                # 9b. Compile slide clip MP4 with FFmpeg
                step_clip_target = str(Path(scratch_dir) / f"clip_{idx:02d}.mp4")
                clip_cmd = build_slide_clip_command(
                    slide_img_path or slide_img_target,
                    effective_audio_path,
                    step_clip_target,
                )
                clip_ok = await async_run_ffmpeg(hass, clip_cmd)
                if not clip_ok:
                    msg = f"FFmpeg clip compilation failed for step '{step_name}'"
                    raise GeneratorError(msg)

                clip_paths.append(step_clip_target)
            else:
                audio_paths.append(effective_audio_path)

        total_duration = round(current_offset, 2)
        out_path = Path(output_dir)
        await _async_run_in_executor(hass, _ensure_directory, out_path)

        if is_visual:
            # 10a. Concatenate clips into final MP4
            final_video_path = str(out_path / f"{routine_name}.mp4")
            concat_manifest = str(Path(scratch_dir) / "concat_list.txt")
            write_concat_file(clip_paths, concat_manifest)
            concat_cmd = build_concat_command(concat_manifest, final_video_path)
            concat_ok = await async_run_ffmpeg(hass, concat_cmd)
            if not concat_ok:
                msg = f"FFmpeg concatenation failed for routine '{routine_name}'"
                raise GeneratorError(msg)

            metadata: dict[str, Any] = {
                "video_path": final_video_path,
                "audio_path": None,
                "duration": total_duration,
                "slide_timestamps": slide_timestamps,
            }
        else:
            # 10b. Concatenate audio files into final audio
            audio_format = routine_config.get("audio_format", "mp3")
            final_audio_path = str(out_path / f"{routine_name}.{audio_format}")
            concat_manifest = str(Path(scratch_dir) / "concat_audio.txt")
            write_concat_file(audio_paths, concat_manifest)
            concat_cmd = build_audio_concat_command(concat_manifest, final_audio_path)
            concat_ok = await async_run_ffmpeg(hass, concat_cmd)
            if not concat_ok:
                msg = f"FFmpeg audio concatenation failed for routine '{routine_name}'"
                raise GeneratorError(msg)

            metadata = {
                "video_path": None,
                "audio_path": final_audio_path,
                "duration": total_duration,
                "slide_timestamps": slide_timestamps,
            }

        # 11. Write companion metadata JSON file
        metadata_file = out_path / f"{routine_name}.metadata.json"
        await _async_run_in_executor(hass, _write_json_file, metadata_file, metadata)

        return metadata

    finally:
        # 12. Purge intermediate scratchpad files in finally block
        cleanup_scratch_dir(routine_name, base_tmp=scratch_base)
