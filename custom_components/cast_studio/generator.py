"""
Core Generator Engine for CastStudio.

Coordinates data ingestion, slide rendering via Pillow in executor,
TTS speech synthesis with per-step voice and personality conditioning,
native FFmpeg clip muxing and concatenation, companion metadata recording,
and intermediate scratchpad lifecycle management in a finally block.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import wave
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .ai_client import (
    _enforce_recipient_separation,
    async_discover_ai_credentials,
    async_generate_ai_image,
    async_generate_step_content,
)
from .const import DEFAULT_HOST_VOICE, DEFAULT_OUTPUT_DIR, LOGGER
from .ffmpeg_runner import (
    async_run_ffmpeg,
    build_concat_command,
    build_slide_clip_command,
    write_concat_file,
)
from .helpers import async_run_in_executor
from .slide_renderer import render_slide
from .storage_manager import (
    cleanup_scratch_dir,
    get_routine_scratch_dir,
    prune_stale_files,
)
from .tts_client import async_synthesize_speech

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

DEFAULT_SLIDE_PAD_SECONDS = 0.5
FINAL_SLIDE_PAD_SECONDS = 2.5

_async_run_in_executor = async_run_in_executor


class GeneratorError(Exception):
    """Base exception for generator operations."""


class RoutineConfigError(GeneratorError, ValueError):
    """Raised when routine configuration is invalid or has no steps."""


def _get_step_entity_ids(step: dict[str, Any]) -> list[str]:
    """Extract ordered, deduplicated entity IDs configured for a step."""
    raw_ids: list[str] = []
    for key in (
        "entity_id",
        "entity_ids",
        "calendar_entity",
        "calendar_entities",
        "todo_entity",
        "todo_entities",
    ):
        val = step.get(key)
        if isinstance(val, list):
            for item in val:
                if item and str(item).strip():
                    raw_ids.extend(p.strip() for p in str(item).split(",") if p.strip())
        elif val and str(val).strip():
            raw_ids.extend(p.strip() for p in str(val).split(",") if p.strip())

    seen: set[str] = set()
    eids: list[str] = []
    for eid in raw_ids:
        if eid not in seen:
            seen.add(eid)
            eids.append(eid)
    return eids


def is_step_image_dynamic(step: dict[str, Any], step_name: str = "") -> bool:
    """Determine whether a step should generate a dynamic AI image or static asset."""
    # 1. Explicit dynamic_image boolean on step
    if "dynamic_image" in step and step["dynamic_image"] is not None:
        return bool(step["dynamic_image"])

    # 2. Explicit image_mode string on step
    if step.get("image_mode"):
        return str(step["image_mode"]).strip().lower() == "dynamic"

    # 3. Explicit static flag on step
    if step.get("static") is True or step.get("static_image") is True:
        return False

    # 4. Explicit local image_path or icon_path configured on step
    if step.get("image_path") or step.get("icon_path"):
        return False

    # 5. Default rule: scripture / daily_text / bible is static by default
    step_key = (
        str(step.get("type") or step_name or step.get("name") or "").strip().lower()
    )
    # 6. Non-scripture slides default to dynamic
    return step_key not in ("scripture", "daily_text", "bible")


def _format_weather_service_response(
    eid: str,
    state_obj: Any,
    forecasts: list[Any],
) -> str:
    """Format weather state and weather.get_forecasts response into context string."""
    state_val = getattr(state_obj, "state", "unknown") if state_obj else "unknown"
    attrs = (getattr(state_obj, "attributes", None) or {}) if state_obj else {}
    temp = attrs.get("temperature")
    unit = attrs.get("temperature_unit") or "°F"
    line_parts = [f"Weather ({eid}): Condition: {state_val}"]
    if temp is not None:
        line_parts.append(f"Temperature: {temp}{unit}")
    if (humidity := attrs.get("humidity")) is not None:
        line_parts.append(f"Humidity: {humidity}%")

    forecast_parts: list[str] = []
    for idx, fc in enumerate(forecasts[:2]):
        if not isinstance(fc, dict):
            continue
        day_label = "Today" if idx == 0 else "Tomorrow"
        fc_bits: list[str] = []
        if cond := fc.get("condition"):
            fc_bits.append(f"Condition: {cond}")
        if (hi := fc.get("temperature")) is not None:
            fc_bits.append(f"High: {hi}{unit}")
        if (lo := fc.get("templow")) is not None:
            fc_bits.append(f"Low: {lo}{unit}")
        if (precip := fc.get("precipitation_probability")) is not None:
            fc_bits.append(f"Rain chance: {precip}%")
        if fc_bits:
            forecast_parts.append(f"{day_label} ({', '.join(fc_bits)})")

    if forecast_parts:
        line_parts.append(f"Forecast: {'; '.join(forecast_parts)}")
    return ", ".join(line_parts)


def _format_calendar_service_response(eid: str, events: list[Any]) -> str:
    """Format calendar.get_events response list into context string."""
    event_strs: list[str] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        summary = (
            ev.get("summary")
            or ev.get("message")
            or ev.get("description")
            or "Scheduled Event"
        )
        parts = [f"Summary: {summary}"]
        if start := ev.get("start"):
            parts.append(f"Start: {start}")
        if end := ev.get("end"):
            parts.append(f"End: {end}")
        if location := ev.get("location"):
            parts.append(f"Location: {location}")
        event_strs.append(", ".join(parts))

    if not event_strs:
        return (
            f"Calendar ({eid}): No scheduled meetings today. "
            "The user's schedule is completely clear."
        )
    return f"Calendar ({eid}): {'; '.join(event_strs)}"


def _format_todo_service_response(eid: str, items: list[Any]) -> str:
    """Format todo.get_items response list into context string."""
    summaries = [
        str(item.get("summary")).strip()
        for item in items
        if isinstance(item, dict)
        and item.get("summary")
        and str(item.get("summary")).strip()
    ]
    if summaries:
        return f"To-do ({eid}): {len(summaries)} tasks pending: {', '.join(summaries)}"
    return f"To-do ({eid}): 0 tasks pending"


MIN_COMMUTE_ENTRIES_FOR_COMPARISON = 2


def _format_commute_comparison(
    commute_entries: list[tuple[str, float, str]],
) -> str | None:
    """Compare 2+ numeric commute sensors and state which option is faster."""
    if len(commute_entries) < MIN_COMMUTE_ENTRIES_FOR_COMPARISON:
        return None
    sorted_entries = sorted(commute_entries, key=lambda item: item[1])
    fastest_label, fastest_val, fastest_unit = sorted_entries[0]
    second_label, second_val, second_unit = sorted_entries[1]
    unit_str = f" {fastest_unit}" if fastest_unit else " min"
    sec_unit_str = f" {second_unit}" if second_unit else " min"
    delta = round(second_val - fastest_val, 1)
    if delta <= 0:
        return (
            f"Commute Comparison: {fastest_label} and {second_label} "
            f"have the same travel time ({fastest_val:g}{unit_str})."
        )
    return (
        f"Commute Comparison: {fastest_label} ({fastest_val:g}{unit_str}) is "
        f"{delta:g} minutes faster than {second_label} ({second_val:g}{sec_unit_str})."
    )


def _format_scripture_sensor(
    hass: HomeAssistant,
    eid: str,
    attrs: dict[str, Any],
) -> str:
    """Format daily scripture sensor and optional companion commentary sensor."""
    lower_attrs = {str(k).lower(): v for k, v in attrs.items()}
    parts = [f"Daily Scripture ({eid}):"]
    if ref := lower_attrs.get("scripture"):
        parts.append(f"Citation: {ref}")
    if txt := (lower_attrs.get("text") or lower_attrs.get("commentary")):
        parts.append(f"Text: {txt}")
    if lower_attrs.get("text") and lower_attrs.get("commentary"):
        parts.append(f"Commentary: {lower_attrs['commentary']}")
    elif not lower_attrs.get("commentary") and eid.endswith("_daily_text_today"):
        comment_obj = hass.states.get(f"{eid}_comment")
        comment_attrs = getattr(comment_obj, "attributes", None) or {}
        comment_text = (
            comment_attrs.get("text") if hasattr(comment_attrs, "get") else None
        )
        if comment_text and comment_text != lower_attrs.get("text"):
            parts.append(f"Commentary: {comment_text}")
    return "\n".join(parts)


def _extract_verbatim_scripture_data(
    hass: HomeAssistant,
    step: dict[str, Any],
) -> tuple[str | None, str | None, str | None]:
    """Extract scripture citation, text, and commentary verbatim from step or hass."""
    # 1. Try entities configured on the step or default daily text sensor
    candidates = _get_step_entity_ids(step)
    if not candidates:
        candidates = ["sensor.jw_library_daily_text_today"]

    for eid in candidates:
        if hasattr(hass, "states") and hasattr(hass.states, "get"):
            state_obj = hass.states.get(eid)
            attrs = getattr(state_obj, "attributes", None) or {}
            lower_attrs = (
                {str(k).lower(): v for k, v in attrs.items()}
                if isinstance(attrs, Mapping)
                else {}
            )
            citation = lower_attrs.get("scripture")
            text = lower_attrs.get("text")
            comment = lower_attrs.get("commentary")
            if not comment and eid.endswith("_daily_text_today"):
                comment_obj = hass.states.get(f"{eid}_comment")
                c_attrs = getattr(comment_obj, "attributes", None) or {}
                if isinstance(c_attrs, Mapping):
                    comment = c_attrs.get("text")
            if citation and text:
                return (
                    str(citation).strip(),
                    str(text).strip(),
                    str(comment).strip() if comment else None,
                )

    # 2. Try parsing from step context if async_extract_entity_context
    # already formatted it
    context = str(step.get("context") or "")
    if "Citation:" in context and "Text:" in context:
        citation, text, comment = None, None, None
        for line in context.splitlines():
            line_str = line.strip()
            if line_str.startswith("Citation:"):
                citation = line_str.removeprefix("Citation:").strip()
            elif line_str.startswith("Text:"):
                text = line_str.removeprefix("Text:").strip()
            elif line_str.startswith("Commentary:"):
                comment = line_str.removeprefix("Commentary:").strip()
        if citation and text:
            return citation, text, comment

    return None, None, None


def _format_single_entity_state(  # noqa: PLR0912
    hass: HomeAssistant,
    eid: str,
    state_obj: Any,
    commute_candidates: list[tuple[str, float, str]],
    *,
    is_commute_step: bool = False,
) -> str:
    """Format a single Home Assistant entity state into a context line."""
    state_val = getattr(state_obj, "state", "unknown")
    attrs = getattr(state_obj, "attributes", {}) or {}
    if not isinstance(attrs, dict):
        attrs = {}

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
        return ", ".join(line_parts)

    if eid.startswith("calendar."):
        summary = (
            attrs.get("message") or attrs.get("summary") or attrs.get("description")
        )
        start_time = attrs.get("start_time")
        if state_val == "off" and not summary:
            return (
                f"Calendar ({eid}): No scheduled meetings today. "
                "The user's schedule is completely clear."
            )
        line_parts = [f"Calendar ({eid}): State: {state_val}"]
        if summary:
            line_parts.append(f"Summary: {summary}")
        if start_time:
            line_parts.append(f"Start: {start_time}")
        return ", ".join(line_parts)

    if eid.startswith("todo."):
        return f"To-do ({eid}): {state_val} tasks pending"

    if any(str(k).lower() in ("scripture", "text", "commentary") for k in attrs):
        return _format_scripture_sensor(hass, eid, attrs)

    unit = str(attrs.get("unit_of_measurement", "")).strip()
    suffix = f" {unit}" if unit else ""
    friendly = str(attrs.get("friendly_name", "")).strip()
    base_line = f"Sensor ({eid}): {state_val}{suffix}".strip()
    if friendly:
        base_line = f"{base_line} ({friendly})"

    lower_eid = eid.lower()
    if is_commute_step or "commute" in lower_eid or "travel_time" in lower_eid:
        with contextlib.suppress(ValueError, TypeError):
            num_val = float(str(state_val).strip())
            label = friendly or eid.removeprefix("sensor.").replace("_", " ").title()
            commute_candidates.append((label, num_val, unit or "min"))

    return base_line


def _extract_entity_context(
    hass: HomeAssistant,
    step: dict[str, Any],
    service_overrides: dict[str, str] | None = None,
) -> str:
    """Extract live state context for configured entity IDs from Home Assistant."""
    eids = _get_step_entity_ids(step)
    entity_lines: list[str] = []
    step_name = str(step.get("name", "")).lower()
    step_type = str(step.get("type", "")).lower()
    is_fun_fact = step_name == "fun_fact" or step_type == "fun_fact"
    is_national_day = step_name == "national_day" or step_type == "national_day"
    is_commute = step_name == "commute" or step_type == "commute"
    has_states_getter = hasattr(hass, "states") and hasattr(hass.states, "get")
    commute_candidates: list[tuple[str, float, str]] = []

    for eid in eids:
        if service_overrides and eid in service_overrides:
            entity_lines.append(service_overrides[eid])
            continue

        if not has_states_getter:
            continue

        state_obj = hass.states.get(eid)
        if state_obj is None:
            continue

        state_val = getattr(state_obj, "state", "unknown")
        if (is_fun_fact or is_national_day) and str(state_val).lower() in (
            "unknown",
            "unavailable",
            "",
        ):
            continue

        entity_lines.append(
            _format_single_entity_state(
                hass,
                eid,
                state_obj,
                commute_candidates,
                is_commute_step=is_commute,
            )
        )

    if comparison_line := _format_commute_comparison(commute_candidates):
        entity_lines.append(comparison_line)

    entity_context_str = "\n".join(entity_lines)
    existing_context = step.get("context")

    if existing_context and str(existing_context).strip() and entity_context_str:
        return f"{str(existing_context).strip()}\n{entity_context_str}"
    if entity_context_str:
        return entity_context_str
    if existing_context and str(existing_context).strip():
        return str(existing_context).strip()
    if is_national_day:
        return "Share today's official National Day celebration and its history."
    if is_fun_fact:
        return "Share an engaging, surprising morning fun fact."
    return ""


async def _async_call_service_for_entity(
    async_call: Any,
    domain: str,
    service: str,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    """Invoke a Home Assistant service and validate dict response output."""
    try:
        res = async_call(
            domain,
            service,
            data,
            blocking=True,
            return_response=True,
        )
        if inspect.isawaitable(res):
            res = await res
        if isinstance(res, dict):
            return res
    except Exception as err:  # noqa: BLE001
        LOGGER.debug("Service %s.%s failed for %s: %s", domain, service, data, err)
    return None


async def async_extract_entity_context(
    hass: HomeAssistant,
    step: dict[str, Any],
) -> str:
    """Asynchronously extract entity context for weather, calendar, and tasks."""
    eids = _get_step_entity_ids(step)
    service_overrides: dict[str, str] = {}

    services = getattr(hass, "services", None)
    async_call = getattr(services, "async_call", None) if services else None
    has_states_getter = hasattr(hass, "states") and hasattr(hass.states, "get")

    if callable(async_call):
        now = datetime.now().astimezone()
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end_dt = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()

        for eid in eids:
            if eid.startswith("weather."):
                res = await _async_call_service_for_entity(
                    async_call,
                    "weather",
                    "get_forecasts",
                    {"entity_id": eid, "type": "daily"},
                )
                if res and isinstance(res.get(eid), dict):
                    forecasts = res[eid].get("forecast")
                    if isinstance(forecasts, list) and forecasts:
                        state_obj = hass.states.get(eid) if has_states_getter else None
                        service_overrides[eid] = _format_weather_service_response(
                            eid, state_obj, forecasts
                        )

            elif eid.startswith("calendar."):
                res = await _async_call_service_for_entity(
                    async_call,
                    "calendar",
                    "get_events",
                    {
                        "entity_id": eid,
                        "start_date_time": start_dt,
                        "end_date_time": end_dt,
                    },
                )
                if res and isinstance(res.get(eid), dict):
                    events = res[eid].get("events")
                    if isinstance(events, list):
                        service_overrides[eid] = _format_calendar_service_response(
                            eid, events
                        )

            elif eid.startswith("todo."):
                res = await _async_call_service_for_entity(
                    async_call,
                    "todo",
                    "get_items",
                    {"entity_id": eid, "status": ["needs_action"]},
                )
                if res and isinstance(res.get(eid), dict):
                    items = res[eid].get("items")
                    if isinstance(items, list):
                        service_overrides[eid] = _format_todo_service_response(
                            eid, items
                        )

    return _extract_entity_context(hass, step, service_overrides=service_overrides)


def _is_unconfigured_optional_step(hass: HomeAssistant, step: dict[str, Any]) -> bool:
    """Check if an entity step is unconfigured or missing with no fallback."""
    step_name = str(step.get("name", "")).lower()
    step_type = str(step.get("type", "")).lower()
    if step_name in ("fun_fact", "national_day") or step_type in (
        "fun_fact",
        "national_day",
    ):
        return False

    has_entity_key = (
        "entity_id" in step
        or "entity_ids" in step
        or "calendar_entity" in step
        or "calendar_entities" in step
        or "todo_entity" in step
        or "todo_entities" in step
    )
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


def get_executable_steps(
    hass: HomeAssistant,
    routine_config: dict[str, Any],
    *,
    log_skipped: bool = False,
) -> list[dict[str, Any]]:
    """Filter routine steps and skip unconfigured optional steps."""
    all_steps = routine_config.get("steps")
    if not all_steps or not isinstance(all_steps, list):
        return []

    is_visual = routine_config.get("visuals", True)
    if is_visual:
        candidates = [s for s in all_steps if s.get("visual", False) is True]
    else:
        candidates = [s for s in all_steps if not s.get("background_only", False)]

    executable: list[dict[str, Any]] = []
    for raw_idx, step in enumerate(candidates):
        if _is_unconfigured_optional_step(hass, step):
            if log_skipped:
                LOGGER.info(
                    "Skipping unconfigured optional step '%s'",
                    step.get("name", f"step_{raw_idx:02d}"),
                )
            continue
        executable.append(step)
    return executable


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
    """Build FFmpeg command to concatenate audio clips into output file."""
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


def _ensure_directory(directory: Path) -> None:
    """Ensure directory and parents exist synchronously."""
    directory.mkdir(parents=True, exist_ok=True)


def _write_json_file(file_path: Path, data: dict[str, Any]) -> None:
    """Write dictionary to JSON file synchronously."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _unlink_file_if_exists(file_path: str) -> None:
    with contextlib.suppress(OSError):
        Path(file_path).unlink(missing_ok=True)


def _detect_step_weather_condition(
    hass: HomeAssistant,
    step: dict[str, Any],
) -> str | None:
    """Return weather state condition if step references a weather entity."""
    if not (hasattr(hass, "states") and hasattr(hass.states, "get")):
        return None
    for eid in _get_step_entity_ids(step):
        if eid.startswith("weather."):
            state_obj = hass.states.get(eid)
            state_val = getattr(state_obj, "state", None)
            if isinstance(state_val, str) and state_val not in (
                "unknown",
                "unavailable",
                "",
            ):
                return state_val
    return None


async def _async_resolve_step_visual_assets(  # noqa: PLR0912, PLR0913, PLR0915
    hass: HomeAssistant,
    step_to_process: dict[str, Any],
    step_name: str,
    card_data: dict[str, Any],
    ai_creds: dict[str, Any],
    scratch_dir: str | None = None,
) -> None:
    """Resolve dynamic and AI-generated visual background assets for a visual slide."""
    is_dynamic = is_step_image_dynamic(step_to_process, step_name)

    if card_data.get("image_path"):
        card_data.setdefault("image_type", "dynamic" if is_dynamic else "static")
        card_data.setdefault("dynamic_image", is_dynamic)
        card_data.setdefault("layout", "photo")
        return

    step_key = str(step_to_process.get("type") or step_name or "").strip().lower()
    name_key = str(step_name or "").strip().lower()

    # Determine fallback static asset and prompt per slide theme
    if step_key in ("greeting", "welcome") or name_key in ("greeting", "welcome"):
        fallback_asset = "greeting_coffee.jpg"
        dynamic_prompt = (
            "A peaceful morning scene with a warm ceramic mug of fresh coffee "
            "on a wooden table, soft golden morning sunrise light streaming "
            "through a window, cozy atmosphere, photorealistic cinematic 16:9"
        )
    elif step_key in ("national_day", "holiday", "observance") or name_key in (
        "national_day",
        "holiday",
        "observance",
    ):
        fallback_asset = "national_day.jpg"
        topic = (
            card_data.get("subtitle")
            or card_data.get("title")
            or step_to_process.get("context")
            or "National Day celebration"
        )
        dynamic_prompt = (
            f"A vibrant, festive cinematic photograph celebrating {topic}, "
            "holiday atmosphere, joyful, detailed, beautiful natural lighting, "
            "16:9 widescreen aspect ratio"
        )
    elif step_key in ("fun_fact", "trivia") or name_key in ("fun_fact", "trivia"):
        fallback_asset = "fun_fact.jpg"
        topic = (
            card_data.get("subtitle")
            or card_data.get("title")
            or step_to_process.get("context")
            or "morning fun fact"
        )
        dynamic_prompt = (
            f"An intriguing, cinematic photograph representing {topic}, "
            "curious and fascinating, detailed, beautiful natural lighting, "
            "16:9 widescreen aspect ratio"
        )
    elif step_key == "weather" or name_key == "weather":
        cond = str(
            card_data.get("weather_condition")
            or _detect_step_weather_condition(hass, step_to_process)
            or ""
        ).lower()
        if any(
            w in cond for w in ("rain", "pour", "drizzle", "storm", "thunder", "shower")
        ):
            fallback_asset = "weather_rainy.jpg"
        elif any(
            w in cond for w in ("snow", "sleet", "hail", "ice", "blizzard", "winter")
        ):
            fallback_asset = "weather_snow.jpg"
        elif any(
            w in cond for w in ("cloud", "overcast", "fog", "mist", "wind", "haze")
        ):
            fallback_asset = "weather_cloudy.jpg"
        else:
            fallback_asset = "weather_sunny.jpg"
        dynamic_prompt = (
            "A photorealistic cinematic 16:9 widescreen morning landscape in soft dawn "
            f"light showing {cond or 'clear morning'} weather, realistic atmosphere, "
            "8k resolution, beautiful natural lighting"
        )
    elif step_key in (
        "agenda",
        "calendar",
        "schedule",
        "todo",
        "tasks",
    ) or name_key in (
        "agenda",
        "calendar",
        "schedule",
        "todo",
        "tasks",
    ):
        fallback_asset = "calendar_agenda.jpg"
        dynamic_prompt = (
            "A clean, aesthetic morning workspace with an open daily planner "
            "notebook and coffee cup on a modern desk bathed in warm morning "
            "daylight, cinematic 16:9 widescreen"
        )
    elif step_key in ("commute", "traffic", "transit") or name_key in (
        "commute",
        "traffic",
        "transit",
    ):
        fallback_asset = "commute_traffic.jpg"
        dynamic_prompt = (
            "An aerial cinematic 16:9 widescreen view of morning highway traffic "
            "and city skyline bathed in soft golden morning sunlight, "
            "clear commute route"
        )
    elif step_key in ("scripture", "bible", "daily_text") or name_key in (
        "scripture",
        "bible",
        "daily_text",
    ):
        fallback_asset = "bible_scripture.jpg"
        dynamic_prompt = (
            "A peaceful morning Bible study scene with an open leather Bible on a "
            "warm wooden table, soft morning window light, cinematic 16:9 widescreen"
        )
    else:
        fallback_asset = "weather_sunny.jpg"
        dynamic_prompt = str(
            card_data.get("image_prompt")
            or f"A beautiful cinematic morning photograph for {step_name}, 16:9"
        )

    if not is_dynamic:
        card_data["image_path"] = fallback_asset
        card_data["image_type"] = "static"
        card_data["dynamic_image"] = False
        card_data.setdefault("layout", "photo")
        return

    ai_img_path = (
        str(Path(scratch_dir) / f"bg_{step_name}.png") if scratch_dir else None
    )
    if ai_img_path and ai_creds.get("platform") == "gemini" and ai_creds.get("api_key"):
        try:
            generated = await async_generate_ai_image(
                hass, dynamic_prompt, ai_img_path, ai_creds=ai_creds
            )
            if generated and Path(generated).is_file():
                card_data["image_path"] = generated
                card_data["image_type"] = "dynamic"
                card_data["dynamic_image"] = True
                card_data.setdefault("layout", "photo")
                return
        except Exception as err:  # noqa: BLE001
            LOGGER.warning(
                "Dynamic AI image generation failed for step '%s': %s",
                step_name,
                err,
            )

    # Fallback to static asset if dynamic generation fails or credentials unavailable
    card_data["image_path"] = fallback_asset
    card_data["image_type"] = "static"
    card_data["dynamic_image"] = False
    card_data.setdefault("layout", "photo")
    LOGGER.info(
        "Using static fallback asset '%s' for step '%s'",
        fallback_asset,
        step_name,
    )


async def _async_compile_single_step(  # noqa: PLR0912, PLR0913, PLR0915
    hass: HomeAssistant,
    step_to_process: dict[str, Any],
    step_name: str,
    recipient: dict[str, Any],
    ai_creds: dict[str, Any],
    *,
    is_visual: bool,
    default_pad_seconds: float,
    audio_target_path: str,
    slide_target_path: str | None = None,
    clip_target_path: str | None = None,
) -> tuple[str, float, float]:
    """Compile a single routine or static step into audio and (if visual) MP4 clip."""
    step_key = str(step_to_process.get("type") or step_name or "").strip().lower()
    name_key = str(step_name or "").strip().lower()
    is_scripture = step_key in ("scripture", "daily_text", "bible") or name_key in (
        "scripture",
        "daily_text",
        "bible",
    )
    is_first = step_to_process.get("is_first_step")

    explicit_narration = (
        step_to_process.get("narration")
        or step_to_process.get("message")
        or step_to_process.get("text")
        or step_to_process.get("alert_message")
    )
    if explicit_narration and str(explicit_narration).strip():
        raw_content = {
            "visual_card": {
                "title": step_to_process.get(
                    "title",
                    step_to_process.get(
                        "alert_title", step_name.replace("_", " ").title()
                    ),
                ),
                "subtitle": step_to_process.get("subtitle", ""),
                "bullet_points": step_to_process.get("bullet_points", []),
            },
            "narration_script": str(explicit_narration).strip(),
        }
        content = _enforce_recipient_separation(
            raw_content, recipient, is_first=is_first
        )
    elif (
        is_scripture
        and (scripture_data := _extract_verbatim_scripture_data(hass, step_to_process))[
            0
        ]
    ):
        citation, text, comment = scripture_data
        if comment:
            verbatim_narration = (
                f"Today's text is from {citation}. It reads: {text}.\n{comment}"
            )
        else:
            verbatim_narration = f"Today's text is from {citation}. It reads: {text}."
        raw_content = {
            "visual_card": {
                "title": "Daily Scripture",
                "subtitle": str(citation),
                "bullet_points": [f'"{text}"'],
            },
            "narration_script": verbatim_narration.strip(),
        }
        content = _enforce_recipient_separation(
            raw_content, recipient, is_first=is_first
        )
    else:
        content = await async_generate_step_content(
            hass, step_to_process, recipient, ai_creds
        )

    card_data = dict(content.get("visual_card", {}))
    for key in (
        "image_path",
        "icon_path",
        "icon",
        "layout",
        "avatar_fallback",
        "theme",
        "badge",
    ):
        if key in step_to_process:
            card_data[key] = step_to_process[key]
    if "image_path" not in card_data and "image_asset" in step_to_process:
        card_data["image_path"] = step_to_process["image_asset"]
    if not card_data.get("title"):
        if step_to_process.get("title"):
            card_data["title"] = step_to_process["title"]
        elif step_to_process.get("alert_title"):
            card_data["title"] = step_to_process["alert_title"]
        else:
            card_data.setdefault("title", step_name.replace("_", " ").title())
    if not card_data.get("subtitle") and step_to_process.get("subtitle"):
        card_data["subtitle"] = step_to_process["subtitle"]

    card_data.setdefault("step_name", step_name)
    if weather_cond := _detect_step_weather_condition(hass, step_to_process):
        card_data.setdefault("weather_condition", weather_cond)

    if is_visual:
        scratch_dir = str(Path(slide_target_path).parent) if slide_target_path else None
        await _async_resolve_step_visual_assets(
            hass,
            step_to_process,
            step_name,
            card_data,
            ai_creds,
            scratch_dir=scratch_dir,
        )

    narration_script = content.get("narration_script", "")
    if not narration_script:
        fallback_msg = (
            step_to_process.get("context")
            or card_data.get("title")
            or step_name.replace("_", " ")
        )
        narration_script = str(fallback_msg)

    voice = step_to_process.get("voice", DEFAULT_HOST_VOICE)
    personality = (
        step_to_process.get("voice_personality")
        or step_to_process.get("personality")
        or "news_anchor"
    )

    synth_audio_path = await async_synthesize_speech(
        hass,
        narration_script,
        voice,
        personality,
        audio_target_path,
        creds=ai_creds,
    )
    effective_audio_path = synth_audio_path or audio_target_path

    step_duration_override = step_to_process.get("duration")
    pad_seconds = 0.0
    if step_duration_override is not None:
        if is_visual:
            pad_seconds = float(step_to_process.get("pad_seconds", 0.0))
        duration = float(step_duration_override)
    else:
        raw_duration = await _async_run_in_executor(
            hass,
            get_audio_duration,
            effective_audio_path,
            fallback_text=narration_script,
        )
        if is_visual:
            pad_seconds = float(step_to_process.get("pad_seconds", default_pad_seconds))
            duration = round(raw_duration + pad_seconds, 2)
        else:
            duration = raw_duration

    if is_visual and slide_target_path and clip_target_path:
        phonetic = step_to_process.get("phonetic_name") or recipient.get(
            "phonetic_name"
        )
        slide_img_path = await _async_run_in_executor(
            hass,
            render_slide,
            card_data,
            slide_target_path,
            phonetic,
        )
        clip_cmd = build_slide_clip_command(
            slide_img_path or slide_target_path,
            effective_audio_path,
            clip_target_path,
            duration=duration,
            pad_seconds=pad_seconds,
        )
        clip_ok = await async_run_ffmpeg(hass, clip_cmd)
        if not clip_ok:
            msg = f"FFmpeg clip compilation failed for step '{step_name}'"
            raise GeneratorError(msg)

    image_info = {
        "image_type": card_data.get("image_type", "static"),
        "dynamic_image": card_data.get("dynamic_image", False),
        "image_path": card_data.get("image_path"),
    }

    return effective_audio_path, duration, pad_seconds, image_info


async def _async_finalize_routine_media(  # noqa: PLR0913
    hass: HomeAssistant,
    routine_name: str,
    routine_config: dict[str, Any],
    scratch_dir: str,
    output_dir: str,
    *,
    is_visual: bool,
    clip_paths: list[str],
    audio_paths: list[str],
    slide_timestamps: list[dict[str, Any]],
    total_duration: float,
) -> dict[str, Any]:
    """Concatenate compiled clips or audio files and write companion metadata JSON."""
    out_path = Path(output_dir)
    await _async_run_in_executor(hass, _ensure_directory, out_path)

    if is_visual:
        final_video_path = str(out_path / f"{routine_name}.mp4")
        concat_manifest = str(Path(scratch_dir) / "concat_list.txt")
        await _async_run_in_executor(
            hass, write_concat_file, clip_paths, concat_manifest
        )
        concat_cmd = build_concat_command(concat_manifest, final_video_path)
        if not await async_run_ffmpeg(hass, concat_cmd):
            msg = f"FFmpeg concatenation failed for routine '{routine_name}'"
            raise GeneratorError(msg)

        metadata: dict[str, Any] = {
            "video_path": final_video_path,
            "audio_path": None,
            "duration": total_duration,
            "slide_timestamps": slide_timestamps,
        }
    else:
        audio_format = routine_config.get("audio_format", "mp3")
        final_audio_path = str(out_path / f"{routine_name}.{audio_format}")
        concat_manifest = str(Path(scratch_dir) / "concat_audio.txt")
        await _async_run_in_executor(
            hass, write_concat_file, audio_paths, concat_manifest
        )
        concat_cmd = build_audio_concat_command(concat_manifest, final_audio_path)
        if not await async_run_ffmpeg(hass, concat_cmd):
            msg = f"FFmpeg audio concatenation failed for routine '{routine_name}'"
            raise GeneratorError(msg)

        metadata = {
            "video_path": None,
            "audio_path": final_audio_path,
            "duration": total_duration,
            "slide_timestamps": slide_timestamps,
        }

    metadata_file = out_path / f"{routine_name}.metadata.json"
    await _async_run_in_executor(hass, _write_json_file, metadata_file, metadata)
    return metadata


async def async_compile_routine_media(
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
        if not [s for s in all_steps if s.get("visual", False) is True]:
            msg = f"No visual steps found in routine '{routine_name}' configuration"
            raise RoutineConfigError(msg)
    elif not [s for s in all_steps if not s.get("background_only", False)]:
        msg = f"No audio steps found in routine '{routine_name}' configuration"
        raise RoutineConfigError(msg)

    try:
        await _async_run_in_executor(hass, prune_stale_files, output_dir)
    except Exception as err:  # noqa: BLE001
        LOGGER.warning("Stale file pruning failed in %s: %s", output_dir, err)

    scratch_base = routine_config.get("scratch_base")
    scratch_dir = await _async_run_in_executor(
        hass, get_routine_scratch_dir, routine_name, base_tmp=scratch_base
    )

    try:
        ai_creds = await async_discover_ai_credentials(hass)
        if routine_config.get("conversation_agent"):
            ai_creds["agent_id"] = routine_config["conversation_agent"]

        executable_steps = get_executable_steps(hass, routine_config, log_skipped=True)
        if not executable_steps:
            msg = f"No executable steps remained for routine '{routine_name}'"
            raise RoutineConfigError(msg)

        slide_timestamps: list[dict[str, Any]] = []
        clip_paths: list[str] = []
        audio_paths: list[str] = []
        current_offset = 0.0

        for idx, step in enumerate(executable_steps):
            is_last_step = idx == len(executable_steps) - 1
            step_to_process = dict(step)
            step_to_process["is_first_step"] = idx == 0
            if extracted_context := await async_extract_entity_context(
                hass, step_to_process
            ):
                step_to_process["context"] = extracted_context

            step_name = step_to_process.get("name", f"step_{idx:02d}")
            default_pad = (
                FINAL_SLIDE_PAD_SECONDS if is_last_step else DEFAULT_SLIDE_PAD_SECONDS
            )
            step_clip_target = str(Path(scratch_dir) / f"clip_{idx:02d}.mp4")

            (
                effective_audio_path,
                duration,
                _,
                image_info,
            ) = await _async_compile_single_step(
                hass,
                step_to_process,
                step_name,
                recipient,
                ai_creds,
                is_visual=is_visual,
                default_pad_seconds=default_pad,
                audio_target_path=str(Path(scratch_dir) / f"audio_{idx:02d}.wav"),
                slide_target_path=str(Path(scratch_dir) / f"slide_{idx:02d}.jpg"),
                clip_target_path=step_clip_target,
            )

            slide_timestamps.append(
                {
                    "name": step_name,
                    "step_name": step_name,
                    "timestamp": round(current_offset, 2),
                    "start_time": round(current_offset, 2),
                    "duration": round(duration, 2),
                    "end_time": round(current_offset + duration, 2),
                    "actions_on_display": step_to_process.get(
                        "actions_on_display", step_to_process.get("actions", [])
                    ),
                    "image_type": image_info.get("image_type", "static"),
                    "dynamic_image": image_info.get("dynamic_image", False),
                    "image_path": image_info.get("image_path"),
                }
            )
            current_offset += duration

            if is_visual:
                clip_paths.append(step_clip_target)
            else:
                audio_paths.append(effective_audio_path)

        return await _async_finalize_routine_media(
            hass,
            routine_name,
            routine_config,
            scratch_dir,
            output_dir,
            is_visual=is_visual,
            clip_paths=clip_paths,
            audio_paths=audio_paths,
            slide_timestamps=slide_timestamps,
            total_duration=round(current_offset, 2),
        )

    finally:
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
        "theme",
        "badge",
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
    """Compile a reusable static visual alert clip with companion metadata."""
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
        try:
            _, duration, _, _ = await _async_compile_single_step(
                hass,
                step_config,
                clean_clip_name,
                {},
                ai_creds,
                is_visual=True,
                default_pad_seconds=0.0,
                audio_target_path=str(Path(scratch_dir) / "audio.wav"),
                slide_target_path=str(Path(scratch_dir) / "slide.jpg"),
                clip_target_path=final_video_path,
            )
        except GeneratorError as err:
            await _async_run_in_executor(hass, _unlink_file_if_exists, final_video_path)
            msg = f"FFmpeg clip compilation failed for static clip '{clean_clip_name}'"
            raise GeneratorError(msg) from err

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
