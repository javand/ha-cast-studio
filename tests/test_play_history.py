"""Tests for the persistent played-today history."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from custom_components.cast_studio.people import Person, Routine
from custom_components.cast_studio.play_history import STORAGE_KEY, PlayHistory

from .conftest import ALEX_ID

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

ALEX = Person(
    person_id=ALEX_ID,
    name="Alex",
    phonetic_name="Alex",
    media_player=None,
    commute_destination_zone=None,
    transportation_mode="fastest",
    reset_time=time(3, 0),
    max_age_minutes=0,
)


def _routine(routine_id: str) -> Routine:
    return Routine(
        routine_id=routine_id,
        person_id=ALEX_ID,
        name=routine_id,
        segments=(),
        dashboard_path=None,
        dashboard_view=None,
        dashboard_display=None,
    )


QUICK = _routine("r_quick")
NIGHT = _routine("r_night")


def _morning() -> datetime:
    return datetime(2026, 3, 10, 6, 30, tzinfo=dt_util.get_default_time_zone())


async def test_play_history_skips_already_played_and_resets_next_day(
    hass: HomeAssistant,
) -> None:
    history = PlayHistory(hass)
    await history.async_load()
    assert history.played(ALEX, QUICK, _morning()) == set()
    await history.async_mark_played(ALEX, QUICK, ["greeting", "weather"], _morning())
    await history.async_mark_played(ALEX, QUICK, ["weather", "closing"], _morning())
    assert history.played(ALEX, QUICK, _morning()) == {"greeting", "weather", "closing"}
    assert history.played(ALEX, QUICK, _morning() + timedelta(days=1)) == set()


async def test_play_history_survives_restart(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    history = PlayHistory(hass)
    await history.async_load()
    await history.async_mark_played(ALEX, QUICK, ["greeting", "weather"], _morning())
    # Flush the delayed save.
    await hass.async_stop()
    assert STORAGE_KEY in hass_storage

    reloaded = PlayHistory(hass)
    await reloaded.async_load()
    assert reloaded.played(ALEX, QUICK, _morning()) == {"greeting", "weather"}


async def test_play_history_unmark_removes_only_given_types_today(
    hass: HomeAssistant,
) -> None:
    history = PlayHistory(hass)
    await history.async_load()
    await history.async_mark_played(ALEX, QUICK, ["greeting", "weather"], _morning())
    await history.async_unmark_played(ALEX, QUICK, ["weather", "closing"], _morning())
    assert history.played(ALEX, QUICK, _morning()) == {"greeting"}
    # Another briefing day's unmark leaves today's record alone.
    await history.async_unmark_played(
        ALEX, QUICK, ["greeting"], _morning() + timedelta(days=1)
    )
    assert history.played(ALEX, QUICK, _morning()) == {"greeting"}


async def test_play_history_tracks_each_routine_separately(
    hass: HomeAssistant,
) -> None:
    history = PlayHistory(hass)
    await history.async_load()
    await history.async_mark_played(ALEX, QUICK, ["greeting", "weather"], _morning())
    assert history.played(ALEX, NIGHT, _morning()) == set()
    await history.async_mark_played(ALEX, NIGHT, ["closing"], _morning())
    await history.async_unmark_played(ALEX, NIGHT, ["closing"], _morning())
    assert history.played(ALEX, QUICK, _morning()) == {"greeting", "weather"}


async def test_play_history_drops_old_person_keyed_records(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "key": STORAGE_KEY,
        "data": {ALEX_ID: {"day": "2026-03-10", "played": ["greeting"]}},
    }
    history = PlayHistory(hass)
    await history.async_load()
    assert history.played(ALEX, _routine(ALEX_ID), _morning()) == set()
