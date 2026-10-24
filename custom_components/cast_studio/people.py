"""People and routines stored as config subentries of the CastStudio entry."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import DEFAULT_TRANSPORTATION_MODE, DOMAIN, LOGGER

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date, datetime

    from homeassistant.config_entries import ConfigEntry, ConfigSubentry
    from homeassistant.core import HomeAssistant

SUBENTRY_PERSON = "person"
SUBENTRY_ROUTINE = "routine"

SEGMENT_TYPES: tuple[str, ...] = (
    "greeting",
    "weather",
    "national_day",
    "fun_fact",
    "agenda",
    "commute",
    "scripture",
    "daily_encouragement",
    "closing",
)

OUTPUT_FORMATS: tuple[str, ...] = ("audio", "visual")
TRANSPORTATION_MODES: tuple[str, ...] = ("fastest", "driving", "transit")

DEFAULT_OUTPUT_FORMAT = "visual"
DEFAULT_RESET_TIME = "03:00:00"
DEFAULT_MAX_AGE_MINUTES = 0

# Person subentry data keys (§5.1 fields).
PERSON_NAME = "name"
PERSON_PHONETIC_NAME = "phonetic_name"
PERSON_MEDIA_PLAYER = "media_player"
PERSON_OUTPUT_FORMAT = "output_format"
PERSON_COMMUTE_DESTINATION = "commute_destination_zone"
PERSON_TRANSPORTATION_MODE = "transportation_mode"
PERSON_RESET_TIME = "reset_time"
PERSON_MAX_AGE_MINUTES = "max_age_minutes"
PERSON_PRONUNCIATIONS = "pronunciations"

PERSON_CORE_KEYS: tuple[str, ...] = (
    PERSON_NAME,
    PERSON_PHONETIC_NAME,
    PERSON_MEDIA_PLAYER,
    PERSON_OUTPUT_FORMAT,
    PERSON_COMMUTE_DESTINATION,
    PERSON_TRANSPORTATION_MODE,
    PERSON_RESET_TIME,
    PERSON_MAX_AGE_MINUTES,
)

# Per-person commute travel-time sensors (settings keys).
PERSON_COMMUTE_DRIVE_SENSOR = "commute_drive_sensor"
PERSON_COMMUTE_TRANSIT_SENSOR = "commute_transit_sensor"
# Optional evening trip-home sensors (e.g. Google Travel Time with a departure time).
PERSON_COMMUTE_DRIVE_RETURN_SENSOR = "commute_drive_return_sensor"
PERSON_COMMUTE_TRANSIT_RETURN_SENSOR = "commute_transit_return_sensor"

# The agenda's calendars; older entries stored one under PERSON_LEGACY_CALENDAR.
PERSON_CALENDARS = "calendar_entities"
PERSON_LEGACY_CALENDAR = "calendar_entity"

# Per-person segment settings (§5.2), shown in the person form's advanced section.
PERSON_SETTINGS_KEYS: tuple[str, ...] = (
    "weather_entity",
    PERSON_CALENDARS,
    "todo_entity",
    "scripture_entity",
    PERSON_COMMUTE_DRIVE_SENSOR,
    PERSON_COMMUTE_TRANSIT_SENSOR,
    PERSON_COMMUTE_DRIVE_RETURN_SENSOR,
    PERSON_COMMUTE_TRANSIT_RETURN_SENSOR,
    "host_voice",
    "scripture_voice",
    "host_personality",
    "scripture_personality",
    "conversation_agent",
    "greeting_title",
    "greeting_subtitle",
    "greeting_image",
    PERSON_PRONUNCIATIONS,
)

# Routine subentry data keys.
ROUTINE_PERSON_ID = "person_id"
ROUTINE_NAME = "name"
ROUTINE_SEGMENTS = "segments"
ROUTINE_DASHBOARD_PATH = "dashboard_path"
# The routine form's single dashboard picker, split into path and view on save.
ROUTINE_DASHBOARD = "dashboard"
ROUTINE_DASHBOARD_VIEW = "dashboard_view"
ROUTINE_DASHBOARD_DISPLAY = "dashboard_display"


@dataclass(frozen=True)
class Person:
    """A person who receives briefings."""

    person_id: str
    name: str
    phonetic_name: str
    media_player: str | None
    output_format: Literal["audio", "visual"]
    commute_destination_zone: str | None
    transportation_mode: str
    reset_time: time
    max_age_minutes: int
    settings: Mapping[str, Any] = field(default_factory=dict, hash=False)
    pronunciations: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Routine:
    """A named, ordered list of segments for one person."""

    routine_id: str
    person_id: str
    name: str
    segments: tuple[str, ...]
    dashboard_path: str | None
    dashboard_view: str | None
    dashboard_display: str | None


def slugify_name(value: str) -> str:
    """Return the slug used for entity ids and name comparisons."""
    return slugify(value)


def is_valid_name(value: str | None) -> bool:
    """Return True when a person or routine name can make a real slug."""
    return bool(value) and any(ch.isalnum() for ch in value)


def build_person_data(
    name: str,
    phonetic_name: str | None,
    commute_destination_zone: str | None,
    transportation_mode: str | None,
) -> dict[str, Any]:
    """Return stored person data with the per-person defaults (§5.7 shape)."""
    return {
        PERSON_NAME: name,
        PERSON_PHONETIC_NAME: phonetic_name or name,
        PERSON_MEDIA_PLAYER: None,
        PERSON_OUTPUT_FORMAT: DEFAULT_OUTPUT_FORMAT,
        PERSON_COMMUTE_DESTINATION: commute_destination_zone or None,
        PERSON_TRANSPORTATION_MODE: transportation_mode or DEFAULT_TRANSPORTATION_MODE,
        PERSON_RESET_TIME: DEFAULT_RESET_TIME,
        PERSON_MAX_AGE_MINUTES: DEFAULT_MAX_AGE_MINUTES,
    }


def clean_pronunciations(rows: Any) -> tuple[tuple[str, str], ...]:
    """Keep only rows where both the written and spoken values are non-blank."""
    cleaned: list[tuple[str, str]] = []
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        written = str(row.get("written") or "").strip()
        spoken = str(row.get("spoken") or "").strip()
        if written and spoken:
            cleaned.append((written, spoken))
    return tuple(cleaned)


def _person_from_subentry(subentry: ConfigSubentry) -> Person:
    data = subentry.data
    name = data.get(PERSON_NAME) or subentry.title
    return Person(
        person_id=subentry.subentry_id,
        name=name,
        phonetic_name=data.get(PERSON_PHONETIC_NAME) or name,
        media_player=data.get(PERSON_MEDIA_PLAYER) or None,
        output_format=data.get(PERSON_OUTPUT_FORMAT) or DEFAULT_OUTPUT_FORMAT,
        commute_destination_zone=data.get(PERSON_COMMUTE_DESTINATION) or None,
        transportation_mode=(
            data.get(PERSON_TRANSPORTATION_MODE) or DEFAULT_TRANSPORTATION_MODE
        ),
        reset_time=time.fromisoformat(
            data.get(PERSON_RESET_TIME) or DEFAULT_RESET_TIME
        ),
        max_age_minutes=int(
            data.get(PERSON_MAX_AGE_MINUTES) or DEFAULT_MAX_AGE_MINUTES
        ),
        settings=MappingProxyType(
            {
                k: v
                for k, v in data.items()
                if k not in PERSON_CORE_KEYS and k != PERSON_PRONUNCIATIONS
            }
        ),
        pronunciations=clean_pronunciations(data.get(PERSON_PRONUNCIATIONS)),
    )


def _routine_from_subentry(subentry: ConfigSubentry) -> Routine:
    data = subentry.data
    return Routine(
        routine_id=subentry.subentry_id,
        person_id=data[ROUTINE_PERSON_ID],
        name=data.get(ROUTINE_NAME) or subentry.title,
        segments=tuple(data.get(ROUTINE_SEGMENTS) or ()),
        dashboard_path=data.get(ROUTINE_DASHBOARD_PATH) or None,
        dashboard_view=data.get(ROUTINE_DASHBOARD_VIEW) or None,
        dashboard_display=data.get(ROUTINE_DASHBOARD_DISPLAY) or None,
    )


def get_people(entry: ConfigEntry) -> list[Person]:
    """Return the entry's people in the order they were added."""
    return [
        _person_from_subentry(subentry)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_PERSON
    ]


def get_routines(entry: ConfigEntry, person_id: str | None = None) -> list[Routine]:
    """
    Return the entry's routines in order, optionally for one person only.

    Routines whose person has been removed are skipped with a warning.
    """
    person_ids = {
        subentry.subentry_id
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_PERSON
    }
    routines: list[Routine] = []
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_ROUTINE:
            continue
        routine = _routine_from_subentry(subentry)
        if routine.person_id not in person_ids:
            LOGGER.warning(
                "Ignoring CastStudio routine '%s': its person was removed",
                routine.name,
            )
            continue
        if person_id is None or routine.person_id == person_id:
            routines.append(routine)
    return routines


def get_loaded_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """Return the loaded CastStudio entry, if there is one."""
    try:
        entries = hass.config_entries.async_entries(DOMAIN)
    except Exception:  # noqa: BLE001 - legacy callers pass partial hass mocks
        return None
    for entry in entries or ():
        if getattr(entry, "state", None) is ConfigEntryState.LOADED:
            return entry
    return None


def get_first_person(hass: HomeAssistant) -> Person | None:
    """Return the loaded entry's first person, read now rather than cached."""
    if (entry := get_loaded_entry(hass)) is None:
        return None
    people = get_people(entry)
    return people[0] if people else None


def find_person(entry: ConfigEntry, name_or_id: str) -> Person | None:
    """Find a person by subentry id, or by name ignoring case."""
    people = get_people(entry)
    for person in people:
        if person.person_id == name_or_id:
            return person
    wanted = name_or_id.casefold()
    for person in people:
        if person.name.casefold() == wanted:
            return person
    return None


def find_routine(
    entry: ConfigEntry, routine: str, person: str | None = None
) -> Routine:
    """
    Find a routine by subentry id or name, optionally scoped to a person.

    Raises HomeAssistantError when nothing matches, or when the name is used
    by more than one person and no person was given.
    """
    person_id: str | None = None
    if person is not None:
        found = find_person(entry, person)
        if found is None:
            msg = f"CastStudio person '{person}' not found"
            raise HomeAssistantError(msg)
        person_id = found.person_id

    candidates = get_routines(entry, person_id)
    by_id = [r for r in candidates if r.routine_id == routine]
    if by_id:
        return by_id[0]

    wanted = slugify_name(routine)
    matches = [r for r in candidates if slugify_name(r.name) == wanted]
    if not matches:
        scope = f" for person '{person}'" if person is not None else ""
        msg = f"CastStudio routine '{routine}' not found{scope}"
        raise HomeAssistantError(msg)
    if len(matches) > 1:
        msg = (
            f"CastStudio routine '{routine}' is ambiguous: more than one person "
            "has a routine with that name. Name the person too."
        )
        raise HomeAssistantError(msg)
    return matches[0]


def briefing_day(person: Person, now: datetime) -> date:
    """
    Return the person's briefing day for a timezone-aware ``now``.

    The day rolls over at ``person.reset_time`` local wall-clock time, so
    times before the reset still belong to the previous calendar day.
    """
    local = dt_util.as_local(now)
    if local.time() < person.reset_time:
        return local.date() - timedelta(days=1)
    return local.date()


def is_content_fresh(
    person: Person, built_at: datetime, built_day: date, now: datetime
) -> bool:
    """Return True if content built at ``built_at`` is still usable at ``now``."""
    if built_day != briefing_day(person, now):
        return False
    if person.max_age_minutes == 0:
        return True
    return now - built_at <= timedelta(minutes=person.max_age_minutes)


def person_calendars(settings: Mapping[str, Any]) -> list[str]:
    """Return a person's agenda calendars, reading the older single-calendar key."""
    calendars = settings.get(PERSON_CALENDARS)
    if isinstance(calendars, str):
        calendars = [calendars]
    if not calendars and (legacy := settings.get(PERSON_LEGACY_CALENDAR)):
        calendars = [legacy]
    return [str(eid) for eid in calendars or [] if eid]
