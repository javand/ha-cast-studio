"""Shared base and actions for the entities on a routine's device."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from . import briefing
from .const import DOMAIN
from .people import (
    get_loaded_entry,
    get_people,
    get_routines,
    slugify_name,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .people import Person, Routine

DATA_ROUTINE_ENTITY_ADDERS = "routine_entity_adders"
DATA_ROUTINE_PLAY_ON = "routine_play_on"


def routine_device_info(person: Person, routine: Routine) -> DeviceInfo:
    """Return the device a routine's entities share, named like its list row."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"routine_{routine.routine_id}")},
        name=f"{routine.name} ({person.name})",
    )


def routine_slug(person: Person, routine: Routine) -> str:
    """Return the ``<person>_<routine>`` part of the routine's entity ids."""
    return f"{slugify_name(person.name)}_{slugify_name(routine.name)}"


class RoutineEntity(Entity):
    """One control on a routine's device."""

    _attr_has_entity_name = True

    def __init__(self, person: Person, routine: Routine) -> None:
        """Initialise for one routine, on its own device."""
        self.routine_id = routine.routine_id
        self.routine_slug = routine_slug(person, routine)
        self._attr_device_info = routine_device_info(person, routine)


def current_routine(hass: HomeAssistant, routine_id: str) -> tuple[Person, Routine]:
    """Return the routine's current settings and its person, or raise."""
    entry = get_loaded_entry(hass)
    if entry is not None:
        people = {person.person_id: person for person in get_people(entry)}
        for routine in get_routines(entry):
            if routine.routine_id == routine_id and routine.person_id in people:
                return people[routine.person_id], routine
    msg = "This routine no longer exists"
    raise HomeAssistantError(msg)


def play_target(hass: HomeAssistant, person: Person, routine: Routine) -> str | None:
    """
    Return the player the routine's Play uses.

    A player chosen on the device page wins until the person's output device
    changes; then the new output device applies.
    """
    chosen = (
        hass.data.get(DOMAIN, {}).get(DATA_ROUTINE_PLAY_ON, {}).get(routine.routine_id)
    )
    if chosen is not None and chosen[0] == person.media_player:
        return chosen[1]
    return person.media_player


def set_play_target(
    hass: HomeAssistant, person: Person, routine: Routine, target: str
) -> None:
    """Choose the player the routine's Play uses."""
    chosen = hass.data.setdefault(DOMAIN, {}).setdefault(DATA_ROUTINE_PLAY_ON, {})
    chosen[routine.routine_id] = (person.media_player, target)


async def async_rebuild_routine(hass: HomeAssistant, routine_id: str) -> None:
    """Build the routine's segments again, playing nothing."""
    entry = _loaded_entry(hass)
    _, routine = current_routine(hass, routine_id)
    await briefing.async_prebuild(hass, entry, routine=routine)


async def async_play_routine(
    hass: HomeAssistant, routine_id: str, *, rebuild: bool = False
) -> None:
    """Play the routine on its Play on player, rebuilding it first if asked."""
    entry = _loaded_entry(hass)
    person, routine = current_routine(hass, routine_id)
    if rebuild:
        await briefing.async_prebuild(hass, entry, routine=routine)
    await briefing.async_play(
        hass, entry, routine, target=play_target(hass, person, routine)
    )


def _loaded_entry(hass: HomeAssistant) -> ConfigEntry:
    if (entry := get_loaded_entry(hass)) is None:
        msg = "CastStudio is not set up: add the integration first"
        raise HomeAssistantError(msg)
    return entry


def async_setup_routine_platform(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    factory: Callable[[Person, Routine], RoutineEntity],
) -> None:
    """Add a platform's entities now, and for routines added later."""
    seen: set[str] = set()

    @callback
    def _add() -> None:
        people = {person.person_id: person for person in get_people(entry)}
        for routine in get_routines(entry):
            if routine.routine_id in seen or routine.person_id not in people:
                continue
            seen.add(routine.routine_id)
            async_add_entities(
                [factory(people[routine.person_id], routine)],
                config_subentry_id=routine.routine_id,
            )

    _add()
    adders: list[Any] = hass.data.setdefault(DOMAIN, {}).setdefault(
        DATA_ROUTINE_ENTITY_ADDERS, []
    )
    adders.append(_add)
    entry.async_on_unload(lambda: adders.remove(_add))


@callback
def async_add_new_routine_entities(hass: HomeAssistant) -> None:
    """Give any routine added since setup its controls."""
    for adder in list(hass.data.get(DOMAIN, {}).get(DATA_ROUTINE_ENTITY_ADDERS, [])):
        adder()
