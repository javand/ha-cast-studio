"""
Core Generator Engine for CastStudio.

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


def _get_step_entity_ids(step: dict[str, Any]) -> list[str]:
    """Extract ordered, deduplicated entity IDs configured for a step."""
    raw_ids: list[str] = []
    for key in ("entity_id", "entity_ids"):
        val = step.get(key)
        if isinstance(val, list):
            raw_ids.extend(
                [str(item).strip() for item in val if item and str(item).strip()]
            )
        elif val and str(val).strip():
            raw_ids.append(str(val).strip())

    seen: set[str] = set()
    eids: list[str] = []
    for eid in raw_ids:
        if eid not in seen:
            seen.add(eid)
            eids.append(eid)
    return eids


def _extract_entity_context(  # noqa: PLR0912
    hass: HomeAssistant,
    step: dict[str, Any],
) -> str:
    """Extract live state context for configured entity IDs from Home Assistant."""
    eids = _get_step_entity_ids(step)
    entity_lines: list[str] = []

    if hasattr(hass, "states") and hasattr(hass.states, "get"):
        for eid in eids:
            state_obj = hass.states.get(eid)
            if state_obj is None:
                continue

            state_val = getattr(state_obj, "state", "unknown")
            attrs = getattr(state_obj, "attributes", {}) or {}

            if eid.startswith("weather."):
                temp = attrs.get("temperature")
                unit = attrs.get("temperature_unit") or "°"
                line_parts = [f"Weather ({eid}): Condition: {state_val}"]
                if temp is not None:
                    line_parts.append(f"Temperature: {temp}{unit}")
                if forecast := attrs.get("forecast"):
                    if isinstance(forecast, list):
                        line_parts.append(f"Forecast: {forecast[:3]}")
                    else:
                        line_parts.append(f"Forecast: {forecast}")
                entity_lines.append(", ".join(line_parts))

            elif eid.startswith("calendar."):
                summary = (
                    attrs.get("message")
                    or attrs.get("summary")
                    or attrs.get("description")
                )
                start_time = attrs.get("start_time")
                line_parts = [f"Calendar ({eid}): State: {state_val}"]
                if summary:
                    line_parts.append(f"Summary: {summary}")
                if start_time:
                    line_parts.append(f"Start: {start_time}")
                entity_lines.append(", ".join(line_parts))

            elif eid.startswith("todo."):
                entity_lines.append(f"To-do ({eid}): {state_val} tasks pending")

            else:
                unit = attrs.get("unit_of_measurement", "")
                suffix = f" {unit}" if unit else ""
                entity_lines.append(f"Sensor ({eid}): {state_val}{suffix}".strip())

    entity_context_str = "\n".join(entity_lines)
    existing_context = step.get("context")

    if existing_context and str(existing_context).strip() and entity_context_str:
        return f"{str(existing_context).strip()}\n{entity_context_str}"
    if entity_context_str:
        return entity_context_str
    return str(existing_context).strip() if existing_context else ""


def _is_unconfigured_optional_step(hass: HomeAssistant, step: dict[str, Any]) -> bool:
    """Check if an entity step is unconfigured or missing with no fallback."""
    has_entity_key = "entity_id" in step or "entity_ids" in step
    if not has_entity_key:
        return False

    eids = _get_step_entity_ids(step)
    has_valid_state = False
    if hasattr(hass, "states") and hasattr(hass.states, "get"):
        has_valid_state = any(hass.states.get(eid) is not None for eid in eids)

    if not has_valid_state:
        has_context = bool(step.get("context") and str(step.get("context")).strip())
        has_actions = bool(step.get("actions") or step.get("actions_on_display"))
        if not has_context and not has_actions:
            return True

    return False


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
    scratch_dir = await _async_run_in_executor(
        hass, get_routine_scratch_dir, routine_name, base_tmp=scratch_base
    )

    try:
        # 3. Discover AI credentials
        ai_creds = await async_discover_ai_credentials(hass)

        slide_timestamps: list[dict[str, Any]] = []
        clip_paths: list[str] = []
        audio_paths: list[str] = []
        current_offset = 0.0

        for idx, step in enumerate(steps_to_process):
            # Check for unconfigured optional entity steps
            if _is_unconfigured_optional_step(hass, step):
                LOGGER.info(
                    "Skipping unconfigured optional step '%s'",
                    step.get("name", f"step_{idx:02d}"),
                )
                continue

            step_to_process = dict(step)
            extracted_context = _extract_entity_context(hass, step_to_process)
            if extracted_context:
                step_to_process["context"] = extracted_context

            step_name = step_to_process.get("name", f"step_{idx:02d}")

            # 4. Generate structured step content
            content = await async_generate_step_content(
                hass, step_to_process, recipient, ai_creds
            )
            card_data = content.get("visual_card", {})
            narration_script = content.get("narration_script", "")

            # 5. Determine voice and personality conditioning
            voice = step_to_process.get("voice", DEFAULT_HOST_VOICE)
            personality = (
                step_to_process.get("voice_personality")
                or step_to_process.get("personality")
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
            step_duration_override = step_to_process.get("duration")
            if step_duration_override is not None:
                duration = float(step_duration_override)
            else:
                duration = await _async_run_in_executor(
                    hass,
                    get_audio_duration,
                    effective_audio_path,
                    fallback_text=narration_script,
                )

            # 8. Record slide timestamp & display action binding
            slide_info: dict[str, Any] = {
                "name": step_name,
                "step_name": step_name,
                "timestamp": round(current_offset, 2),
                "start_time": round(current_offset, 2),
                "duration": round(duration, 2),
                "end_time": round(current_offset + duration, 2),
                "actions_on_display": step_to_process.get(
                    "actions_on_display", step_to_process.get("actions", [])
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

        if not slide_timestamps:
            msg = f"No executable steps remained for routine '{routine_name}'"
            raise RoutineConfigError(msg)

        total_duration = round(current_offset, 2)
        out_path = Path(output_dir)
        await _async_run_in_executor(hass, _ensure_directory, out_path)

        if is_visual:
            # 10a. Concatenate clips into final MP4
            final_video_path = str(out_path / f"{routine_name}.mp4")
            concat_manifest = str(Path(scratch_dir) / "concat_list.txt")
            await _async_run_in_executor(
                hass, write_concat_file, clip_paths, concat_manifest
            )
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
            await _async_run_in_executor(
                hass, write_concat_file, audio_paths, concat_manifest
            )
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
        # 12. Purge intermediate scratchpad files in finally block via executor
        await _async_run_in_executor(
            hass, cleanup_scratch_dir, routine_name, base_tmp=scratch_base
        )


def _validate_clip_name(clip_name: str) -> str:
    """Validate clip name and protect against directory traversal."""
    clean_clip_name = clip_name.strip() if clip_name else ""
    if not clean_clip_name:
        msg = "clip_name cannot be empty or whitespace"
        raise RoutineConfigError(msg)

    if (
        ".." in clean_clip_name
        or "/" in clean_clip_name
        or "\\" in clean_clip_name
        or Path(clean_clip_name).name != clean_clip_name
    ):
        msg = f"Invalid clip_name path traversal: '{clip_name}'"
        raise RoutineConfigError(msg)

    return clean_clip_name


def _prepare_static_card_data(
    clean_clip_name: str,
    step_config: dict[str, Any],
    ai_card_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive and overlay card data for static visual clip."""
    card_data = dict(ai_card_data or {})
    card_keys = (
        "title",
        "subtitle",
        "bullet_points",
        "image_path",
        "icon_path",
        "icon",
        "layout",
    )
    for key in card_keys:
        if key in step_config:
            card_data[key] = step_config[key]

    if "title" not in card_data and "alert_title" in step_config:
        card_data["title"] = step_config["alert_title"]
    if "image_path" not in card_data and "image_asset" in step_config:
        card_data["image_path"] = step_config["image_asset"]

    if not card_data.get("title"):
        card_data.setdefault("title", clean_clip_name.replace("_", " ").title())

    return card_data


async def async_compile_static_clip(
    hass: HomeAssistant,
    clip_name: str,
    step_config: dict[str, Any],
    output_dir: str = DEFAULT_OUTPUT_DIR,
    scratch_base: str | None = None,
) -> dict[str, Any]:
    """
    Compile a standalone, reusable static visual alert clip with companion metadata.

    Args:
        hass: HomeAssistant instance.
        clip_name: Identifier for the static clip (e.g. 'freezer_door').
        step_config: Step configuration with title, narration/message, voice, actions.
        output_dir: Root output directory (clips stored under output_dir/static/).
        scratch_base: Optional base path for scratch directory.

    Returns:
        Metadata dictionary for the compiled static clip.

    Raises:
        RoutineConfigError: If clip_name is empty, whitespace,
            or invalid path traversal.
        GeneratorError: If media generation or FFmpeg compilation fails.

    """
    clean_clip_name = _validate_clip_name(clip_name)

    out_path = Path(output_dir)
    static_dir = out_path / "static"
    await _async_run_in_executor(hass, _ensure_directory, static_dir)

    final_video_path = str(static_dir / f"{clean_clip_name}.mp4")
    final_metadata_path = static_dir / f"{clean_clip_name}.metadata.json"

    scratch_dir = await _async_run_in_executor(
        hass,
        get_routine_scratch_dir,
        f"static_{clean_clip_name}",
        base_tmp=scratch_base,
    )

    try:
        ai_creds = await async_discover_ai_credentials(hass)

        narration_script = (
            step_config.get("narration")
            or step_config.get("message")
            or step_config.get("text")
            or step_config.get("alert_message")
        )

        ai_card: dict[str, Any] = {}
        if narration_script:
            narration_script = str(narration_script).strip()
        else:
            ai_content = await async_generate_step_content(
                hass, step_config, creds=ai_creds
            )
            narration_script = ai_content.get("narration_script", "")
            ai_card = dict(ai_content.get("visual_card", {}))

        card_data = _prepare_static_card_data(
            clean_clip_name, step_config, ai_card_data=ai_card
        )

        if not narration_script:
            narration_script = card_data.get("title", clean_clip_name)

        voice = step_config.get("voice", DEFAULT_HOST_VOICE)
        personality = (
            step_config.get("voice_personality")
            or step_config.get("personality")
            or "news_anchor"
        )

        step_wav_path = str(Path(scratch_dir) / "audio.wav")
        synth_audio = await async_synthesize_speech(
            hass, narration_script, voice, personality, step_wav_path, creds=ai_creds
        )
        effective_audio_path = synth_audio or step_wav_path

        step_duration_override = step_config.get("duration")
        if step_duration_override is not None:
            duration = float(step_duration_override)
        else:
            duration = await _async_run_in_executor(
                hass,
                get_audio_duration,
                effective_audio_path,
                fallback_text=narration_script,
            )

        slide_img_target = str(Path(scratch_dir) / "slide.jpg")
        slide_img_path = await _async_run_in_executor(
            hass,
            render_slide,
            card_data,
            slide_img_target,
            step_config.get("phonetic_name"),
        )
        effective_slide_path = slide_img_path or slide_img_target

        clip_cmd = build_slide_clip_command(
            effective_slide_path,
            effective_audio_path,
            final_video_path,
        )
        clip_ok = await async_run_ffmpeg(hass, clip_cmd)
        if not clip_ok:
            try:
                Path(final_video_path).unlink(missing_ok=True)
            except OSError:
                pass
            msg = f"FFmpeg clip compilation failed for static clip '{clean_clip_name}'"
            raise GeneratorError(msg)

        metadata: dict[str, Any] = {
            "clip_name": clean_clip_name,
            "routine_name": clean_clip_name,
            "video_path": final_video_path,
            "audio_path": None,
            "duration": round(duration, 2),
            "slide_timestamps": [
                {
                    "name": clean_clip_name,
                    "timestamp": 0.0,
                    "start_time": 0.0,
                    "duration": round(duration, 2),
                    "end_time": round(duration, 2),
                    "actions_on_display": step_config.get(
                        "actions_on_display", step_config.get("actions", [])
                    ),
                }
            ],
            "static": True,
        }
        await _async_run_in_executor(
            hass, _write_json_file, final_metadata_path, metadata
        )
        return metadata

    finally:
        await _async_run_in_executor(
            hass,
            cleanup_scratch_dir,
            f"static_{clean_clip_name}",
            base_tmp=scratch_base,
        )

