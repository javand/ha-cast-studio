"""Tests for when announcements build: edits, daily rebuilds, restarts, removal."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.cast_studio.announcement_build import (
    AnnouncementBuild,
    WordsFailedError,
    announcement_dir,
    read_build,
)
from custom_components.cast_studio.announcement_runtime import get_manager
from custom_components.cast_studio.announcements import (
    get_announcements,
)
from custom_components.cast_studio.const import CONF_OUTPUT_DIR, DOMAIN

from .conftest import (
    SAM_ID,
    announcement_subentry,
    at,
    person_subentry,
    write_fake_build,
)

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

    from custom_components.cast_studio.announcements import Announcement

RUNTIME = "custom_components.cast_studio.announcement_runtime"


@dataclass
class FakeBuilder:
    """Stands in for async_build_announcement and records each build."""

    calls: list[str] = field(default_factory=list)
    gate: asyncio.Event | None = None
    started: asyncio.Event = field(default_factory=asyncio.Event)
    fail: Exception | None = None

    async def __call__(  # noqa: PLR0913
        self,
        hass: HomeAssistant,
        directory: Path,
        announcement: Announcement,
        ai_creds: dict[str, Any],
        now: datetime,
        previous: AnnouncementBuild | None,
    ) -> AnnouncementBuild:
        self.calls.append(announcement.name)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.fail is not None:
            raise self.fail
        return write_fake_build(directory, announcement, dt_util.now())


@pytest.fixture
def builder() -> FakeBuilder:
    found = FakeBuilder()
    with (
        patch(f"{RUNTIME}.async_build_announcement", new=found),
        patch(
            f"{RUNTIME}.async_discover_ai_credentials",
            new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"}),
        ),
    ):
        yield found


def make_entry(tmp_path: Path, *announcements: dict[str, Any]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=2,
        unique_id=DOMAIN,
        title="CastStudio",
        data={CONF_OUTPUT_DIR: str(tmp_path)},
        subentries_data=[person_subentry(SAM_ID, "Sam"), *announcements],
    )


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def directory(tmp_path: Path, announcement_id: str) -> Path:
    return announcement_dir(str(tmp_path), announcement_id)


def prebuilt(tmp_path: Path, entry: MockConfigEntry, now: datetime) -> None:
    """Leave a finished build on disk for each announcement, as before a restart."""
    for ann in get_announcements(entry):
        write_fake_build(directory(tmp_path, ann.announcement_id), ann, now)


@pytest.fixture
def at_nine(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    freezer.move_to(at(9))
    return freezer


async def test_setup_builds_announcement_without_a_build(
    hass: HomeAssistant, tmp_path: Path, builder: FakeBuilder, at_nine: Any
) -> None:
    await setup(hass, make_entry(tmp_path, announcement_subentry("a1", "Freezer Door")))
    assert builder.calls == ["Freezer Door"]
    state = get_manager(hass).state("a1")
    assert state.status == "ready"
    assert read_build(directory(tmp_path, "a1")) == state.build


async def test_restart_reuses_sidecar_without_building(
    hass: HomeAssistant, tmp_path: Path, builder: FakeBuilder, at_nine: Any
) -> None:
    entry = make_entry(tmp_path, announcement_subentry("a1", "Freezer Door"))
    prebuilt(tmp_path, entry, at(8))
    await setup(hass, entry)
    assert builder.calls == []
    assert get_manager(hass).state("a1").status == "ready"


async def test_settings_edit_rebuilds_only_that_announcement(
    hass: HomeAssistant, tmp_path: Path, builder: FakeBuilder, at_nine: Any
) -> None:
    entry = make_entry(
        tmp_path,
        announcement_subentry("a1", "Freezer Door"),
        announcement_subentry("a2", "Front Door", message="The front door is locked"),
    )
    prebuilt(tmp_path, entry, at(8))
    await setup(hass, entry)
    sub = entry.subentries["a1"]
    hass.config_entries.async_update_subentry(
        entry, sub, data={**sub.data, "message": "Shut the freezer"}
    )
    await hass.async_block_till_done()
    assert builder.calls == ["Freezer Door"]
    assert get_manager(hass).state("a1").build.words == "Shut the freezer"


async def test_person_edit_rebuilds_nothing(
    hass: HomeAssistant, tmp_path: Path, builder: FakeBuilder, at_nine: Any
) -> None:
    entry = make_entry(tmp_path, announcement_subentry("a1", "Freezer Door"))
    prebuilt(tmp_path, entry, at(8))
    await setup(hass, entry)
    sub = entry.subentries[SAM_ID]
    hass.config_entries.async_update_subentry(
        entry, sub, data={**sub.data, "phonetic_name": "Sahm"}
    )
    await hass.async_block_till_done()
    assert builder.calls == []


async def test_daily_rebuilds_at_rebuild_time(
    hass: HomeAssistant, tmp_path: Path, builder: FakeBuilder, at_nine: Any
) -> None:
    entry = make_entry(
        tmp_path, announcement_subentry("a1", "Welcome Sam", freshness="daily")
    )
    prebuilt(tmp_path, entry, at(8))
    await setup(hass, entry)
    assert builder.calls == []
    at_nine.move_to(at(3, day=21))
    async_fire_time_changed(hass, at(3, day=21))
    await hass.async_block_till_done()
    assert builder.calls == ["Welcome Sam"]
    assert get_manager(hass).state("a1").build.build_day.isoformat() == "2026-10-21"


async def test_daily_missed_while_down_rebuilds_once_at_startup(
    hass: HomeAssistant, tmp_path: Path, builder: FakeBuilder, at_nine: Any
) -> None:
    entry = make_entry(
        tmp_path, announcement_subentry("a1", "Welcome Sam", freshness="daily")
    )
    prebuilt(tmp_path, entry, at(8, day=19))
    await setup(hass, entry)
    assert builder.calls == ["Welcome Sam"]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert builder.calls == ["Welcome Sam"]


async def test_static_never_rebuilds_on_day_change(
    hass: HomeAssistant, tmp_path: Path, builder: FakeBuilder, at_nine: Any
) -> None:
    entry = make_entry(tmp_path, announcement_subentry("a1", "Freezer Door"))
    prebuilt(tmp_path, entry, at(8))
    await setup(hass, entry)
    at_nine.move_to(at(3, day=21))
    async_fire_time_changed(hass, at(3, day=21))
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert builder.calls == []


async def test_failed_rebuild_keeps_previous_and_shows_error(
    hass: HomeAssistant, tmp_path: Path, builder: FakeBuilder, at_nine: Any
) -> None:
    entry = make_entry(tmp_path, announcement_subentry("a1", "Freezer Door"))
    prebuilt(tmp_path, entry, at(8))
    await setup(hass, entry)
    manager = get_manager(hass)
    previous = manager.state("a1").build
    builder.fail = WordsFailedError("quota")
    await manager.async_rebuild("a1")
    state = manager.state("a1")
    assert state.status == "error"
    assert state.error == "quota"
    assert state.build == previous


async def test_removed_while_building_discards_result(
    hass: HomeAssistant, tmp_path: Path, builder: FakeBuilder, at_nine: Any
) -> None:
    builder.gate = asyncio.Event()
    entry = make_entry(tmp_path, announcement_subentry("a1", "Freezer Door"))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await builder.started.wait()
    hass.config_entries.async_remove_subentry(entry, "a1")
    builder.gate.set()
    await hass.async_block_till_done()
    assert get_manager(hass).state("a1") is None
    assert not directory(tmp_path, "a1").exists()


async def test_ensure_built_returns_previous_during_rebuild(
    hass: HomeAssistant, tmp_path: Path, builder: FakeBuilder, at_nine: Any
) -> None:
    entry = make_entry(tmp_path, announcement_subentry("a1", "Freezer Door"))
    prebuilt(tmp_path, entry, at(8))
    await setup(hass, entry)
    manager = get_manager(hass)
    previous = manager.state("a1").build
    builder.gate = asyncio.Event()
    rebuild = hass.async_create_task(manager.async_rebuild("a1"))
    await builder.started.wait()
    assert manager.state("a1").status == "building"
    assert await manager.async_ensure_built("a1") == previous
    builder.gate.set()
    await rebuild


async def test_ensure_built_builds_when_never_built(
    hass: HomeAssistant, tmp_path: Path, builder: FakeBuilder, at_nine: Any
) -> None:
    builder.fail = HomeAssistantError("no speech")
    await setup(hass, make_entry(tmp_path, announcement_subentry("a1", "Freezer Door")))
    manager = get_manager(hass)
    assert manager.state("a1").status == "error"
    builder.fail = None
    build = await manager.async_ensure_built("a1")
    assert build.words == "Close the freezer door"


async def test_rebuild_action_rebuilds_by_name(
    hass: HomeAssistant, tmp_path: Path, builder: FakeBuilder, at_nine: Any
) -> None:
    entry = make_entry(tmp_path, announcement_subentry("a1", "Freezer Door"))
    prebuilt(tmp_path, entry, at(8))
    await setup(hass, entry)
    await hass.services.async_call(
        DOMAIN, "rebuild_announcement", {"announcement": "freezer door"}, blocking=True
    )
    assert builder.calls == ["Freezer Door"]


async def test_rebuild_action_unknown_name_raises(
    hass: HomeAssistant, tmp_path: Path, builder: FakeBuilder, at_nine: Any
) -> None:
    await setup(hass, make_entry(tmp_path))
    with pytest.raises(HomeAssistantError, match="not found"):
        await hass.services.async_call(
            DOMAIN, "rebuild_announcement", {"announcement": "Nope"}, blocking=True
        )


async def test_new_announcement_builds_after_setup(
    hass: HomeAssistant, tmp_path: Path, builder: FakeBuilder, at_nine: Any
) -> None:
    entry = make_entry(tmp_path)
    await setup(hass, entry)
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=announcement_subentry("x", "Patio Door")["data"],
            subentry_type="announcement",
            title="Patio Door",
            unique_id=None,
        ),
    )
    await hass.async_block_till_done()
    assert builder.calls == ["Patio Door"]


async def test_options_flow_saves_on_a_set_up_entry(
    hass: HomeAssistant, tmp_path: Path, builder: FakeBuilder, at_nine: Any
) -> None:
    """Final review: the announcement listener must not break saving options."""
    entry = make_entry(tmp_path, announcement_subentry("a1", "Freezer Door"))
    await setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch("homeassistant.config_entries.ConfigEntries.async_reload", AsyncMock()):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={CONF_OUTPUT_DIR: str(tmp_path)}
        )
    assert result["type"] == "create_entry"


async def test_removed_during_requested_rebuild_leaves_no_folder(
    hass: HomeAssistant, tmp_path: Path, builder: FakeBuilder, at_nine: Any
) -> None:
    """Final review: a rebuild from the button or action is cancelled too."""
    entry = make_entry(tmp_path, announcement_subentry("a1", "Freezer Door"))
    prebuilt(tmp_path, entry, at(8))
    await setup(hass, entry)
    builder.gate = asyncio.Event()
    rebuild = hass.async_create_task(get_manager(hass).async_rebuild("a1"))
    await builder.started.wait()
    hass.config_entries.async_remove_subentry(entry, "a1")
    await hass.async_block_till_done()
    builder.gate.set()
    await rebuild
    await hass.async_block_till_done()
    assert get_manager(hass).state("a1") is None
    assert not directory(tmp_path, "a1").exists()
