"""Persistent record of which segment types each person heard today."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

from .people import briefing_day

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from .people import Person

STORAGE_VERSION = 1
STORAGE_KEY = "cast_studio.play_history"
SAVE_DELAY = 1


class PlayHistory:
    """Tracks segment types played per person for the current briefing day."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize empty history."""
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        """Load history from storage."""
        self._data = await self._store.async_load() or {}

    def played(self, person: Person, now: datetime) -> set[str]:
        """Return segment types already played in the person's current briefing day."""
        entry = self._data.get(person.person_id)
        if not entry or entry.get("day") != briefing_day(person, now).isoformat():
            return set()
        return set(entry.get("played", []))

    async def async_mark_played(
        self, person: Person, segment_types: Iterable[str], now: datetime
    ) -> None:
        """Record segment types as played and schedule a save."""
        played = self.played(person, now) | set(segment_types)
        self._data[person.person_id] = {
            "day": briefing_day(person, now).isoformat(),
            "played": sorted(played),
        }
        self._store.async_delay_save(lambda: self._data, SAVE_DELAY)
