"""
Home Assistant Custom Integration for CastStudio.

Provides pregeneration and playback services for multi-sensory briefing routines,
integrating slide visuals, dual-voice TTS, synchronized display actions, and concurrent
background smart home actions.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_OUTPUT_DIR,
    CONF_PHONETIC_NAME,
    CONF_RECIPIENT_NAME,
    DEFAULT_HOST_VOICE,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PHONETIC_NAME,
    DEFAULT_RECIPIENT_NAME,
    DEFAULT_SCRIPTURE_VOICE,
    DOMAIN,
    LOGGER,
)
from .generator import (
    async_compile_routine_media,
    async_compile_static_clip,
    get_executable_steps,
)
from .helpers import async_run_in_executor, create_task
from .orchestrator import (
    async_execute_background_actions,
    async_play_and_sync_routine,
)
from .sensor import (
    CastStudioRoutineSensor,
    async_get_or_create_sensor,
    async_update_routine_sensor,
)

if TYPE_CHECKING:
    import asyncio

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

_create_task = create_task
_async_run_in_executor = async_run_in_executor


def _check_static_media(
    out_path: Path,
    routine_name: str,
) -> tuple[bool, dict[str, Any] | None]:
    """Check permanent static alert clip (never expires)."""
    static_meta_file = out_path / "static" / f"{routine_name}.metadata.json"
    static_video_file = out_path / "static" / f"{routine_name}.mp4"

    if static_meta_file.is_file():
        try:
            meta = json.loads(static_meta_file.read_text(encoding="utf-8"))
            vpath = meta.get("video_path")
            if vpath and Path(vpath).is_file():
                return True, meta
            if static_video_file.is_file():
                meta["video_path"] = str(static_video_file)
                return True, meta
        except Exception as err:  # noqa: BLE001
            LOGGER.warning(
                "Failed to validate static metadata file %s: %s",
                static_meta_file,
                err,
            )

    if static_video_file.is_file():
        meta = {
            "clip_name": routine_name,
            "routine_name": routine_name,
            "video_path": str(static_video_file),
            "audio_path": None,
            "duration": 0.0,
            "slide_timestamps": [],
            "static": True,
        }
        return True, meta

    return False, None


def _is_media_ready_and_fresh(  # noqa: PLR0911, PLR0912
    output_dir: str,
    routine_name: str,
    max_age_seconds: int = 86400,
    *,
    is_static: bool = False,
    expected_step_count: int | None = None,
) -> tuple[bool, dict[str, Any] | None]:
    """Check if media or companion metadata exists and is not stale."""
    out_path = Path(output_dir)

    # 1. Check permanent static alert clip first (never expires)
    static_ready, static_meta = _check_static_media(out_path, routine_name)
    if static_ready:
        return True, static_meta

    if is_static:
        return False, None

    # 2. Check regular routine files
    metadata_file = out_path / f"{routine_name}.metadata.json"
    video_file = out_path / f"{routine_name}.mp4"

    now = time.time()

    if metadata_file.is_file():
        try:
            mtime = metadata_file.stat().st_mtime
            if now - mtime < max_age_seconds:
                meta = json.loads(metadata_file.read_text(encoding="utf-8"))
                if expected_step_count is not None and expected_step_count > 0:
                    slides = meta.get("slide_timestamps", [])
                    if len(slides) != expected_step_count:
                        return False, None
                vpath = meta.get("video_path")
                apath = meta.get("audio_path")
                vpath_fresh = (
                    vpath
                    and Path(vpath).is_file()
                    and now - Path(vpath).stat().st_mtime < max_age_seconds
                )
                apath_fresh = (
                    apath
                    and Path(apath).is_file()
                    and now - Path(apath).stat().st_mtime < max_age_seconds
                )
                if vpath_fresh or apath_fresh:
                    return True, meta
                if not vpath and not apath:
                    return True, meta
        except Exception as err:  # noqa: BLE001
            LOGGER.warning(
                "Failed to validate metadata file %s: %s", metadata_file, err
            )

    if video_file.is_file():
        if expected_step_count is not None and expected_step_count > 0:
            return False, None
        try:
            mtime = video_file.stat().st_mtime
            if now - mtime < max_age_seconds:
                meta = {
                    "video_path": str(video_file),
                    "audio_path": None,
                    "duration": 0.0,
                    "slide_timestamps": [],
                }
                return True, meta
        except Exception as err:  # noqa: BLE001
            LOGGER.warning("Failed to inspect video file %s: %s", video_file, err)

    return False, None


def _count_expected_executable_steps(
    hass: HomeAssistant,
    routine_config: dict[str, Any],
) -> int | None:
    """Count visual or audio steps that are executable."""
    count = len(get_executable_steps(hass, routine_config))
    return count if count > 0 else None


def _discover_morning_entities(hass: HomeAssistant) -> dict[str, Any]:
    """Discover available Home Assistant entities for default morning steps."""
    weather_entity = ""
    national_day_entity = ""
    fun_fact_entity = ""
    calendar_entities: list[str] = []
    todo_entity = ""
    commute_sensors: list[str] = []
    scripture_sensor = ""

    entity_ids: list[str] = []
    if hasattr(hass, "states") and hasattr(hass.states, "async_all"):
        try:
            all_states = hass.states.async_all()
            if isinstance(all_states, (list, tuple)):
                entity_ids = [
                    str(getattr(s, "entity_id", ""))
                    for s in all_states
                    if getattr(s, "entity_id", None)
                ]
        except Exception:  # noqa: BLE001
            entity_ids = []

    for eid in entity_ids:
        if eid.startswith("weather.") and not weather_entity:
            weather_entity = eid
        elif eid.startswith("calendar."):
            calendar_entities.append(eid)
        elif eid.startswith("todo.") and not todo_entity:
            todo_entity = eid
        elif eid.startswith("sensor."):
            lower_eid = eid.lower()
            if not scripture_sensor and (
                "daily_text" in lower_eid or "scripture" in lower_eid
            ):
                scripture_sensor = eid
            elif "commute" in lower_eid or "travel_time" in lower_eid:
                commute_sensors.append(eid)
            elif not national_day_entity and (
                "national_day" in lower_eid or "holiday" in lower_eid
            ):
                national_day_entity = eid
            elif not fun_fact_entity and (
                "fun_fact" in lower_eid or "trivia" in lower_eid
            ):
                fun_fact_entity = eid

    return {
        "weather_entity": weather_entity,
        "national_day_entity": national_day_entity,
        "fun_fact_entity": fun_fact_entity,
        "calendar_entities": calendar_entities,
        "todo_entity": todo_entity,
        "commute_sensor": commute_sensors[0] if commute_sensors else "",
        "commute_sensors": commute_sensors,
        "scripture_sensor": scripture_sensor,
    }


def _build_default_morning_routine_config(
    hass: HomeAssistant,
    recipient: dict[str, Any],
) -> dict[str, Any]:
    """Build standard morning routine config with discovered or optional entities."""
    discovered = _discover_morning_entities(hass)
    recipient_name = recipient.get("name", DEFAULT_RECIPIENT_NAME)
    greeting_ctx = f"A cheerful, energizing morning greeting for {recipient_name}."
    return {
        "visuals": True,
        "steps": [
            {
                "name": "greeting",
                "visual": True,
                "title": recipient_name,
                "subtitle": "Good Morning",
                "voice": DEFAULT_HOST_VOICE,
                "voice_personality": "news_anchor",
                "context": greeting_ctx,
            },
            {
                "name": "weather",
                "visual": True,
                "entity_id": discovered["weather_entity"],
                "voice": DEFAULT_HOST_VOICE,
                "voice_personality": "news_anchor",
            },
            {
                "name": "national_day",
                "visual": True,
                "entity_id": discovered["national_day_entity"],
                "voice": DEFAULT_HOST_VOICE,
                "voice_personality": "news_anchor",
            },
            {
                "name": "fun_fact",
                "visual": True,
                "entity_id": discovered["fun_fact_entity"],
                "voice": DEFAULT_HOST_VOICE,
                "voice_personality": "news_anchor",
            },
            {
                "name": "agenda",
                "visual": True,
                "calendar_entities": discovered["calendar_entities"],
                "todo_entity": discovered["todo_entity"],
                "entity_ids": [
                    *discovered["calendar_entities"],
                    *([discovered["todo_entity"]] if discovered["todo_entity"] else []),
                ],
                "voice": DEFAULT_HOST_VOICE,
                "voice_personality": "news_anchor",
            },
            {
                "name": "commute",
                "visual": True,
                "entity_id": discovered["commute_sensor"],
                "entity_ids": discovered["commute_sensors"],
                "voice": DEFAULT_HOST_VOICE,
                "voice_personality": "news_anchor",
            },
            {
                "name": "scripture",
                "visual": True,
                "entity_id": discovered["scripture_sensor"],
                "voice": DEFAULT_SCRIPTURE_VOICE,
                "voice_personality": "life_coach",
            },
        ],
    }


def _build_default_routine_config(
    hass: HomeAssistant,
    routine_name: str,
    recipient: dict[str, Any],
) -> dict[str, Any]:
    """Build default routine configuration when none is explicitly configured."""
    if routine_name in ("morning_briefing", "morning"):
        return _build_default_morning_routine_config(hass, recipient)
    return {}


def _extract_target_player(data: dict[str, Any]) -> str | None:
    """Extract and normalize target player entity ID from data supporting aliases."""
    raw = data.get("target_player") or data.get("media_player") or data.get("entity_id")
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else None
    return str(raw).strip() if raw and str(raw).strip() else None


def _resolve_routine_invocation(
    hass: HomeAssistant,
    routine_name: str,
    routine_config: dict[str, Any] | None,
    recipient: dict[str, Any] | None,
    output_dir: str | None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Resolve effective routine_config, recipient profile, and output_dir."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    eff_config = (
        routine_config
        if routine_config is not None
        else domain_data.get("routines", {}).get(routine_name, {})
    )
    if "target_player" not in eff_config:
        resolved_tp = _extract_target_player(eff_config)
        if resolved_tp:
            eff_config["target_player"] = resolved_tp
    eff_recipient = (
        recipient
        if recipient is not None
        else domain_data.get("recipient", {"name": "User", "phonetic_name": "User"})
    )
    if not eff_config.get("steps") and not eff_config.get("static"):
        default_cfg = _build_default_routine_config(hass, routine_name, eff_recipient)
        if default_cfg:
            eff_config = {**default_cfg, **eff_config}
            if not eff_config.get("steps") and default_cfg.get("steps"):
                eff_config["steps"] = default_cfg["steps"]

    default_out = domain_data.get("output_dir", DEFAULT_OUTPUT_DIR)
    eff_output_dir = output_dir or eff_config.get("output_dir", default_out)
    return eff_config, eff_recipient, eff_output_dir


def _set_sensor_ready(
    sensor: CastStudioRoutineSensor,
    metadata: dict[str, Any],
) -> None:
    """Update routine sensor to 'ready' with metadata attributes."""
    sensor.update_state(
        "ready",
        duration=metadata.get("duration"),
        last_pregenerated=metadata.get(
            "last_pregenerated",
            datetime.now(UTC).isoformat(),
        ),
        video_path=metadata.get("video_path"),
        audio_path=metadata.get("audio_path"),
        slide_count=len(metadata.get("slide_timestamps", [])),
        active_task=None,
    )


async def async_pregenerate_routine(  # noqa: PLR0913
    hass: HomeAssistant,
    routine_name: str,
    target_player: str | None = None,  # noqa: ARG001 Unused function argument
    routine_config: dict[str, Any] | None = None,
    recipient: dict[str, Any] | None = None,
    output_dir: str | None = None,
) -> dict[str, Any]:
    """Pregenerate routine media assets silently ahead of time."""
    if not routine_name or not str(routine_name).strip():
        msg = "Missing or empty required parameter 'routine_name'"
        raise ValueError(msg)

    routine_config, recipient, output_dir = _resolve_routine_invocation(
        hass, routine_name, routine_config, recipient, output_dir
    )

    sensor = async_get_or_create_sensor(hass, routine_name)
    sensor.update_state("generating", active_task="pregenerate_routine")

    try:
        metadata = await async_compile_routine_media(
            hass,
            routine_name=routine_name,
            routine_config=routine_config,
            recipient=recipient,
            output_dir=output_dir,
        )
    except Exception as err:
        sensor.update_state("error", active_task=None)
        LOGGER.error("Failed to pregenerate routine '%s': %s", routine_name, err)
        raise
    else:
        _set_sensor_ready(sensor, metadata)
        return metadata


async def async_compile_clip(
    hass: HomeAssistant,
    clip_name: str,
    step_config: dict[str, Any],
    output_dir: str | None = None,
) -> dict[str, Any]:
    """Pre-compiles a permanent, reusable static multimedia alert clip."""
    if not clip_name or not str(clip_name).strip():
        msg = "Missing or empty required parameter 'clip_name'"
        raise ValueError(msg)
    if not step_config or not isinstance(step_config, dict):
        msg = "Missing or invalid required parameter 'step_config'"
        raise ValueError(msg)

    domain_data = hass.data.setdefault(DOMAIN, {})
    output_dir = output_dir or domain_data.get("output_dir", DEFAULT_OUTPUT_DIR)

    sensor = async_get_or_create_sensor(hass, clip_name)
    sensor.update_state("generating", active_task="compile_clip")

    try:
        metadata = await async_compile_static_clip(
            hass,
            clip_name=clip_name,
            step_config=step_config,
            output_dir=output_dir,
        )
    except Exception as err:
        sensor.update_state("error", active_task=None)
        LOGGER.error("Failed to compile static clip '%s': %s", clip_name, err)
        raise
    else:
        _set_sensor_ready(sensor, metadata)
        return metadata


async def async_run_routine(  # noqa: PLR0913, PLR0915
    hass: HomeAssistant,
    routine_name: str,
    target_player: str | None = None,
    routine_config: dict[str, Any] | None = None,
    recipient: dict[str, Any] | None = None,
    output_dir: str | None = None,
    force_regenerate: bool = False,  # noqa: FBT001, FBT002
) -> dict[str, Any]:
    """Execute routine with instant playback or fallback on-demand generation."""
    if not routine_name or not str(routine_name).strip():
        msg = "Missing or empty required parameter 'routine_name'"
        raise ValueError(msg)

    routine_config, recipient, output_dir = _resolve_routine_invocation(
        hass, routine_name, routine_config, recipient, output_dir
    )
    target_player = _extract_target_player(
        {"target_player": target_player}
    ) or _extract_target_player(routine_config)

    sensor = async_get_or_create_sensor(hass, routine_name)

    # 1. Check if media exists and is fresh (<24h) unless force_regenerate is requested
    expected_steps = _count_expected_executable_steps(hass, routine_config)
    force_reg = force_regenerate or bool(routine_config.get("force_regenerate", False))
    if force_reg:
        is_ready, metadata = False, None
    else:
        is_static_routine = bool(routine_config.get("static", False))
        is_ready, metadata = await _async_run_in_executor(
            hass,
            _is_media_ready_and_fresh,
            output_dir,
            routine_name,
            86400,
            is_static=is_static_routine,
            expected_step_count=expected_steps,
        )

    # 2. If not ready/missing or stale, compile on-demand
    if not is_ready or metadata is None:
        sensor.update_state("generating", active_task="run_routine")
        try:
            if routine_config.get("static"):
                step_config = routine_config.get("step_config", routine_config)
                metadata = await async_compile_static_clip(
                    hass,
                    clip_name=routine_name,
                    step_config=step_config,
                    output_dir=output_dir,
                )
            else:
                metadata = await async_compile_routine_media(
                    hass,
                    routine_name=routine_name,
                    routine_config=routine_config,
                    recipient=recipient,
                    output_dir=output_dir,
                )
            _set_sensor_ready(sensor, metadata)
        except Exception as err:
            sensor.update_state("error", active_task=None)
            LOGGER.error(
                "On-demand compilation failed for routine '%s': %s",
                routine_name,
                err,
            )
            raise

    # 3. Simultaneously dispatch background non-visual actions
    steps = routine_config.get("steps", [])
    background_steps: list[dict[str, Any]] = []
    if "background_actions" in routine_config:
        background_steps.append(
            {"background_actions": routine_config["background_actions"]}
        )
    background_steps.extend([s for s in steps if not s.get("visual", False)])

    if background_steps:
        _create_task(
            hass,
            async_execute_background_actions(hass, background_steps),
            name=f"background_actions_{routine_name}",
        )

    # 4. Set sensor state to playing
    sensor.update_state("playing", active_task="run_routine")

    # Forward post-routine actions from routine configuration to metadata
    post_routine_actions = (
        routine_config.get("post_routine_actions")
        or routine_config.get("post_briefing_actions")
        or routine_config.get("post_actions")
    )
    if post_routine_actions:
        metadata["post_routine_actions"] = post_routine_actions

    # 5. Dispatch async_play_and_sync_routine
    play_task = None
    if target_player:
        play_task = await async_play_and_sync_routine(
            hass,
            routine_name,
            target_player,
            metadata,
            steps,
        )

    # 6. Monitor playback completion and restore sensor state to ready
    async def _monitor_playback_completion(
        task: asyncio.Task[Any] | None,
        sens: CastStudioRoutineSensor,
    ) -> None:
        try:
            if task is not None:
                await task
        except Exception as err:  # noqa: BLE001
            LOGGER.debug("Playback completion wait exception: %s", err)
        finally:
            sens.update_state("ready", active_task=None)

    _create_task(
        hass,
        _monitor_playback_completion(play_task, sensor),
        name=f"monitor_playback_{routine_name}",
    )

    return metadata


async def async_reload(hass: HomeAssistant) -> None:
    """Reload integration configuration and reset routine sensors."""
    domain_data = hass.data.get(DOMAIN, {})
    for sensor in domain_data.get("sensors", {}).values():
        sensor.update_state("idle", active_task=None)

    if hasattr(hass, "config_entries") and hasattr(
        hass.config_entries, "async_entries"
    ):
        for entry in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_reload(entry.entry_id)


def _merge_service_post_actions(
    hass: HomeAssistant,
    routine_name: str,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    """Merge top-level post_routine_actions into routine_config if provided."""
    routine_config = data.get("routine_config")
    if "post_routine_actions" in data:
        if routine_config is None:
            domain_data = hass.data.get(DOMAIN, {})
            routine_config = dict(domain_data.get("routines", {}).get(routine_name, {}))
        else:
            routine_config = dict(routine_config)
        if "post_routine_actions" not in routine_config:
            routine_config["post_routine_actions"] = data["post_routine_actions"]
    return routine_config


async def async_setup(
    hass: HomeAssistant,
    config: dict,
) -> bool:
    """Set up the cast_studio component."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_config = config.get(DOMAIN, {})

    domain_data["config"] = domain_config
    recipient = domain_config.get(
        "recipient", {"name": "User", "phonetic_name": "User"}
    )
    domain_data["recipient"] = recipient

    routines = domain_config.get("routines", {})
    domain_data["routines"] = routines
    if CONF_OUTPUT_DIR in domain_config:
        domain_data["output_dir"] = domain_config[CONF_OUTPUT_DIR]

    domain_data.setdefault("sensors", {})
    domain_data["update_sensor"] = async_update_routine_sensor
    domain_data["get_sensor"] = async_get_or_create_sensor

    # Initialize sensors for configured routines
    for routine_name in routines:
        async_get_or_create_sensor(hass, routine_name)

    async def _async_handle_pregenerate(call: ServiceCall) -> None:
        data = dict(call.data)
        routine_name = data.get("routine_name")
        if not routine_name or not str(routine_name).strip():
            msg = "Missing or empty required service parameter 'routine_name'"
            raise ValueError(msg)
        routine_config = _merge_service_post_actions(hass, routine_name, data)
        target = _extract_target_player(data)
        await async_pregenerate_routine(
            hass,
            routine_name=routine_name,
            target_player=target,
            routine_config=routine_config,
            recipient=data.get("recipient"),
            output_dir=data.get("output_dir"),
        )

    async def _async_handle_run(call: ServiceCall) -> None:
        data = dict(call.data)
        routine_name = data.get("routine_name")
        if not routine_name or not str(routine_name).strip():
            msg = "Missing or empty required service parameter 'routine_name'"
            raise ValueError(msg)
        routine_config = _merge_service_post_actions(hass, routine_name, data)
        target = _extract_target_player(data)
        await async_run_routine(
            hass,
            routine_name=routine_name,
            target_player=target,
            routine_config=routine_config,
            recipient=data.get("recipient"),
            output_dir=data.get("output_dir"),
            force_regenerate=data.get("force_regenerate", False),
        )

    async def _async_handle_compile_clip(call: ServiceCall) -> None:
        data = dict(call.data)
        clip_name = data.get("clip_name")
        step_config = data.get("step_config")
        if not clip_name or not str(clip_name).strip():
            msg = "Missing or empty required service parameter 'clip_name'"
            raise ValueError(msg)
        if not step_config or not isinstance(step_config, dict):
            msg = "Missing or invalid required service parameter 'step_config'"
            raise ValueError(msg)
        await async_compile_clip(
            hass,
            clip_name=clip_name,
            step_config=step_config,
            output_dir=data.get("output_dir"),
        )

    async def _async_handle_reload(call: ServiceCall) -> None:  # noqa: ARG001
        await async_reload(hass)

    hass.services.async_register(
        DOMAIN, "pregenerate_routine", _async_handle_pregenerate
    )
    hass.services.async_register(DOMAIN, "run_routine", _async_handle_run)
    hass.services.async_register(DOMAIN, "compile_clip", _async_handle_compile_clip)
    hass.services.async_register(DOMAIN, "reload", _async_handle_reload)

    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up CastStudio from a config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    recipient_name = entry.options.get(
        CONF_RECIPIENT_NAME, entry.data.get(CONF_RECIPIENT_NAME, DEFAULT_RECIPIENT_NAME)
    )
    phonetic_name = entry.options.get(
        CONF_PHONETIC_NAME, entry.data.get(CONF_PHONETIC_NAME, DEFAULT_PHONETIC_NAME)
    )
    output_dir = entry.options.get(
        CONF_OUTPUT_DIR, entry.data.get(CONF_OUTPUT_DIR, DEFAULT_OUTPUT_DIR)
    )
    domain_data["recipient"] = {
        "name": recipient_name,
        "phonetic_name": phonetic_name,
    }
    domain_data["output_dir"] = output_dir

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
