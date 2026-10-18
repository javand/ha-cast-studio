"""
Home Assistant Custom Integration for CastStudio.

Provides pregeneration and playback services for multi-sensory briefing routines,
integrating slide visuals, dual-voice TTS, synchronized display actions, and concurrent
background smart home actions.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.const import Platform

from .const import (
    CONF_PHONETIC_NAME,
    CONF_RECIPIENT_NAME,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PHONETIC_NAME,
    DEFAULT_RECIPIENT_NAME,
    DOMAIN,
    LOGGER,
)
from .generator import (
    async_compile_routine_media,
    async_compile_static_clip,
)
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
    from collections.abc import Callable, Coroutine

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
]


def _create_task(
    hass: HomeAssistant,
    coro: Coroutine[Any, Any, Any],
    name: str | None = None,
) -> asyncio.Task[Any]:
    """Create task in Home Assistant or fallback to asyncio loop."""
    if hasattr(hass, "async_create_task") and callable(hass.async_create_task):
        res = hass.async_create_task(coro, name=name)
        if isinstance(res, asyncio.Task):
            return res
    return asyncio.create_task(coro, name=name)


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

    loop = asyncio.get_running_loop()
    target = functools.partial(func, **kwargs) if kwargs else func
    try:
        return await loop.run_in_executor(None, target, *args)
    except Exception:  # noqa: BLE001
        return func(*args, **kwargs)


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


def _is_media_ready_and_fresh(
    output_dir: str,
    routine_name: str,
    max_age_seconds: int = 86400,
    *,
    is_static: bool = False,
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

    domain_data = hass.data.setdefault(DOMAIN, {})
    if routine_config is None:
        routine_config = domain_data.get("routines", {}).get(routine_name, {})
    if recipient is None:
        recipient = domain_data.get(
            "recipient", {"name": "User", "phonetic_name": "User"}
        )
    if output_dir is None:
        output_dir = routine_config.get("output_dir", DEFAULT_OUTPUT_DIR)

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

    output_dir = output_dir or DEFAULT_OUTPUT_DIR

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
        return metadata


async def async_run_routine(  # noqa: PLR0912, PLR0913, PLR0915
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

    domain_data = hass.data.setdefault(DOMAIN, {})
    if routine_config is None:
        routine_config = domain_data.get("routines", {}).get(routine_name, {})
    if recipient is None:
        recipient = domain_data.get(
            "recipient", {"name": "User", "phonetic_name": "User"}
        )
    if output_dir is None:
        output_dir = routine_config.get("output_dir", DEFAULT_OUTPUT_DIR)
    if target_player is None:
        target_player = routine_config.get("target_player")

    sensor = async_get_or_create_sensor(hass, routine_name)

    # 1. Check if media exists and is fresh (<24h) unless force_regenerate is requested
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
        await async_pregenerate_routine(
            hass,
            routine_name=routine_name,
            target_player=data.get("target_player"),
            routine_config=data.get("routine_config"),
            recipient=data.get("recipient"),
            output_dir=data.get("output_dir"),
        )

    async def _async_handle_run(call: ServiceCall) -> None:
        data = dict(call.data)
        routine_name = data.get("routine_name")
        if not routine_name or not str(routine_name).strip():
            msg = "Missing or empty required service parameter 'routine_name'"
            raise ValueError(msg)
        routine_config = data.get("routine_config")
        if "post_routine_actions" in data:
            if routine_config is None:
                domain_data = hass.data.get(DOMAIN, {})
                routine_config = dict(
                    domain_data.get("routines", {}).get(routine_name, {})
                )
            if "post_routine_actions" not in routine_config:
                routine_config["post_routine_actions"] = data["post_routine_actions"]
        await async_run_routine(
            hass,
            routine_name=routine_name,
            target_player=data.get("target_player"),
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
    domain_data.setdefault(
        "recipient", {"name": recipient_name, "phonetic_name": phonetic_name}
    )

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
    return True
