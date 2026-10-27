"""Select platform for cast_studio: the player a Play button uses."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .announcement_entity import (
    AnnouncementEntity,
    async_setup_announcement_platform,
)
from .announcement_runtime import get_manager
from .people import slugify_name
from .routine_entity import (
    RoutineEntity,
    async_setup_routine_platform,
    current_routine,
    play_target,
    set_play_target,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .announcements import Announcement
    from .people import Person, Routine


class CastStudioAnnouncementPlayOnSelect(AnnouncementEntity, SelectEntity):
    """Which media player the Play button uses; starts at the Play on default."""

    _attr_name = "Play on"
    _attr_icon = "mdi:cast-audio"

    def __init__(self, announcement: Announcement) -> None:
        """Initialise as ``select.cast_studio_play_on_<name>``."""
        super().__init__(announcement)
        self.entity_id = f"select.cast_studio_play_on_{slugify_name(announcement.name)}"
        self._attr_unique_id = (
            f"cast_studio_announcement_play_on_{announcement.announcement_id}"
        )

    @property
    def current_option(self) -> str | None:
        """Return the chosen player, or the announcement's default."""
        announcement = self.announcement()
        if announcement is None:
            return None
        return get_manager(self.hass).play_target(announcement)

    @property
    def options(self) -> list[str]:
        """Return every media player, plus the current one if it is gone."""
        players = set(self.hass.states.async_entity_ids("media_player"))
        if (current := self.current_option) is not None:
            players.add(current)
        return sorted(players)

    async def async_select_option(self, option: str) -> None:
        """Play on ``option`` from now on, until the default changes."""
        if (announcement := self.announcement()) is not None:
            get_manager(self.hass).set_play_target(announcement, option)


class CastStudioRoutinePlayOnSelect(RoutineEntity, SelectEntity):
    """Which media player the routine's Play buttons use."""

    _attr_name = "Play on"
    _attr_icon = "mdi:cast-audio"

    def __init__(self, person: Person, routine: Routine) -> None:
        """Initialise as ``select.cast_studio_play_on_<person>_<routine>``."""
        super().__init__(person, routine)
        self.entity_id = f"select.cast_studio_play_on_{self.routine_slug}"
        self._attr_unique_id = f"cast_studio_routine_play_on_{routine.routine_id}"

    async def async_added_to_hass(self) -> None:
        """Follow the person's output device when it changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_CONFIG_ENTRY_CHANGED, self._async_entry_changed
            )
        )

    @callback
    def _async_entry_changed(self, *_args: object) -> None:
        self.async_write_ha_state()

    @property
    def current_option(self) -> str | None:
        """Return the chosen player, or the person's output device."""
        try:
            person, routine = current_routine(self.hass, self.routine_id)
        except HomeAssistantError:
            return None
        return play_target(self.hass, person, routine)

    @property
    def options(self) -> list[str]:
        """Return every media player, plus the current one if it is gone."""
        players = set(self.hass.states.async_entity_ids("media_player"))
        if (current := self.current_option) is not None:
            players.add(current)
        return sorted(players)

    async def async_select_option(self, option: str) -> None:
        """Play on ``option`` from now on, until the output device changes."""
        person, routine = current_routine(self.hass, self.routine_id)
        set_play_target(self.hass, person, routine, option)
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a Play on select for each announcement and routine."""
    async_setup_announcement_platform(
        hass, entry, async_add_entities, CastStudioAnnouncementPlayOnSelect
    )
    async_setup_routine_platform(
        hass, entry, async_add_entities, CastStudioRoutinePlayOnSelect
    )
