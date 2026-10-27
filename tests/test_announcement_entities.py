"""Tests for each announcement's slide image and rebuild button."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.image import async_get_image
from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers import entity_registry as er

from custom_components.cast_studio.announcement_build import (
    AnnouncementBuild,
    announcement_dir,
)
from custom_components.cast_studio.announcements import (
    get_announcements,
)

from .conftest import announcement_subentry, at, write_fake_build
from .test_announcement_runtime import make_entry, setup

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

    from custom_components.cast_studio.announcements import Announcement

RUNTIME = "custom_components.cast_studio.announcement_runtime"
IMAGE = "image.cast_studio_announcement_freezer_door"
BUTTON = "button.cast_studio_rebuild_freezer_door"


@pytest.fixture
def builds(freezer: FrozenDateTimeFactory) -> list[str]:
    freezer.move_to(at(9))
    calls: list[str] = []

    async def _build(  # noqa: PLR0913
        hass: Any,
        directory: Path,
        announcement: Announcement,
        creds: Any,
        now: datetime,
        previous: Any,
    ) -> AnnouncementBuild:
        calls.append(announcement.name)
        return write_fake_build(
            directory, announcement, now, persona_id="elena", visual_file="slide"
        )

    with (
        patch(f"{RUNTIME}.async_build_announcement", new=_build),
        patch(
            f"{RUNTIME}.async_discover_ai_credentials",
            new=AsyncMock(return_value={}),
        ),
    ):
        yield calls


async def test_image_entity_shows_slide_and_attributes(
    hass: HomeAssistant, tmp_path: Path, builds: list[str]
) -> None:
    await setup(hass, make_entry(tmp_path, announcement_subentry("a1", "Freezer Door")))
    state = hass.states.get(IMAGE)
    assert state is not None
    assert state.attributes["words"] == "Close the freezer door"
    assert state.attributes["wording"] == "verbatim"
    assert state.attributes["freshness"] == "static"
    assert state.attributes["persona"] == "Elena"
    assert state.attributes["status"] == "ready"
    assert state.attributes["built_at"] == at(9).isoformat()
    image = await async_get_image(hass, IMAGE)
    assert image.content == b"\x89PNG slide"
    assert image.content_type == "image/png"


async def test_audio_announcement_image_has_no_picture(
    hass: HomeAssistant, tmp_path: Path, builds: list[str]
) -> None:
    await setup(
        hass,
        make_entry(
            tmp_path, announcement_subentry("a1", "Freezer Door", output_format="audio")
        ),
    )
    entity = hass.data["image"].get_entity(IMAGE)
    assert await entity.async_image() is None
    assert hass.states.get(IMAGE).attributes["words"] == "Close the freezer door"


async def test_button_press_rebuilds(
    hass: HomeAssistant, tmp_path: Path, builds: list[str]
) -> None:
    entry = make_entry(tmp_path, announcement_subentry("a1", "Freezer Door"))
    write_fake_build(
        announcement_dir(str(tmp_path), "a1"),
        get_announcements(entry)[0],
        at(8),
        persona_id="elena",
        visual_file="slide",
    )
    await setup(hass, entry)
    assert builds == []
    await hass.services.async_call(
        "button", "press", {"entity_id": BUTTON}, blocking=True
    )
    await hass.async_block_till_done()
    assert builds == ["Freezer Door"]


async def test_new_announcement_gets_entities_without_reload(
    hass: HomeAssistant, tmp_path: Path, builds: list[str]
) -> None:
    entry = make_entry(tmp_path)
    await setup(hass, entry)
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=announcement_subentry("x", "Freezer Door")["data"],
            subentry_type="announcement",
            title="Freezer Door",
            unique_id=None,
        ),
    )
    await hass.async_block_till_done()
    assert hass.states.get(IMAGE) is not None
    assert hass.states.get(BUTTON) is not None
    registry = er.async_get(hass)
    assert registry.async_get(IMAGE).config_subentry_id is not None


async def test_removed_announcement_loses_entities(
    hass: HomeAssistant, tmp_path: Path, builds: list[str]
) -> None:
    entry = make_entry(tmp_path, announcement_subentry("a1", "Freezer Door"))
    await setup(hass, entry)
    hass.config_entries.async_remove_subentry(entry, "a1")
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    assert registry.async_get(IMAGE) is None
    assert registry.async_get(BUTTON) is None


async def test_restart_shows_existing_slide(
    hass: HomeAssistant, tmp_path: Path, builds: list[str]
) -> None:
    """After a restart the image shows the build read back from disk."""
    entry = make_entry(tmp_path, announcement_subentry("a1", "Freezer Door"))
    write_fake_build(
        announcement_dir(str(tmp_path), "a1"),
        get_announcements(entry)[0],
        at(8),
        persona_id="elena",
        visual_file="slide",
    )
    await setup(hass, entry)
    assert builds == []
    state = hass.states.get(IMAGE)
    assert state.state == at(8).isoformat()
    assert state.attributes["status"] == "ready"
