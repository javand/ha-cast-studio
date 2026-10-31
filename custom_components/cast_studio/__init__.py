"""
Home Assistant Custom Integration for CastStudio.

Builds and plays per-person briefings: narrated slides or audio segments,
stored ahead of time and played on a media player on demand.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    ConfigEntryChange,
    ConfigSubentry,
)
from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import briefing
from .announcement_play import async_announce
from .announcement_runtime import DATA_ANNOUNCEMENTS, AnnouncementManager, get_manager
from .announcements import find_announcement
from .const import (
    CONF_COMMUTE_DESTINATION,
    CONF_DEFAULT_TRANSPORTATION_MODE,
    CONF_OUTPUT_DIR,
    CONF_PHONETIC_NAME,
    CONF_RECIPIENT_NAME,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PHONETIC_NAME,
    DEFAULT_RECIPIENT_NAME,
    DEFAULT_TRANSPORTATION_MODE,
    DOMAIN,
    LOGGER,
)
from .people import (
    DEFAULT_OUTPUT_FORMAT,
    ROUTINE_OUTPUT_FORMAT,
    ROUTINE_PERSON_ID,
    SUBENTRY_PERSON,
    SUBENTRY_ROUTINE,
    build_person_data,
    find_person,
    find_routine,
    get_loaded_entry,
)
from .routine_entity import async_add_new_routine_entities

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.SELECT,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

ATTR_PERSON = "person"
ATTR_ROUTINE = "routine"
ATTR_TARGET = "target"
ATTR_SKIP_PLAYED = "skip_played"
ATTR_VOLUME = "volume"
ATTR_ANNOUNCEMENT = "announcement"

PREBUILD_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_PERSON): cv.string,
            vol.Optional(ATTR_ROUTINE): cv.string,
        }
    ),
    cv.has_at_least_one_key(ATTR_PERSON, ATTR_ROUTINE),
)
REFRESH_SCHEMA = PREBUILD_SCHEMA
PLAY_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_PERSON): cv.string,
        vol.Required(ATTR_ROUTINE): cv.string,
        vol.Optional(ATTR_TARGET): cv.entity_domain("media_player"),
        vol.Optional(ATTR_SKIP_PLAYED, default=False): cv.boolean,
        vol.Optional(ATTR_VOLUME): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
    }
)
REBUILD_ANNOUNCEMENT_SCHEMA = vol.Schema({vol.Required(ATTR_ANNOUNCEMENT): cv.string})
ANNOUNCE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ANNOUNCEMENT): cv.string,
        vol.Optional(ATTR_TARGET): cv.entity_domain("media_player"),
    }
)


def _require_loaded_entry(hass: HomeAssistant) -> ConfigEntry:
    """Return the loaded entry, or raise when CastStudio is not set up."""
    if (entry := get_loaded_entry(hass)) is None:
        msg = "CastStudio is not set up: add the integration first"
        raise HomeAssistantError(msg)
    return entry


def _register_briefing_actions(hass: HomeAssistant) -> None:
    """Register the engine's ``prebuild``, ``refresh`` and ``play`` actions."""

    async def _async_build(call: ServiceCall, build: Any) -> None:
        entry = _require_loaded_entry(hass)
        person_name = call.data.get(ATTR_PERSON)
        if routine_name := call.data.get(ATTR_ROUTINE):
            routine = find_routine(entry, routine_name, person_name)
            await build(hass, entry, routine=routine)
            return
        if (person := find_person(entry, person_name)) is None:
            msg = f"CastStudio person '{person_name}' not found"
            raise HomeAssistantError(msg)
        await build(hass, entry, person=person)

    async def _async_handle_prebuild(call: ServiceCall) -> None:
        await _async_build(call, briefing.async_prebuild)

    async def _async_handle_refresh(call: ServiceCall) -> None:
        await _async_build(call, briefing.async_refresh)

    async def _async_handle_play(call: ServiceCall) -> None:
        entry = _require_loaded_entry(hass)
        routine = find_routine(
            entry, call.data[ATTR_ROUTINE], call.data.get(ATTR_PERSON)
        )
        await briefing.async_play(
            hass,
            entry,
            routine,
            target=call.data.get(ATTR_TARGET),
            skip_played=call.data.get(ATTR_SKIP_PLAYED, False),
            volume=call.data.get(ATTR_VOLUME),
        )

    hass.services.async_register(
        DOMAIN, "prebuild", _async_handle_prebuild, schema=PREBUILD_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "refresh", _async_handle_refresh, schema=REFRESH_SCHEMA
    )
    hass.services.async_register(DOMAIN, "play", _async_handle_play, schema=PLAY_SCHEMA)


def _register_announcement_actions(hass: HomeAssistant) -> None:
    """Register the ``announce`` and ``rebuild_announcement`` actions."""

    async def _async_handle_announce(call: ServiceCall) -> None:
        entry = _require_loaded_entry(hass)
        announcement = find_announcement(entry, call.data[ATTR_ANNOUNCEMENT])
        await async_announce(hass, announcement, call.data.get(ATTR_TARGET))

    async def _async_handle_rebuild(call: ServiceCall) -> None:
        entry = _require_loaded_entry(hass)
        announcement = find_announcement(entry, call.data[ATTR_ANNOUNCEMENT])
        manager = get_manager(hass)
        await manager.async_rebuild(announcement.announcement_id)
        state = manager.state(announcement.announcement_id)
        if state is not None and state.status == "error":
            msg = f"Announcement '{announcement.name}' did not build: {state.error}"
            raise HomeAssistantError(msg)

    hass.services.async_register(
        DOMAIN, "announce", _async_handle_announce, schema=ANNOUNCE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        "rebuild_announcement",
        _async_handle_rebuild,
        schema=REBUILD_ANNOUNCEMENT_SCHEMA,
    )


async def async_setup(hass: HomeAssistant, config: dict) -> bool:  # noqa: ARG001
    """Register the briefing and announcement actions."""
    hass.data.setdefault(DOMAIN, {})
    _register_briefing_actions(hass)
    _register_announcement_actions(hass)
    return True


def _entry_setting(entry: ConfigEntry, key: str, default: Any) -> Any:
    """Read an entry setting, options first, then data, then the default."""
    return entry.options.get(key, entry.data.get(key, default))


async def async_migrate_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Migrate a CastStudio entry to the current version."""
    if entry.version > 2:  # noqa: PLR2004
        LOGGER.error("Cannot downgrade CastStudio entry from version %s", entry.version)
        return False

    if entry.version == 1:
        # v1 → v2 (spec §5.7): the recipient becomes the first person.
        name = _entry_setting(entry, CONF_RECIPIENT_NAME, DEFAULT_RECIPIENT_NAME)
        person = build_person_data(
            name,
            _entry_setting(entry, CONF_PHONETIC_NAME, DEFAULT_PHONETIC_NAME),
            _entry_setting(entry, CONF_COMMUTE_DESTINATION, None),
            _entry_setting(
                entry, CONF_DEFAULT_TRANSPORTATION_MODE, DEFAULT_TRANSPORTATION_MODE
            ),
        )
        hass.config_entries.async_add_subentry(
            entry,
            ConfigSubentry(
                data=MappingProxyType(person),
                subentry_type=SUBENTRY_PERSON,
                title=name,
                unique_id=None,
            ),
        )
        hass.config_entries.async_update_entry(entry, version=2, minor_version=2)
        LOGGER.info("Migrated CastStudio entry to version 2 with person %s", name)

    if entry.version == 2 and entry.minor_version < 2:  # noqa: PLR2004
        _migrate_output_format_to_routines(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=2)
        LOGGER.info("Moved CastStudio output formats from people to routines")

    return True


def _migrate_output_format_to_routines(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Give each routine its person's output format, then drop it from people."""
    # People stored the format under the same key routines use now.
    formats = {
        sub.subentry_id: sub.data.get(ROUTINE_OUTPUT_FORMAT) or DEFAULT_OUTPUT_FORMAT
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_PERSON
    }
    for sub in list(entry.subentries.values()):
        data = dict(sub.data)
        if sub.subentry_type == SUBENTRY_ROUTINE:
            if ROUTINE_OUTPUT_FORMAT in data:
                continue
            data[ROUTINE_OUTPUT_FORMAT] = formats.get(
                data.get(ROUTINE_PERSON_ID), DEFAULT_OUTPUT_FORMAT
            )
        elif sub.subentry_type == SUBENTRY_PERSON:
            if data.pop(ROUTINE_OUTPUT_FORMAT, None) is None:
                continue
        else:
            continue
        hass.config_entries.async_update_subentry(entry, sub, data=data)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up CastStudio from a config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data["output_dir"] = _entry_setting(
        entry, CONF_OUTPUT_DIR, DEFAULT_OUTPUT_DIR
    )
    await briefing.async_setup_runtime(hass, entry)
    manager = AnnouncementManager(hass, entry, domain_data["output_dir"])
    domain_data[DATA_ANNOUNCEMENTS] = manager

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await briefing.async_refresh_sensors(hass, entry)
    await manager.async_setup()

    # Not an update listener: those cannot be used with OptionsFlowWithReload.
    @callback
    def _entry_changed(change: ConfigEntryChange, changed: ConfigEntry) -> None:
        if change is ConfigEntryChange.UPDATED and changed is entry:
            async_add_new_routine_entities(hass)
            hass.async_create_task(manager.async_sync(), f"{DOMAIN} announcement sync")

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, _entry_changed)
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        if manager := hass.data.get(DOMAIN, {}).pop(DATA_ANNOUNCEMENTS, None):
            await manager.async_unload()
        briefing.async_unload_runtime(hass)
    return unloaded
