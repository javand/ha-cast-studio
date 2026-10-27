"""
When announcements build, and what state each one is in.

A build runs when an announcement has no build on disk, when its settings
changed, when a daily announcement's rebuild time passes, or on request.
A restart reads the last build back from its sidecar and builds nothing new
unless one of those holds. Plays never wait for a rebuild: the previous build
stays in use until the new one is ready, and after a failed rebuild.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .ai_client import async_discover_ai_credentials
from .announcement_build import (
    AnnouncementBuild,
    announcement_dir,
    async_build_announcement,
    read_build,
)
from .announcements import announcement_day, get_announcements, settings_fingerprint
from .const import DOMAIN, LOGGER
from .helpers import async_run_in_executor, create_task

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from datetime import datetime, time
    from pathlib import Path

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .announcements import Announcement

DATA_ANNOUNCEMENTS = "announcements"
SIGNAL_ANNOUNCEMENT_UPDATED = f"{DOMAIN}_announcement_updated"
# Callbacks the image and button platforms leave to add a new announcement's
# entities without a reload.
DATA_ENTITY_ADDERS = "announcement_entity_adders"


@dataclass
class AnnouncementState:
    """The current build of one announcement and how its last build went."""

    build: AnnouncementBuild | None
    status: Literal["idle", "building", "ready", "error", "fallback"]
    error: str | None = None


async def _async_cancel(tasks: Iterable[asyncio.Task[None]]) -> None:
    """Cancel tasks and wait for them to finish."""
    tasks = list(tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def get_manager(hass: HomeAssistant) -> AnnouncementManager:
    """Return the running announcement manager."""
    manager = hass.data.get(DOMAIN, {}).get(DATA_ANNOUNCEMENTS)
    if manager is None:
        msg = "CastStudio is not set up: add the integration first"
        raise HomeAssistantError(msg)
    return manager


class AnnouncementManager:
    """Builds announcements when they need it and tracks their state."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, output_dir: str
    ) -> None:
        """Initialise for one config entry."""
        self.hass = hass
        self.entry = entry
        self.output_dir = output_dir
        self._states: dict[str, AnnouncementState] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: dict[str, set[asyncio.Task[None]]] = {}
        self._timers: dict[str, tuple[time, Callable[[], None]]] = {}
        # Player chosen on the device page, with the default it replaced.
        self._play_on: dict[str, tuple[str | None, str]] = {}

    # --- lookups -------------------------------------------------------------

    def _announcements(self) -> dict[str, Announcement]:
        return {a.announcement_id: a for a in get_announcements(self.entry)}

    def announcement(self, announcement_id: str) -> Announcement | None:
        """Return an announcement's current settings, or None if it is gone."""
        return self._announcements().get(announcement_id)

    def state(self, announcement_id: str) -> AnnouncementState | None:
        """Return an announcement's state, or None if it is unknown."""
        return self._states.get(announcement_id)

    def play_target(self, announcement: Announcement) -> str | None:
        """
        Return the player the Play button uses.

        A player chosen on the device page wins until the announcement's
        own Play on default changes; then the new default applies.
        """
        chosen = self._play_on.get(announcement.announcement_id)
        if chosen is not None and chosen[0] == announcement.target:
            return chosen[1]
        return announcement.target

    def set_play_target(self, announcement: Announcement, target: str) -> None:
        """Choose the player the Play button uses."""
        self._play_on[announcement.announcement_id] = (announcement.target, target)
        self._signal(announcement.announcement_id)

    def needs_build(self, announcement: Announcement, now: datetime) -> bool:
        """Return True when the announcement's current build is out of date."""
        state = self._states.get(announcement.announcement_id)
        build = state.build if state else None
        if build is None or build.fingerprint != settings_fingerprint(announcement):
            return True
        return (
            announcement.freshness == "daily"
            and build.build_day != announcement_day(announcement, now)
        )

    def _dir(self, announcement_id: str) -> Path:
        return announcement_dir(self.output_dir, announcement_id)

    def _signal(self, announcement_id: str) -> None:
        async_dispatcher_send(self.hass, SIGNAL_ANNOUNCEMENT_UPDATED, announcement_id)

    # --- lifecycle -------------------------------------------------------------

    async def async_setup(self) -> None:
        """Read every sidecar, start daily timers and build what needs it."""
        current = self._announcements()
        await self._async_add(list(current.values()))
        self._schedule_needed(current)

    async def _async_add(self, announcements: list[Announcement]) -> None:
        """Start tracking announcements, reading their sidecars in one job."""
        builds = await async_run_in_executor(
            self.hass,
            lambda: [read_build(self._dir(a.announcement_id)) for a in announcements],
        )
        for announcement, build in zip(announcements, builds, strict=True):
            self._states[announcement.announcement_id] = AnnouncementState(
                build=build, status=build.status if build else "idle"
            )
            self._update_timer(announcement)
            self._signal(announcement.announcement_id)

    def _schedule_needed(self, current: dict[str, Announcement]) -> None:
        now = dt_util.now()
        for announcement in current.values():
            if self.needs_build(announcement, now):
                self._schedule(announcement.announcement_id)

    async def async_sync(self) -> None:
        """Follow the entry's announcements after any subentry change."""
        current = self._announcements()
        for announcement_id in set(self._states) - set(current):
            await self._async_remove(announcement_id)
        new: list[Announcement] = []
        for announcement in current.values():
            if announcement.announcement_id in self._states:
                self._update_timer(announcement)
                self._signal(announcement.announcement_id)  # settings may differ
            else:
                new.append(announcement)
        if new:
            await self._async_add(new)
            for adder in self.hass.data.get(DOMAIN, {}).get(DATA_ENTITY_ADDERS, []):
                adder(new)
        self._schedule_needed(current)

    async def _async_remove(self, announcement_id: str) -> None:
        """Forget a removed announcement, its pending builds and its files."""
        if timer := self._timers.pop(announcement_id, None):
            timer[1]()
        await _async_cancel(self._tasks.pop(announcement_id, ()))
        self._states.pop(announcement_id, None)
        self._locks.pop(announcement_id, None)
        self._play_on.pop(announcement_id, None)
        await async_run_in_executor(
            self.hass, shutil.rmtree, self._dir(announcement_id), ignore_errors=True
        )
        self._signal(announcement_id)

    async def async_unload(self) -> None:
        """Stop timers and in-flight builds."""
        for _, unsub in self._timers.values():
            unsub()
        self._timers.clear()
        await _async_cancel(task for group in self._tasks.values() for task in group)
        self._tasks.clear()

    def _update_timer(self, announcement: Announcement) -> None:
        """Keep one daily timer per daily announcement, at its rebuild time."""
        announcement_id = announcement.announcement_id
        wanted = (
            announcement.rebuild_time if announcement.freshness == "daily" else None
        )
        existing = self._timers.get(announcement_id)
        if existing and existing[0] == wanted:
            return
        if existing:
            existing[1]()
            del self._timers[announcement_id]
        if wanted is None:
            return

        @callback
        def _rebuild_time(_now: datetime) -> None:
            if (a := self.announcement(announcement_id)) and self.needs_build(
                a, dt_util.now()
            ):
                self._schedule(announcement_id)

        unsub = async_track_time_change(
            self.hass,
            _rebuild_time,
            hour=wanted.hour,
            minute=wanted.minute,
            second=wanted.second,
        )
        self._timers[announcement_id] = (wanted, unsub)

    # --- building --------------------------------------------------------------

    def _track(self, announcement_id: str, *, force: bool) -> asyncio.Task[None]:
        """Run a build as a task removal and unload can cancel."""
        task = create_task(
            self.hass,
            self._async_run(announcement_id, force=force),
            name=f"cast_studio_announcement_{announcement_id}",
        )
        group = self._tasks.setdefault(announcement_id, set())
        group.add(task)
        task.add_done_callback(group.discard)
        return task

    def _schedule(self, announcement_id: str) -> None:
        """Build in the background if the announcement still needs it then."""
        self._track(announcement_id, force=False)

    async def _async_wait(self, announcement_id: str, *, force: bool) -> None:
        """Run a tracked build and wait; a removal ends the wait quietly."""
        task = self._track(announcement_id, force=force)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                task.cancel()
                raise  # the caller itself was cancelled
            # The build was cancelled because the announcement was removed.

    async def _async_run(self, announcement_id: str, *, force: bool) -> None:
        """Run one build, serialized per announcement."""
        lock = self._locks.setdefault(announcement_id, asyncio.Lock())
        async with lock:
            announcement = self.announcement(announcement_id)
            state = self._states.get(announcement_id)
            if announcement is None or state is None:
                return
            now = dt_util.now()
            if not force and not self.needs_build(announcement, now):
                return
            state.status = "building"
            self._signal(announcement_id)
            try:
                creds = await async_discover_ai_credentials(self.hass)
                build = await async_build_announcement(
                    self.hass,
                    self._dir(announcement_id),
                    announcement,
                    creds,
                    now,
                    state.build,
                )
            except Exception as err:  # noqa: BLE001 - the previous build stays
                LOGGER.warning(
                    "Could not build announcement '%s': %s", announcement.name, err
                )
                state.status = "error"
                state.error = str(err)
            else:
                if self._states.get(announcement_id) is not state:
                    return  # removed while building; its folder is gone
                state.build = build
                state.status = build.status
                state.error = None
            self._signal(announcement_id)

    async def async_rebuild(self, announcement_id: str) -> None:
        """Build the announcement again now and wait for it."""
        await self._async_wait(announcement_id, force=True)

    async def async_ensure_built(self, announcement_id: str) -> AnnouncementBuild:
        """Return the current build, building first if there has never been one."""
        state = self._states.get(announcement_id)
        if state is not None and state.build is not None:
            return state.build
        await self._async_wait(announcement_id, force=False)
        state = self._states.get(announcement_id)
        if state is None or state.build is None:
            reason = state.error if state and state.error else "no build"
            msg = f"CastStudio announcement could not be built: {reason}"
            raise HomeAssistantError(msg)
        return state.build
