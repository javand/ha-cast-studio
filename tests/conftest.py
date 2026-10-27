"""Test fixtures for CastStudio integration tests."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cast_studio.const import (
    CONF_OUTPUT_DIR,
    DOMAIN,
)

if TYPE_CHECKING:
    from pathlib import Path

    from homeassistant.core import HomeAssistant

    from custom_components.cast_studio.announcement_build import AnnouncementBuild
    from custom_components.cast_studio.announcements import Announcement

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
            "commute_destination_zone": None,
            "transportation_mode": "fastest",
            "reset_time": "03:00:00",
            "max_age_minutes": 0,
            **data,
        },
    }


def routine_subentry(
    subentry_id: str, person_id: str, name: str, segments: list[str], **data: Any
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
            "output_format": "visual",
            **data,
        },
    }


def announcement_subentry(subentry_id: str, name: str, **data: Any) -> dict[str, Any]:
    """Build a stored announcement subentry: static, verbatim, visual by default."""
    return {
        "subentry_id": subentry_id,
        "subentry_type": "announcement",
        "title": name,
        "unique_id": None,
        "data": {
            "name": name,
            "description": "A friendly reminder",
            "wording": "verbatim",
            "message": "Close the freezer door",
            "freshness": "static",
            "personas": ["default_host"],
            "rebuild_time": "03:00:00",
            "pictures": [],
            "output_format": "visual",
            **data,
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
            person_subentry(SAM_ID, "Sam"),
            routine_subentry("r_alex_quick", ALEX_ID, "Quick", ["greeting"]),
            routine_subentry("r_sam_quick", SAM_ID, "Quick", ["greeting", "weather"]),
            routine_subentry(
                "r_sam_full", SAM_ID, "Full", ["greeting", "weather", "closing"]
            ),
        ]
    )


def write_fake_build(  # noqa: PLR0913
    directory: Path,
    announcement: Announcement,
    now: datetime,
    *,
    persona_id: str = "default_host",
    duration: float = 2.0,
    visual_file: str | None = None,
) -> AnnouncementBuild:
    """
    Write a real sidecar and audio file, as a finished build would.

    ``visual_file`` ("slide" or "clip") adds that file when the announcement
    is visual.
    """
    from custom_components.cast_studio.announcement_build import (
        AnnouncementBuild,
        write_build,
    )
    from custom_components.cast_studio.announcements import (
        announcement_day,
        settings_fingerprint,
    )

    directory.mkdir(parents=True, exist_ok=True)
    tag = now.strftime("%d%H%M%S%f")
    audio = directory / f"speech.{tag}.mp3"
    audio.write_bytes(b"mp3")
    extra: Path | None = None
    if visual_file and announcement.output_format == "visual":
        extra = directory / (
            f"slide.{tag}.png" if visual_file == "slide" else f"clip.{tag}.mp4"
        )
        extra.write_bytes(b"\x89PNG slide" if visual_file == "slide" else b"mp4")
    is_clip = extra is not None and visual_file == "clip"
    build = AnnouncementBuild(
        fingerprint=settings_fingerprint(announcement),
        build_day=(
            announcement_day(announcement, now)
            if announcement.freshness == "daily"
            else None
        ),
        built_at=now,
        words=announcement.message,
        persona_id=persona_id,
        audio_path=str(audio),
        audio_duration=duration,
        clip_path=str(extra) if is_clip else None,
        clip_duration=duration if is_clip else None,
        slide_path=str(extra) if extra is not None and not is_clip else None,
    )
    write_build(directory, build)
    return build


def at(hour: int, minute: int = 0, day: int = 20) -> datetime:
    """Return a local, timezone-aware time on a fake October 2026 day."""
    return datetime(2026, 10, day, hour, minute, tzinfo=dt_util.get_default_time_zone())
