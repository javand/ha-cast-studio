"""Tests for people and routines stored as config subentries."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cast_studio.const import (
    CONF_API_KEY,
    CONF_COMMUTE_DESTINATION,
    CONF_DEFAULT_TRANSPORTATION_MODE,
    CONF_OUTPUT_DIR,
    CONF_PHONETIC_NAME,
    CONF_RECIPIENT_NAME,
    DOMAIN,
)
from custom_components.cast_studio.people import (
    SEGMENT_TYPES,
    SUBENTRY_PERSON,
    SUBENTRY_ROUTINE,
    Person,
    briefing_day,
    find_person,
    find_routine,
    get_people,
    get_routines,
    is_content_fresh,
    person_calendars,
    slugify_name,
)

from .conftest import ALEX_ID, SAM_ID, make_entry, person_subentry, routine_subentry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


V1_DATA = {
    CONF_API_KEY: "fake_key",
    CONF_RECIPIENT_NAME: "Alex",
    CONF_PHONETIC_NAME: "Al-ex",
    CONF_COMMUTE_DESTINATION: "zone.office",
    CONF_DEFAULT_TRANSPORTATION_MODE: "fastest",
    CONF_OUTPUT_DIR: "/config/www/cast_studio",
}


def test_constants() -> None:
    """Subentry types and segment types use the exact agreed strings."""
    assert SUBENTRY_PERSON == "person"
    assert SUBENTRY_ROUTINE == "routine"
    assert SEGMENT_TYPES == (
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


def test_v1_constant_values_match_migration_keys() -> None:
    """The migration reads these v1 keys by their stored string values."""
    assert CONF_RECIPIENT_NAME == "recipient_name"
    assert CONF_PHONETIC_NAME == "phonetic_name"
    assert CONF_COMMUTE_DESTINATION == "commute_destination_zone"
    assert CONF_DEFAULT_TRANSPORTATION_MODE == "default_transportation_mode"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    with patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_migrate_v1_entry_creates_first_person_and_no_routine(
    hass: HomeAssistant,
) -> None:
    """Spec §5.7: v1 recipient settings become the first person subentry."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=1, unique_id=DOMAIN, data=dict(V1_DATA)
    )
    entry.add_to_hass(hass)

    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 2
    people = get_people(entry)
    assert len(people) == 1
    alex = people[0]
    assert alex.name == "Alex"
    assert alex.phonetic_name == "Al-ex"
    assert alex.commute_destination_zone == "zone.office"
    assert alex.transportation_mode == "fastest"
    assert alex.output_format == "visual"
    assert alex.media_player is None
    assert alex.reset_time == time(3, 0)
    assert alex.max_age_minutes == 0
    assert alex.person_id in entry.subentries
    assert get_routines(entry) == []
    # The v1 keys stay in entry.data; the migration only adds the person.
    assert dict(entry.data) == V1_DATA


@pytest.mark.asyncio
async def test_migrate_prefers_options_over_data(hass: HomeAssistant) -> None:
    """The migrated person uses the values the old pipeline actually uses."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id=DOMAIN,
        data=dict(V1_DATA),
        options={
            CONF_RECIPIENT_NAME: "Sam",
            CONF_DEFAULT_TRANSPORTATION_MODE: "transit",
        },
    )
    entry.add_to_hass(hass)

    await _setup(hass, entry)

    (person,) = get_people(entry)
    assert person.name == "Sam"
    assert person.phonetic_name == "Al-ex"
    assert person.transportation_mode == "transit"


@pytest.mark.asyncio
async def test_migrate_rejects_future_version(hass: HomeAssistant) -> None:
    """An entry from a newer version is not downgraded."""
    entry = MockConfigEntry(domain=DOMAIN, version=3, unique_id=DOMAIN, data={})
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


def test_get_people_parses_fields_and_settings() -> None:
    """Core keys map to fields; every other key passes through as settings."""
    entry = make_entry(
        [
            person_subentry(
                SAM_ID,
                "Sam",
                media_player="media_player.kitchen_speaker",
                output_format="audio",
                commute_destination_zone="zone.office",
                transportation_mode="transit",
                reset_time="04:30:00",
                max_age_minutes=90.0,
                weather_entity="weather.home",
                host_voice="Puck",
            )
        ]
    )

    (sam,) = get_people(entry)

    assert sam == Person(
        person_id=SAM_ID,
        name="Sam",
        phonetic_name="Sam",
        media_player="media_player.kitchen_speaker",
        output_format="audio",
        commute_destination_zone="zone.office",
        transportation_mode="transit",
        reset_time=time(4, 30),
        max_age_minutes=90,
        settings={"weather_entity": "weather.home", "host_voice": "Puck"},
    )
    assert isinstance(sam.max_age_minutes, int)


def test_get_routines_filters_by_person(
    entry_with_two_quick_routines: MockConfigEntry,
) -> None:
    """Routines come back in stored order, optionally for one person."""
    entry = entry_with_two_quick_routines
    assert [r.routine_id for r in get_routines(entry)] == [
        "r_alex_quick",
        "r_sam_quick",
        "r_sam_full",
    ]
    sam_routines = get_routines(entry, SAM_ID)
    assert [r.name for r in sam_routines] == ["Quick", "Full"]
    assert sam_routines[1].segments == ("greeting", "weather", "closing")
    assert sam_routines[1].dashboard_path is None


def test_find_person_by_id_or_name(
    entry_with_two_quick_routines: MockConfigEntry,
) -> None:
    """A person is found by subentry id or case-insensitive name."""
    entry = entry_with_two_quick_routines
    assert find_person(entry, SAM_ID).name == "Sam"
    assert find_person(entry, "sAM").person_id == SAM_ID
    assert find_person(entry, "Nobody") is None


def test_find_routine_ambiguous_without_person_raises(
    entry_with_two_quick_routines: MockConfigEntry,
) -> None:
    """Two people share "Quick", so a lookup without a person is ambiguous."""
    with pytest.raises(HomeAssistantError, match="ambiguous"):
        find_routine(entry_with_two_quick_routines, "Quick")


def test_find_routine_with_person_disambiguates(
    entry_with_two_quick_routines: MockConfigEntry,
) -> None:
    """Naming the person picks that person's routine."""
    entry = entry_with_two_quick_routines
    routine = find_routine(entry, "quick", person="Sam")
    assert routine.routine_id == "r_sam_quick"
    assert routine.person_id == SAM_ID
    assert find_routine(entry, "Quick", person=ALEX_ID).routine_id == "r_alex_quick"
    # A name used by only one person needs no person.
    assert find_routine(entry, "Full").routine_id == "r_sam_full"


def test_find_routine_not_found_raises(
    entry_with_two_quick_routines: MockConfigEntry,
) -> None:
    """Unknown routines and unknown people raise."""
    entry = entry_with_two_quick_routines
    with pytest.raises(HomeAssistantError, match="not found"):
        find_routine(entry, "Evening")
    with pytest.raises(HomeAssistantError, match="not found"):
        find_routine(entry, "Full", person="Alex")
    with pytest.raises(HomeAssistantError, match="not found"):
        find_routine(entry, "Quick", person="Nobody")


def test_slugify_name() -> None:
    """slugify_name wraps homeassistant.util.slugify."""
    assert slugify_name("Sam's Quick Run!") == "sam_s_quick_run"


def test_orphan_routines_are_ignored_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A routine whose person was removed never resolves."""
    entry = make_entry(
        [
            person_subentry(SAM_ID, "Sam"),
            routine_subentry("r_sam_quick", SAM_ID, "Quick", ["greeting"]),
            routine_subentry("r_gone", "person_removed", "Evening", ["closing"]),
            routine_subentry("r_gone_quick", "person_removed", "Quick", ["weather"]),
        ]
    )

    assert [r.routine_id for r in get_routines(entry)] == ["r_sam_quick"]
    assert "its person was removed" in caplog.text
    with pytest.raises(HomeAssistantError, match="not found"):
        find_routine(entry, "Evening")
    with pytest.raises(HomeAssistantError, match="not found"):
        find_routine(entry, "r_gone")
    # The orphan "Quick" doesn't make Sam's "Quick" ambiguous.
    assert find_routine(entry, "Quick").routine_id == "r_sam_quick"
    assert find_routine(entry, "Quick", person="Sam").routine_id == "r_sam_quick"


def _reset_person(reset: time = time(3, 0), max_age: int = 0) -> Person:
    return Person(
        person_id=ALEX_ID,
        name="Alex",
        phonetic_name="Alex",
        media_player=None,
        output_format="visual",
        commute_destination_zone=None,
        transportation_mode="fastest",
        reset_time=reset,
        max_age_minutes=max_age,
    )


def test_briefing_day_before_reset_is_previous_day() -> None:
    person = _reset_person()
    tz = dt_util.get_default_time_zone()
    before = datetime(2026, 3, 10, 2, 59, tzinfo=tz)
    at = datetime(2026, 3, 10, 3, 0, tzinfo=tz)
    assert briefing_day(person, before) == date(2026, 3, 9)
    assert briefing_day(person, at) == date(2026, 3, 10)


async def test_briefing_day_across_dst_spring_forward_and_fall_back(
    hass: HomeAssistant,
) -> None:
    ny = ZoneInfo("America/New_York")
    await hass.config.async_set_time_zone("America/New_York")
    person = _reset_person()
    for day in (date(2026, 3, 8), date(2026, 11, 1)):
        days = [
            briefing_day(
                person, datetime(day.year, day.month, day.day, h, m, tzinfo=ny)
            )
            for h, m in ((1, 30), (3, 30), (4, 0))
        ]
        assert days == [day - timedelta(days=1), day, day]
    # Monotonic across the fall-back repeated hour (both 01:30 instances).
    first = datetime(2026, 11, 1, 1, 30, tzinfo=ny, fold=0)
    second = datetime(2026, 11, 1, 1, 30, tzinfo=ny, fold=1)
    assert briefing_day(person, first) == briefing_day(person, second)
    assert briefing_day(person, datetime(2026, 3, 8, 3, 0, tzinfo=ny)) == date(
        2026, 3, 8
    )


def test_is_content_fresh_rejects_previous_day_and_respects_max_age() -> None:
    tz = dt_util.get_default_time_zone()
    now = datetime(2026, 3, 10, 12, 0, tzinfo=tz)
    today = date(2026, 3, 10)
    no_limit = _reset_person()
    limited = _reset_person(max_age=120)
    assert not is_content_fresh(
        no_limit, now - timedelta(hours=5), date(2026, 3, 9), now
    )
    assert is_content_fresh(no_limit, now - timedelta(hours=5), today, now)
    assert not is_content_fresh(limited, now - timedelta(hours=3), today, now)
    assert is_content_fresh(limited, now - timedelta(hours=1), today, now)


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        (
            {"calendar_entities": ["calendar.sam", "calendar.family"]},
            ["calendar.sam", "calendar.family"],
        ),
        ({"calendar_entity": "calendar.sam"}, ["calendar.sam"]),
        (
            {"calendar_entities": [], "calendar_entity": "calendar.sam"},
            ["calendar.sam"],
        ),
        (
            {
                "calendar_entities": ["calendar.family"],
                "calendar_entity": "calendar.sam",
            },
            ["calendar.family"],
        ),
        ({"calendar_entities": "calendar.sam"}, ["calendar.sam"]),
        ({}, []),
    ],
)
def test_person_calendars_reads_list_and_legacy_single_key(
    settings: dict[str, object], expected: list[str]
) -> None:
    """The list wins; a person saved before multi-calendar keeps their one."""
    assert person_calendars(settings) == expected
