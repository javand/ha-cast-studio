"""Tests for the announcement settings model."""

from __future__ import annotations

import random
from datetime import time
from typing import TYPE_CHECKING

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.cast_studio.announcements import (
    announcement_day,
    find_announcement,
    get_announcements,
    pick_build_persona,
    resolve_local_picture,
    settings_fingerprint,
)

from .conftest import SAM_ID, announcement_subentry, at, make_entry, person_subentry

if TYPE_CHECKING:
    from pathlib import Path

    from homeassistant.core import HomeAssistant


def _one(**data: object):
    entry = make_entry(
        [
            person_subentry(SAM_ID, "Sam"),
            announcement_subentry("a1", "Freezer Door", **data),
        ]
    )
    return get_announcements(entry)[0]


def test_get_announcements_reads_subentries_and_defaults() -> None:
    entry = make_entry(
        [person_subentry(SAM_ID, "Sam"), announcement_subentry("a1", "Freezer Door")]
    )
    [ann] = get_announcements(entry)
    assert ann.announcement_id == "a1"
    assert ann.name == "Freezer Door"
    assert ann.description == "A friendly reminder"
    assert ann.wording == "verbatim"
    assert ann.message == "Close the freezer door"
    assert ann.freshness == "static"
    assert ann.persona_ids == ("default_host",)
    assert ann.rebuild_time == time(3, 0)
    assert ann.pictures == ()
    assert ann.output_format == "visual"

    bare = announcement_subentry("a2", "Bare")
    for key in ("personas", "freshness", "output_format", "rebuild_time"):
        del bare["data"][key]
    [ann] = get_announcements(make_entry([bare]))
    assert ann.persona_ids == ("default_host",)
    assert ann.freshness == "static"
    assert ann.output_format == "visual"
    assert ann.rebuild_time == time(3, 0)


def test_find_announcement_by_id_and_name() -> None:
    entry = make_entry([announcement_subentry("a1", "Freezer Door")])
    assert find_announcement(entry, "a1").name == "Freezer Door"
    assert find_announcement(entry, "freezer door").announcement_id == "a1"
    with pytest.raises(HomeAssistantError, match="not found"):
        find_announcement(entry, "Patio Door")


def test_fingerprint_changes_with_settings_not_rebuild_time() -> None:
    base = settings_fingerprint(_one())
    assert settings_fingerprint(_one(message="Shut it")) != base
    assert settings_fingerprint(_one(rebuild_time="06:00:00")) == base


def test_announcement_day_rolls_at_rebuild_time() -> None:
    ann = _one(freshness="daily")
    assert announcement_day(ann, at(2, 59)).day == 19
    assert announcement_day(ann, at(3, 0)).day == 20


def test_pick_persona_static_uses_first_valid() -> None:
    ann = _one(personas=["nope", "elena", "cole"])
    assert pick_build_persona(ann, None).persona_id == "elena"


def test_pick_persona_daily_never_repeats_previous() -> None:
    ann = _one(freshness="daily", personas=["elena", "cole"])
    for seed in range(20):
        picked = pick_build_persona(ann, "elena", random.Random(seed))  # noqa: S311
        assert picked.persona_id == "cole"


def test_pick_persona_daily_single_choice_repeats() -> None:
    ann = _one(freshness="daily", personas=["elena"])
    assert pick_build_persona(ann, "elena").persona_id == "elena"


def test_pick_persona_unknown_ids_fall_back_to_default_host() -> None:
    assert pick_build_persona(_one(personas=["nope"]), None).persona_id == (
        "default_host"
    )
    assert pick_build_persona(_one(personas=[]), None).persona_id == "default_host"


def test_resolve_local_picture(hass: HomeAssistant, tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    (media / "a.jpg").write_bytes(b"jpg")
    (tmp_path / "x.jpg").write_bytes(b"outside")
    hass.config.media_dirs = {"local": str(media)}
    assert resolve_local_picture(hass, "media-source://media_source/local/a.jpg") == (
        media / "a.jpg"
    )
    assert (
        resolve_local_picture(hass, "media-source://media_source/local/../x.jpg")
        is None
    )
    assert resolve_local_picture(hass, "media-source://other/x.jpg") is None
    assert resolve_local_picture(hass, "media-source://media_source/nas/a.jpg") is None
    assert (
        resolve_local_picture(hass, "media-source://media_source/local/b.jpg") is None
    )
