"""Button platform for cast_studio: rebuild or play an announcement."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .announcement_entity import (
    AnnouncementEntity,
    async_setup_announcement_platform,
)
from .announcement_play import async_announce
from .announcement_runtime import get_manager
from .people import slugify_name

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .announcements import Announcement


class CastStudioAnnouncementRebuildButton(AnnouncementEntity, ButtonEntity):
    """Builds the announcement again: new words and slide."""

    _attr_name = "Rebuild"
    _attr_icon = "mdi:refresh"

    def __init__(self, announcement: Announcement) -> None:
        """Initialise as ``button.cast_studio_rebuild_<name>``."""
        super().__init__(announcement)
        self.entity_id = f"button.cast_studio_rebuild_{slugify_name(announcement.name)}"
        self._attr_unique_id = (
            f"cast_studio_announcement_rebuild_{announcement.announcement_id}"
        )

    async def async_press(self) -> None:
        """Rebuild now."""
        await get_manager(self.hass).async_rebuild(self.announcement_id)


class CastStudioAnnouncementPlayButton(AnnouncementEntity, ButtonEntity):
    """Plays the announcement on the player chosen in Play on."""

    _attr_name = "Play"
    _attr_icon = "mdi:play"

    def __init__(self, announcement: Announcement) -> None:
        """Initialise as ``button.cast_studio_play_<name>``."""
        super().__init__(announcement)
        self.entity_id = f"button.cast_studio_play_{slugify_name(announcement.name)}"
        self._attr_unique_id = (
            f"cast_studio_announcement_play_{announcement.announcement_id}"
        )

    async def async_press(self) -> None:
        """Play now, on the Play on player."""
        manager = get_manager(self.hass)
        announcement = manager.announcement(self.announcement_id)
        if announcement is None:
            msg = "This announcement no longer exists"
            raise HomeAssistantError(msg)
        await async_announce(self.hass, announcement, manager.play_target(announcement))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up rebuild and play buttons for each announcement."""
    async_setup_announcement_platform(
        hass, entry, async_add_entities, CastStudioAnnouncementRebuildButton
    )
    async_setup_announcement_platform(
        hass, entry, async_add_entities, CastStudioAnnouncementPlayButton
    )
