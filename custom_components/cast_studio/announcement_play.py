"""
Play an announcement on a media player, pausing a briefing playing there.

Announcements for one player queue and play in order. The action returns
once the announcement is playing; the rest runs in the background.

Over a briefing, the announcement plays as audio only. The briefing's
lifecycle is held so it cannot end (or show its dashboard) meanwhile; then
its media is cast again and seeked back to where it was, and its end moves
by the time the announcement took.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .announcement_runtime import get_manager
from .briefing import (
    UNAVAILABLE_STATES,
    async_fail_play,
    async_release_player,
    media_of,
    playing_on,
)
from .const import DOMAIN, LOGGER
from .helpers import create_task
from .orchestrator import (
    PlayControl,
    async_play_and_sync_routine,
    has_screen,
    is_cast_player,
)
from .people import slugify_name

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .announcement_build import AnnouncementBuild
    from .announcements import Announcement
    from .briefing import _CurrentPlay

DATA_ANNOUNCE_LOCKS = "announce_locks"
# Player states that mean nothing was using it: CastStudio turns it off after.
_IDLE_STATES = ("off", "idle", "standby")


def _lock(hass: HomeAssistant, target: str) -> asyncio.Lock:
    locks = hass.data.setdefault(DOMAIN, {}).setdefault(DATA_ANNOUNCE_LOCKS, {})
    return locks.setdefault(target, asyncio.Lock())


def _briefing_position(hass: HomeAssistant, target: str) -> float | None:
    """Return where the playing briefing is, in seconds, if the player says."""
    state = hass.states.get(target)
    if state is None or state.state != "playing":
        return None
    position = state.attributes.get("media_position")
    updated = state.attributes.get("media_position_updated_at")
    if position is None or updated is None:
        return None
    if isinstance(updated, str):
        updated = dt_util.parse_datetime(updated)
    if updated is None:
        return None
    return max(0.0, float(position) + (dt_util.utcnow() - updated).total_seconds())


async def async_announce(
    hass: HomeAssistant,
    announcement: Announcement,
    target: str | None = None,
) -> None:
    """
    Play ``announcement`` on ``target``; return once it is playing.

    Without ``target`` it plays on the announcement's Play on player.
    """
    target = target or announcement.target
    if target is None:
        msg = (
            f"Announcement '{announcement.name}' has no Play on player: "
            "set one in its settings or give a target"
        )
        raise HomeAssistantError(msg)
    state = hass.states.get(target)
    if state is None or state.state in UNAVAILABLE_STATES:
        msg = f"Media player {target} is unavailable"
        raise HomeAssistantError(msg)
    build = await get_manager(hass).async_ensure_built(announcement.announcement_id)
    started: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    create_task(
        hass,
        _async_run(hass, announcement, build, target, started),
        name=f"cast_studio_announce_{slugify_name(announcement.name)}",
    )
    await started


async def _async_run(
    hass: HomeAssistant,
    announcement: Announcement,
    build: AnnouncementBuild,
    target: str,
    started: asyncio.Future[None],
) -> None:
    """Play once earlier announcements on ``target`` finish; resolve ``started``."""
    try:
        async with _lock(hass, target):
            briefing = playing_on(hass, target)
            if briefing is None:
                await _async_play_alone(hass, announcement, build, target, started)
            else:
                await _async_play_over(
                    hass, announcement, build, target, started, briefing
                )
    except Exception as err:  # noqa: BLE001 - reported to the caller or logged
        if not started.done():
            started.set_exception(err)
        else:
            LOGGER.warning(
                "Announcement '%s' on %s ended with an error: %s",
                announcement.name,
                target,
                err,
            )


async def _async_play(
    hass: HomeAssistant,
    announcement: Announcement,
    target: str,
    metadata: dict[str, Any],
    started: asyncio.Future[None],
) -> None:
    lifecycle = await async_play_and_sync_routine(
        hass,
        f"announcement_{slugify_name(announcement.name)}",
        target,
        metadata,
        [],
        control=PlayControl(started),
        strict_media=True,
    )
    await lifecycle


async def _async_play_alone(
    hass: HomeAssistant,
    announcement: Announcement,
    build: AnnouncementBuild,
    target: str,
    started: asyncio.Future[None],
) -> None:
    """Play with nothing else on ``target``: video on a screen if it has one."""
    state = hass.states.get(target)
    woke = state is not None and state.state in _IDLE_STATES
    if (
        announcement.output_format == "visual"
        and build.clip_path is not None
        and has_screen(hass, target)
    ):
        metadata = {
            "video_path": build.clip_path,
            "duration": build.clip_duration or build.audio_duration,
        }
    else:
        metadata = {"audio_path": build.audio_path, "duration": build.audio_duration}
    await _async_play(hass, announcement, target, metadata, started)
    if woke and is_cast_player(hass, target):
        await async_release_player(hass, target)


async def _async_play_over(  # noqa: PLR0913
    hass: HomeAssistant,
    announcement: Announcement,
    build: AnnouncementBuild,
    target: str,
    started: asyncio.Future[None],
    briefing: _CurrentPlay,
) -> None:
    """Pause ``briefing`` for the announcement's audio, then resume it."""
    position = _briefing_position(hass, target)
    if position is None:
        LOGGER.warning(
            "Could not tell where the briefing on %s was; "
            "it resumes from the start after '%s'",
            target,
            announcement.name,
        )
    with briefing.control.hold():
        metadata = {
            "audio_path": build.audio_path,
            "duration": build.audio_duration,
            "prepare_player": False,
        }
        await _async_play(hass, announcement, target, metadata, started)
        await _async_resume(hass, briefing, target, position)


async def _async_resume(
    hass: HomeAssistant,
    briefing: _CurrentPlay,
    target: str,
    position: float | None,
) -> None:
    """Cast the paused briefing again from where it was and move its end."""
    if playing_on(hass, target) is not briefing or briefing.superseded_by:
        return  # it ended or was replaced meanwhile: nothing to resume
    media = media_of(briefing)
    if media is None:
        return
    path, is_video = media
    try:
        resumed = await async_play_and_sync_routine(
            hass,
            f"resume_{briefing.routine_id}",
            target,
            {
                ("video_path" if is_video else "audio_path"): path,
                "duration": 0,
                "prepare_player": False,
            },
            [],
            strict_media=True,
        )
        await resumed
        if position:
            await hass.services.async_call(
                "media_player",
                "media_seek",
                {"entity_id": target, "seek_position": position},
                blocking=True,
            )
    except Exception as err:  # noqa: BLE001 - the briefing must not stay stuck
        LOGGER.warning("Could not resume the briefing on %s: %s", target, err)
        async_fail_play(briefing, str(err))
        return
    briefing.control.resumed_at(position or 0.0)
