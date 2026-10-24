"""
Home Assistant Custom Integration for CastStudio.

Builds and plays per-person briefings: narrated slides or audio segments,
stored ahead of time and played on a media player on demand.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from . import briefing
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
    SUBENTRY_PERSON,
    build_person_data,
    find_person,
    find_routine,
    get_loaded_entry,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

ATTR_PERSON = "person"
ATTR_ROUTINE = "routine"
ATTR_TARGET = "target"
ATTR_SKIP_PLAYED = "skip_played"

PREBUILD_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_PERSON): cv.string,
            vol.Optional(ATTR_ROUTINE): cv.string,
        }
    ),
    cv.has_at_least_one_key(ATTR_PERSON, ATTR_ROUTINE),
)
PLAY_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_PERSON): cv.string,
        vol.Required(ATTR_ROUTINE): cv.string,
        vol.Optional(ATTR_TARGET): cv.entity_domain("media_player"),
        vol.Optional(ATTR_SKIP_PLAYED, default=False): cv.boolean,
    }
)


def _require_loaded_entry(hass: HomeAssistant) -> ConfigEntry:
    """Return the loaded entry, or raise when CastStudio is not set up."""
    if (entry := get_loaded_entry(hass)) is None:
        msg = "CastStudio is not set up: add the integration first"
        raise HomeAssistantError(msg)
    return entry


def _register_briefing_actions(hass: HomeAssistant) -> None:
    """Register the new engine's ``prebuild`` and ``play`` actions (spec §5.3)."""

    async def _async_handle_prebuild(call: ServiceCall) -> None:
        entry = _require_loaded_entry(hass)
        person_name = call.data.get(ATTR_PERSON)
        if routine_name := call.data.get(ATTR_ROUTINE):
            routine = find_routine(entry, routine_name, person_name)
            await briefing.async_prebuild(hass, entry, routine=routine)
            return
        if (person := find_person(entry, person_name)) is None:
            msg = f"CastStudio person '{person_name}' not found"
            raise HomeAssistantError(msg)
        await briefing.async_prebuild(hass, entry, person=person)

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
        )

    hass.services.async_register(
        DOMAIN, "prebuild", _async_handle_prebuild, schema=PREBUILD_SCHEMA
    )
    hass.services.async_register(DOMAIN, "play", _async_handle_play, schema=PLAY_SCHEMA)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:  # noqa: ARG001
    """Register the ``prebuild`` and ``play`` actions."""
    hass.data.setdefault(DOMAIN, {})
    _register_briefing_actions(hass)
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
        hass.config_entries.async_update_entry(entry, version=2)
        LOGGER.info("Migrated CastStudio entry to version 2 with person %s", name)

    return True


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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await briefing.async_refresh_sensors(hass, entry)
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        briefing.async_unload_runtime(hass)
    return unloaded
