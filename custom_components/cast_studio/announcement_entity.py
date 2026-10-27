"""Shared base for the entities of one announcement (its slide and button)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .announcement_runtime import (
    DATA_ENTITY_ADDERS,
    SIGNAL_ANNOUNCEMENT_UPDATED,
    AnnouncementState,
    get_manager,
)
from .announcements import get_announcements
from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .announcement_build import AnnouncementBuild
    from .announcements import Announcement


class AnnouncementEntity(Entity):
    """Follows one announcement's state through the update signal."""

    _attr_has_entity_name = True

    def __init__(self, announcement: Announcement) -> None:
        """Initialise for one announcement, on its own device."""
        self.announcement_id = announcement.announcement_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"announcement_{announcement.announcement_id}")},
            name=announcement.name,
        )

    def announcement_state(self) -> AnnouncementState | None:
        """Return the announcement's current state from the manager."""
        return get_manager(self.hass).state(self.announcement_id)

    def announcement_build(self) -> AnnouncementBuild | None:
        """Return the announcement's current build, if it has one."""
        state = self.announcement_state()
        return state.build if state else None

    def announcement(self) -> Announcement | None:
        """Return the announcement's current settings."""
        return get_manager(self.hass).announcement(self.announcement_id)

    async def async_added_to_hass(self) -> None:
        """Update whenever the announcement's state changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_ANNOUNCEMENT_UPDATED, self._async_updated
            )
        )

    @callback
    def _async_updated(self, announcement_id: str) -> None:
        if announcement_id == self.announcement_id and self.announcement_state():
            self._handle_update()
            self.async_write_ha_state()

    def _handle_update(self) -> None:
        """Refresh cached attributes before the state is written."""


def async_setup_announcement_platform(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    factory: Callable[[Announcement], AnnouncementEntity],
) -> None:
    """Add a platform's entities now, and for announcements added later."""

    def _add(announcements: list[Announcement]) -> None:
        for announcement in announcements:
            async_add_entities(
                [factory(announcement)],
                config_subentry_id=announcement.announcement_id,
            )

    _add(get_announcements(entry))
    adders: list[Any] = hass.data.setdefault(DOMAIN, {}).setdefault(
        DATA_ENTITY_ADDERS, []
    )
    adders.append(_add)
    entry.async_on_unload(lambda: adders.remove(_add))
