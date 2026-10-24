"""Test fixtures for CastStudio integration tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cast_studio.const import (
    CONF_OUTPUT_DIR,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

ALEX_ID = "person_alex"
SAM_ID = "person_sam"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: Any,
) -> None:
    """Enable custom integrations for all tests."""
    return


def person_subentry(subentry_id: str, name: str, **data: Any) -> dict[str, Any]:
    """Build a stored person subentry with the default per-person values."""
    return {
        "subentry_id": subentry_id,
        "subentry_type": "person",
        "title": name,
        "unique_id": None,
        "data": {
            "name": name,
            "phonetic_name": name,
            "media_player": None,
            "output_format": "visual",
            "commute_destination_zone": None,
            "transportation_mode": "fastest",
            "reset_time": "03:00:00",
            "max_age_minutes": 0,
            **data,
        },
    }


def routine_subentry(
    subentry_id: str, person_id: str, name: str, segments: list[str]
) -> dict[str, Any]:
    """Build a stored routine subentry."""
    return {
        "subentry_id": subentry_id,
        "subentry_type": "routine",
        "title": name,
        "unique_id": None,
        "data": {
            "person_id": person_id,
            "name": name,
            "segments": segments,
            "dashboard_path": None,
            "dashboard_view": None,
            "dashboard_display": None,
        },
    }


def make_entry(subentries: list[dict[str, Any]] | None = None) -> MockConfigEntry:
    """Build a v2 CastStudio entry holding the given subentries."""
    return MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id=DOMAIN,
        title="CastStudio",
        data={CONF_OUTPUT_DIR: "/config/www/cast_studio"},
        subentries_data=subentries or [],
    )


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A v2 CastStudio entry with no people yet."""
    entry = make_entry()
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def entry_with_sam(hass: HomeAssistant) -> MockConfigEntry:
    """A v2 entry with one person, Sam."""
    entry = make_entry([person_subentry(SAM_ID, "Sam")])
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def entry_with_alex_and_sam(hass: HomeAssistant) -> MockConfigEntry:
    """A v2 entry with two people, Alex then Sam."""
    entry = make_entry(
        [person_subentry(ALEX_ID, "Alex"), person_subentry(SAM_ID, "Sam")]
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def entry_with_two_quick_routines() -> MockConfigEntry:
    """Alex and Sam each have a routine called Quick; Sam also has Full."""
    return make_entry(
        [
            person_subentry(ALEX_ID, "Alex"),
            person_subentry(SAM_ID, "Sam", output_format="audio"),
            routine_subentry("r_alex_quick", ALEX_ID, "Quick", ["greeting"]),
            routine_subentry("r_sam_quick", SAM_ID, "Quick", ["greeting", "weather"]),
            routine_subentry(
                "r_sam_full", SAM_ID, "Full", ["greeting", "weather", "closing"]
            ),
        ]
    )
