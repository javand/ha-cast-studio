"""
Routine Orchestrator for CastStudio.

Handles action partitioning, concurrent background action execution via asyncio.gather,
Google Cast / media player playback triggering, and playback state tracking to fire
synchronized display actions at precise slide timestamps with zero buffer drift.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.network import get_url

from .const import LOGGER
from .helpers import create_task

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant


_ACTIVE_SYNC_TASKS: set[asyncio.Task[Any]] = set()
_ROUTINE_SYNC_TASKS: dict[str, asyncio.Task[Any]] = {}

DEFAULT_PLAYING_TIMEOUT = 25.0

# Set in a play's metadata when a newer play replaces it: its lifecycle then
# skips the post-routine actions, which would otherwise run over the newer play.
SUPERSEDED_FLAG = "_superseded"


class OrchestratorError(Exception):
    """Base exception for orchestrator operations."""


def get_routine_sync_task(routine_name: str) -> asyncio.Task[Any] | None:
    """
    Return the active synchronization task for a given routine, if running.

    Args:
        routine_name: Name of the routine.

    Returns:
        The active asyncio.Task or None.

    """
    return _ROUTINE_SYNC_TASKS.get(routine_name)


def _register_routine_task(
    task: asyncio.Task[Any],
    routine_name: str,
) -> None:
    """Register sync task and hook lifecycle cleanup callback."""
    _ACTIVE_SYNC_TASKS.add(task)
    _ROUTINE_SYNC_TASKS[routine_name] = task

    def _on_done(t: asyncio.Task[Any]) -> None:
        _ACTIVE_SYNC_TASKS.discard(t)
        if _ROUTINE_SYNC_TASKS.get(routine_name) is t:
            _ROUTINE_SYNC_TASKS.pop(routine_name, None)

    task.add_done_callback(_on_done)


def get_media_payload(metadata: dict[str, Any]) -> tuple[str, str]:
    """
    Determine media path and content type from routine metadata.

    Args:
        metadata: Routine companion metadata dictionary.

    Returns:
        Tuple of (media_path, content_type).

    Raises:
        OrchestratorError: If no valid media path is found.

    """
    if video_path := metadata.get("video_path"):
        ctype = (
            metadata.get("media_content_type")
            or metadata.get("content_type")
            or "video/mp4"
        )
        return video_path, ctype

    if audio_path := metadata.get("audio_path"):
        ext = Path(audio_path).suffix.lstrip(".").lower() or "mp3"
        ctype = (
            metadata.get("media_content_type")
            or metadata.get("content_type")
            or f"audio/{ext}"
        )
        return audio_path, ctype

    if media_path := (metadata.get("media_path") or metadata.get("media_content_id")):
        ext = Path(media_path).suffix.lstrip(".").lower()
        default_type = "video/mp4" if ext == "mp4" else f"audio/{ext or 'mp3'}"
        ctype = (
            metadata.get("media_content_type")
            or metadata.get("content_type")
            or default_type
        )
        return media_path, ctype

    msg = (
        "Metadata does not contain a valid 'video_path', 'audio_path', or 'media_path'"
    )
    raise OrchestratorError(msg)


def resolve_cast_media_url(hass: HomeAssistant, media_path: str) -> str:
    """
    Resolve local disk or relative media path to an accessible HTTP URL for Google Cast.

    Google Cast and DLNA devices cannot access local filesystem paths directly.
    Files in Home Assistant's /config/www (or /www) are served over HTTP at /local/.
    """
    if media_path.startswith(("http://", "https://", "media-source://")):
        return media_path
    norm = media_path.replace("\\", "/")
    target_url: str | None = None
    if norm.startswith("/local/"):
        rel = norm.removeprefix("/local/")
        try:
            base = get_url(hass, prefer_external=False).rstrip("/")
        except Exception:  # noqa: BLE001
            target_url = f"/local/{rel}"
        else:
            target_url = f"{base}/local/{rel}"
    elif "/www/" in norm:
        rel = norm.split("/www/", 1)[1].lstrip("/")
        try:
            base = get_url(hass, prefer_external=False).rstrip("/")
        except Exception:  # noqa: BLE001
            target_url = f"/local/{rel}"
        else:
            target_url = f"{base}/local/{rel}"

    if target_url is not None:
        cache_bust = int(time.time())
        sep = "&" if "?" in target_url else "?"
        return f"{target_url}{sep}v={cache_bust}"

    return media_path


def extract_actions(steps_or_actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Extract action definitions from steps or a direct list of actions.

    Filters out steps marked as visual=True, only extracting non-visual steps,
    background_actions, or direct action definitions.

    Args:
        steps_or_actions: List of step dictionaries or direct action dictionaries.

    Returns:
        List of unpacked action dictionaries.

    """
    actions: list[dict[str, Any]] = []
    if not isinstance(steps_or_actions, list):
        return actions

    for item in steps_or_actions:
        if not isinstance(item, dict):
            continue

        has_container = False
        if "actions" in item and isinstance(item["actions"], list):
            has_container = True
            # Only include if not a visual step
            if not item.get("visual", False):
                actions.extend(
                    [act for act in item["actions"] if isinstance(act, dict)]
                )
        if "background_actions" in item and isinstance(
            item["background_actions"], list
        ):
            has_container = True
            actions.extend(
                [act for act in item["background_actions"] if isinstance(act, dict)]
            )
        if (
            not has_container
            and ("action" in item or "service" in item)
            and not item.get("visual", False)
        ):
            # Direct action item
            actions.append(item)

    return actions


def _bind_target_player_to_post_actions(
    post_actions: list[dict[str, Any]],
    target_player: str | None,
) -> list[dict[str, Any]]:
    """Bind target_player to post-routine actions targeting media players or Cast."""
    if not target_player:
        return post_actions

    resolved: list[dict[str, Any]] = []
    for act in post_actions:
        act_copy = dict(act)
        act_name = str(act_copy.get("action") or act_copy.get("service") or "")
        if act_name.startswith(("cast.", "media_player.")):
            has_target = bool(
                isinstance(act_copy.get("target"), dict)
                and (
                    act_copy["target"].get("entity_id")
                    or act_copy["target"].get("device_id")
                    or act_copy["target"].get("area_id")
                )
            )
            has_data_entity = bool(
                isinstance(act_copy.get("data"), dict)
                and act_copy["data"].get("entity_id")
            )
            if not has_target and not has_data_entity:
                act_data = dict(
                    act_copy.get("data") or act_copy.get("service_data") or {}
                )
                act_data["entity_id"] = target_player
                act_copy["data"] = act_data
                act_copy["target"] = {"entity_id": target_player}
        resolved.append(act_copy)
    return resolved


async def _async_execute_single_action(
    hass: HomeAssistant,
    action_def: dict[str, Any],
) -> None:
    """
    Unpack and execute a single Home Assistant service action.

    Handles action or service naming, target dictionary, and data or service_data.

    Args:
        hass: Home Assistant instance.
        action_def: Action configuration dictionary.

    """
    action_name = action_def.get("action") or action_def.get("service")
    if not action_name or not isinstance(action_name, str) or "." not in action_name:
        LOGGER.warning("Skipping invalid action/service definition: %s", action_def)
        return

    domain, service = action_name.split(".", 1)
    target = action_def.get("target")
    data = action_def.get("data") or action_def.get("service_data")

    # Support root-level entity_id when target is not explicitly provided
    if target is None and "entity_id" in action_def:
        target = {"entity_id": action_def["entity_id"]}

    kwargs: dict[str, Any] = {}
    if data is not None:
        kwargs["service_data"] = data
    if target is not None:
        kwargs["target"] = target

    await hass.services.async_call(domain, service, **kwargs)


async def async_execute_background_actions(
    hass: HomeAssistant,
    steps_or_actions: list[dict[str, Any]],
) -> None:
    """
    Execute background actions concurrently using asyncio.gather.

    Logs any exceptions encountered without failing the overall routine.

    Args:
        hass: Home Assistant instance.
        steps_or_actions: List of steps or direct action definitions.

    """
    actions = extract_actions(steps_or_actions)
    if not actions:
        return

    tasks = [_async_execute_single_action(hass, act) for act in actions]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for res in results:
        if isinstance(res, Exception):
            LOGGER.error("Error executing background action: %s", res)


def extract_slide_schedule(
    metadata: dict[str, Any],
    steps: list[dict[str, Any]],
) -> list[tuple[float, list[dict[str, Any]]]]:
    """
    Extract schedule of display actions keyed by offset timestamp.

    Args:
        metadata: Routine metadata dictionary.
        steps: Original routine step definitions.

    Returns:
        List of tuples containing (offset_seconds, actions_list) sorted by offset.

    """
    schedule: list[tuple[float, list[dict[str, Any]]]] = []
    slide_timestamps = metadata.get("slide_timestamps", [])

    if slide_timestamps:
        for slide in slide_timestamps:
            offset = float(slide.get("timestamp", slide.get("start_time", 0.0)))
            actions = slide.get("actions_on_display") or slide.get("actions")
            if not actions and steps:
                slide_name = slide.get("step_name") or slide.get("name")
                matching_step = next(
                    (s for s in steps if s.get("name") == slide_name),
                    None,
                )
                if matching_step:
                    actions = matching_step.get(
                        "actions_on_display"
                    ) or matching_step.get("actions")
            if actions:
                actions_list = actions if isinstance(actions, list) else [actions]
                schedule.append((offset, actions_list))
    elif steps:
        current_time = 0.0
        for step in steps:
            offset = float(step.get("timestamp", step.get("start_time", current_time)))
            actions = step.get("actions_on_display")
            if actions:
                actions_list = actions if isinstance(actions, list) else [actions]
                schedule.append((offset, actions_list))
            duration = float(step.get("duration", 0.0))
            current_time = offset + duration

    schedule.sort(key=lambda item: item[0])
    return schedule


async def _async_sync_slides(
    hass: HomeAssistant,
    schedule: list[tuple[float, list[dict[str, Any]]]],
) -> None:
    """
    Execute synchronized actions_on_display at their designated timestamps.

    Sleep delays are anchored to monotonic loop time to prevent drift.

    Args:
        hass: Home Assistant instance.
        schedule: Sorted schedule of (offset_seconds, actions).

    """
    if not schedule:
        return

    loop = asyncio.get_running_loop()
    start_time = loop.time()

    for offset, actions in schedule:
        now = loop.time() - start_time
        delay = offset - now
        if delay > 0:
            await asyncio.sleep(delay)
        await async_execute_background_actions(hass, actions)


_create_task = create_task


def _is_cast_player(hass: HomeAssistant, entity_id: str) -> bool:
    """Check if target media player entity is a Google Cast device."""
    if not entity_id:
        return False
    with contextlib.suppress(Exception):
        registry = er.async_get(hass)
        entry = registry.async_get(entity_id)
        if entry and getattr(entry, "platform", None) == "cast":
            return True
    if hasattr(hass, "states") and hasattr(hass.states, "get"):
        state_obj = hass.states.get(entity_id)
        if state_obj:
            attrs = getattr(state_obj, "attributes", None) or {}
            if isinstance(attrs, dict) and (
                attrs.get("app_id")
                or attrs.get("app_name")
                or "cast" in str(attrs.get("device_class", "")).lower()
            ):
                return True
    return False


def _is_routine_media_playing(  # noqa: PLR0911
    state_obj: Any,
    expected_media_url: str | None = None,
    media_path: str | None = None,
) -> bool:
    """
    Check if the given state represents our routine media playing.

    Returns False if the player is displaying Lovelace Cast dashboard, or playing
    unrelated media. Accepts playing/buffering or receiver loading when media_content_id
    matches our routine.
    """
    if state_obj is None:
        return False

    state_val = getattr(state_obj, "state", None)
    attrs = getattr(state_obj, "attributes", None)
    if not isinstance(attrs, dict):
        return state_val == "playing"

    # 1. Cast Lovelace receiver and HA Cast splash are NEVER routine media playback
    app_name = (attrs.get("app_name") or "").lower()
    app_id = (attrs.get("app_id") or "").upper()
    media_content_id_str = str(attrs.get("media_content_id") or "").lower()
    if (
        "splash.png" in media_content_id_str
        or "lovelace" in app_name
        or app_name in ("home assistant", "home assistant cast")
        or app_id in ("A078F6B0", "B12CE3C8")
    ):
        return False

    # 2. Extract expected filename for comparison
    expected_candidates: list[str] = []
    if expected_media_url:
        expected_candidates.append(
            Path(expected_media_url.split("?")[0].rstrip("/")).name
        )
    if media_path:
        expected_candidates.append(Path(media_path.replace("\\", "/").rstrip("/")).name)
    valid_candidates = [c for c in expected_candidates if c]

    # 3. Check media_content_id if available
    current_media_id = attrs.get("media_content_id")
    if current_media_id and valid_candidates:
        current_clean = str(current_media_id).split("?")[0].rstrip("/")
        if any(cand in current_clean for cand in valid_candidates):
            # Actively loading or playing our specific routine media!
            return state_val in ("playing", "buffering", "idle")
        # Check if proxy or internal stream from Default Media Receiver
        is_dmr = app_id == "CC1AD845" or "default media receiver" in app_name
        return is_dmr and state_val in ("playing", "buffering")

    # 4. If Default Media Receiver is active and state is playing or buffering
    if app_id == "CC1AD845" or "default media receiver" in app_name:
        return state_val in ("playing", "buffering")

    # 5. Non-Cast or generic player playing state check
    if state_val != "playing":
        return False

    # Filter out ambient / idle cast screens
    return app_name not in ("backdrop", "ambient", "google cast", "homescreen")


async def async_play_and_sync_routine(  # noqa: C901, PLR0915
    hass: HomeAssistant,
    routine_name: str,
    target_player: str,
    metadata: dict[str, Any],
    steps: list[dict[str, Any]],
) -> asyncio.Task[Any]:
    """
    Play routine media and synchronize slide actions upon 'playing' state.

    Args:
        hass: Home Assistant instance.
        routine_name: Name of the routine.
        target_player: Media player entity ID.
        metadata: Routine companion metadata dictionary.
        steps: List of routine step definitions.

    Returns:
        An awaitable task representing the full playback lifecycle.

    """
    media_path, content_type = get_media_payload(metadata)
    resolved_media_url = resolve_cast_media_url(hass, media_path)
    timeout_val = float(
        metadata.get(
            "playing_timeout",
            metadata.get("timeout", DEFAULT_PLAYING_TIMEOUT),
        )
    )
    duration = float(metadata.get("duration", 0.0))

    # 0. Wake up, unmute, and initialize volume if configured
    if metadata.get("prepare_player", True) and target_player:
        if not _is_cast_player(hass, target_player):
            with contextlib.suppress(Exception):
                await hass.services.async_call(
                    "media_player", "turn_on", {"entity_id": target_player}
                )
        if metadata.get("unmute", True):
            with contextlib.suppress(Exception):
                await hass.services.async_call(
                    "media_player",
                    "volume_mute",
                    {"entity_id": target_player, "is_volume_muted": False},
                )
        with contextlib.suppress(Exception):
            vol_val = metadata.get("volume_level")
            target_vol = float(vol_val if vol_val is not None else 0.60)
            await hass.services.async_call(
                "media_player",
                "volume_set",
                {"entity_id": target_player, "volume_level": target_vol},
            )

    # 1. Dispatch playback request to media player
    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "entity_id": target_player,
            "media_content_id": resolved_media_url,
            "media_content_type": content_type,
        },
    )

    schedule = extract_slide_schedule(metadata, steps)
    loop = asyncio.get_running_loop()
    playing_future: asyncio.Future[asyncio.Task[Any] | None] = loop.create_future()
    start_time = [loop.time()]

    # 2. Check if player is already 'playing' at call time
    current_state = hass.states.get(target_player)
    if _is_routine_media_playing(current_state, resolved_media_url, media_path):
        LOGGER.debug(
            "Player '%s' is already in 'playing' state; starting sync",
            target_player,
        )
        task = _create_task(
            hass,
            _async_sync_slides(hass, schedule),
            name=f"sync_slides_{routine_name}",
        )
        _register_routine_task(task, routine_name)
        playing_future.set_result(task)
    else:
        # 3. Register state listener and safety timeout
        cleanups: dict[str, Any] = {"unsub_state": None, "unsub_timeout": None}
        sync_started = False

        def _cleanup() -> None:
            if cleanups["unsub_state"] is not None:
                cleanups["unsub_state"]()
                cleanups["unsub_state"] = None
            if cleanups["unsub_timeout"] is not None:
                cleanups["unsub_timeout"]()
                cleanups["unsub_timeout"] = None

        def _start_sync() -> asyncio.Task[Any] | None:
            nonlocal sync_started
            if sync_started:
                return None
            sync_started = True
            start_time[0] = loop.time()
            _cleanup()

            task = _create_task(
                hass,
                _async_sync_slides(hass, schedule),
                name=f"sync_slides_{routine_name}",
            )
            _register_routine_task(task, routine_name)
            if not playing_future.done():
                playing_future.set_result(task)
            return task

        @callback
        def _handle_state_changed(event: Event) -> asyncio.Task[Any] | None:
            new_state = event.data.get("new_state")
            if _is_routine_media_playing(new_state, resolved_media_url, media_path):
                LOGGER.debug(
                    "Player '%s' entered 'playing'; starting slide sync for '%s'",
                    target_player,
                    routine_name,
                )
                return _start_sync()
            return None

        @callback
        def _handle_timeout(_now: Any) -> None:
            if sync_started:
                return
            LOGGER.warning(
                "Safety timeout (%.1fs) expired waiting for '%s' to enter 'playing'",
                timeout_val,
                target_player,
            )
            _cleanup()
            if not playing_future.done():
                playing_future.set_result(None)

        cleanups["unsub_state"] = async_track_state_change_event(
            hass, [target_player], _handle_state_changed
        )
        cleanups["unsub_timeout"] = async_call_later(hass, timeout_val, _handle_timeout)
        # A cancelled lifecycle cancels the future: stop waiting for 'playing'.
        playing_future.add_done_callback(lambda _future: _cleanup())

    async def _coordinate_playback_lifecycle() -> None:
        try:
            sync_task = await playing_future
            if sync_task is not None:
                await sync_task
                if duration > 0:
                    now = loop.time() - start_time[0]
                    remaining = duration - now
                    if remaining > 0:
                        await asyncio.sleep(remaining)
            elif duration > 0:
                elapsed = loop.time() - start_time[0]
                remaining = max(0.0, duration - elapsed)
                if remaining > 0:
                    await asyncio.sleep(remaining)
        except asyncio.CancelledError:
            if not playing_future.done():
                playing_future.cancel()
            raise
        finally:
            post_actions = (
                metadata.get("post_routine_actions")
                or metadata.get("post_actions")
                or metadata.get("post_briefing_actions")
            )
            if post_actions and not metadata.get(SUPERSEDED_FLAG):
                if isinstance(post_actions, dict):
                    post_actions = [post_actions]
                try:
                    if (
                        metadata.get("prepare_player", True)
                        and target_player
                        and not _is_cast_player(hass, target_player)
                    ):
                        with contextlib.suppress(Exception):
                            await hass.services.async_call(
                                "media_player", "turn_on", {"entity_id": target_player}
                            )
                    resolved_post_actions = _bind_target_player_to_post_actions(
                        post_actions, target_player
                    )
                    await asyncio.shield(
                        async_execute_background_actions(hass, resolved_post_actions)
                    )
                except Exception as err:  # noqa: BLE001
                    LOGGER.error("Error executing post-routine actions: %s", err)

    return _create_task(
        hass,
        _coordinate_playback_lifecycle(),
        name=f"playback_lifecycle_{routine_name}",
    )
