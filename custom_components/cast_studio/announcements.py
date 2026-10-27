"""
Household announcements stored as config subentries of the CastStudio entry.

An announcement is one slide and one spoken line, built ahead of time and
played by the user's automations. A static one is built once; a daily one is
rebuilt every day at its rebuild time, with a persona picked from its list.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from homeassistant.exceptions import HomeAssistantError

from .people import rolled_day, slugify_name
from .personas import Persona, get_persona

if TYPE_CHECKING:
    from datetime import date, datetime

    from homeassistant.config_entries import ConfigEntry, ConfigSubentry
    from homeassistant.core import HomeAssistant

SUBENTRY_ANNOUNCEMENT = "announcement"

ANN_NAME = "name"
ANN_DESCRIPTION = "description"
ANN_WORDING = "wording"
ANN_MESSAGE = "message"
ANN_FRESHNESS = "freshness"
ANN_PERSONAS = "personas"
ANN_REBUILD_TIME = "rebuild_time"
ANN_PICTURES = "pictures"
ANN_OUTPUT_FORMAT = "output_format"
ANN_TARGET = "target"

WORDINGS: tuple[str, ...] = ("verbatim", "creative")
FRESHNESS: tuple[str, ...] = ("static", "daily")
DEFAULT_REBUILD_TIME = "03:00:00"
DEFAULT_PERSONA = "default_host"
MAX_PICTURES = 4
LOCAL_MEDIA_PREFIX = "media-source://media_source/"


@dataclass(frozen=True)
class Announcement:
    """One household announcement."""

    announcement_id: str
    name: str
    description: str
    wording: Literal["verbatim", "creative"]
    message: str
    freshness: Literal["static", "daily"]
    persona_ids: tuple[str, ...]
    rebuild_time: time
    pictures: tuple[str, ...]
    output_format: Literal["audio", "visual"]
    # The media player it plays on when none is given.
    target: str | None = None


def _announcement_from_subentry(subentry: ConfigSubentry) -> Announcement:
    data = subentry.data
    return Announcement(
        announcement_id=subentry.subentry_id,
        name=data.get(ANN_NAME) or subentry.title,
        description=data.get(ANN_DESCRIPTION) or "",
        wording=data.get(ANN_WORDING) or "verbatim",
        message=data.get(ANN_MESSAGE) or "",
        freshness=data.get(ANN_FRESHNESS) or "static",
        persona_ids=tuple(data.get(ANN_PERSONAS) or (DEFAULT_PERSONA,)),
        rebuild_time=time.fromisoformat(
            data.get(ANN_REBUILD_TIME) or DEFAULT_REBUILD_TIME
        ),
        pictures=tuple(data.get(ANN_PICTURES) or ()),
        output_format=data.get(ANN_OUTPUT_FORMAT) or "visual",
        target=data.get(ANN_TARGET) or None,
    )


def get_announcements(entry: ConfigEntry) -> list[Announcement]:
    """Return the entry's announcements in the order they were added."""
    return [
        _announcement_from_subentry(subentry)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_ANNOUNCEMENT
    ]


def find_announcement(entry: ConfigEntry, name_or_id: str) -> Announcement:
    """Find an announcement by subentry id, or by name ignoring case."""
    announcements = get_announcements(entry)
    for announcement in announcements:
        if announcement.announcement_id == name_or_id:
            return announcement
    wanted = slugify_name(name_or_id)
    for announcement in announcements:
        if slugify_name(announcement.name) == wanted:
            return announcement
    msg = f"CastStudio announcement '{name_or_id}' not found"
    raise HomeAssistantError(msg)


def settings_fingerprint(announcement: Announcement) -> str:
    """Return a hash of the settings a build depends on (not the rebuild time)."""
    payload = {
        "name": announcement.name,
        "description": announcement.description,
        "wording": announcement.wording,
        "message": announcement.message,
        "freshness": announcement.freshness,
        "persona_ids": list(announcement.persona_ids),
        "pictures": list(announcement.pictures),
        "output_format": announcement.output_format,
    }
    encoded = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def announcement_day(announcement: Announcement, now: datetime) -> date:
    """Return the day a daily build belongs to; it rolls at the rebuild time."""
    return rolled_day(now, announcement.rebuild_time)


def pick_build_persona(
    announcement: Announcement,
    previous_id: str | None,
    rng: random.Random | None = None,
) -> Persona:
    """
    Pick who speaks a build.

    Static uses the first valid persona. Daily picks at random, never the
    previous build's persona while there is another to choose.
    """
    valid = [
        persona_id
        for persona_id in dict.fromkeys(announcement.persona_ids)
        if get_persona(persona_id) is not None
    ]
    if not valid:
        persona = get_persona(DEFAULT_PERSONA)
        assert persona is not None  # noqa: S101 - the catalog always has it
        return persona
    if announcement.freshness == "static":
        choice = valid[0]
    else:
        if len(valid) > 1 and previous_id in valid:
            valid.remove(previous_id)
        choice = (rng or random).choice(valid)
    persona = get_persona(choice)
    assert persona is not None  # noqa: S101 - filtered above
    return persona


def resolve_local_picture(hass: HomeAssistant, media_content_id: str) -> Path | None:
    """
    Return the file behind a local media-source id, or None.

    Blocking (checks the file): run it in the executor.
    """
    if not media_content_id.startswith(LOCAL_MEDIA_PREFIX):
        return None
    dir_id, _, relative = media_content_id.removeprefix(LOCAL_MEDIA_PREFIX).partition(
        "/"
    )
    base = hass.config.media_dirs.get(dir_id)
    if not base or not relative:
        return None
    root = Path(base).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path
