"""Persistent record of which segment types each routine played today."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

from .people import briefing_day

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from .people import Person, Routine

STORAGE_VERSION = 1
STORAGE_KEY = "cast_studio.play_history"
SAVE_DELAY = 1
# Records sit under this key, one per routine. Older files kept one record
# per person at the top level; those are dropped on load.
ROUTINES = "routines"


class PlayHistory:
    """Tracks segment types played per routine for the person's briefing day."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize empty history."""
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        """Load history from storage."""
        stored = await self._store.async_load() or {}
        routines = stored.get(ROUTINES)
        self._data = routines if isinstance(routines, dict) else {}

    def played(self, person: Person, routine: Routine, now: datetime) -> set[str]:
        """Return segment types the routine already played this briefing day."""
        entry = self._data.get(routine.routine_id)
        if not entry or entry.get("day") != briefing_day(person, now).isoformat():
            return set()
        return set(entry.get("played", []))

    async def async_mark_played(
        self,
        person: Person,
        routine: Routine,
        segment_types: Iterable[str],
        now: datetime,
    ) -> None:
        """Record segment types as played and schedule a save."""
        self._set(
            person, routine, self.played(person, routine, now) | set(segment_types), now
        )

    async def async_unmark_played(
        self,
        person: Person,
        routine: Routine,
        segment_types: Iterable[str],
        now: datetime,
    ) -> None:
        """Forget segment types the routine played this briefing day."""
        played = self.played(person, routine, now)
        if played:
            self._set(person, routine, played - set(segment_types), now)

    def _set(
        self, person: Person, routine: Routine, played: set[str], now: datetime
    ) -> None:
        self._data[routine.routine_id] = {
            "day": briefing_day(person, now).isoformat(),
            "played": sorted(played),
        }
        self._store.async_delay_save(lambda: {ROUTINES: self._data}, SAVE_DELAY)
