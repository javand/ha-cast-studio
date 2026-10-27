"""Sensor platform for cast_studio."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .ai_health import SIGNAL_AI_ACCOUNT_ISSUE, get_ai_account_issue
from .const import DOMAIN, LOGGER
from .people import get_people, get_routines, slugify_name
from .routine_entity import routine_device_info

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .people import Person, Routine

BRIEFING_SENSORS = "briefing_sensors"


class CastStudioBriefingSensor(SensorEntity):
    """Readiness of one person's routine (spec §5.7)."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["idle", "generating", "ready", "playing", "error"]  # noqa: RUF012
    _attr_icon = "mdi:play-circle-outline"
    _unrecorded_attributes = frozenset({"ready_segments"})

    def __init__(
        self,
        hass: HomeAssistant,
        person: Person,
        routine: Routine,
    ) -> None:
        """Initialize the sensor as ``sensor.cast_studio_<person>_<routine>``."""
        self.hass = hass
        self.routine_name = f"{slugify_name(person.name)}_{slugify_name(routine.name)}"
        self.entity_id = f"sensor.cast_studio_{self.routine_name}"
        self._attr_unique_id = f"cast_studio_routine_{routine.routine_id}"
        self._attr_name = "Status"
        self._attr_device_info = routine_device_info(person, routine)
        self.person_name = person.name
        self.routine_display_name = routine.name
        self._state = "idle"
        self._briefing_day: str | None = None
        self._ready_segments: list[str] = []

    @property
    def native_value(self) -> str:
        """Return the routine's readiness."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the person, routine, briefing day and ready segment keys."""
        return {
            "person": self.person_name,
            "routine": self.routine_display_name,
            "briefing_day": self._briefing_day,
            "ready_segments": list(self._ready_segments),
            "ai_account_issue": get_ai_account_issue(self.hass),
        }

    async def async_added_to_hass(self) -> None:
        """Rewrite the state when an AI account problem opens or clears."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_AI_ACCOUNT_ISSUE, self._async_ai_issue_changed
            )
        )

    @callback
    def _async_ai_issue_changed(self) -> None:
        self.async_write_ha_state()

    def set_briefing_state(
        self,
        state: str,
        *,
        briefing_day: str | None = None,
        ready_segments: list[str] | None = None,
    ) -> None:
        """Update the state, and the readiness attributes when given."""
        self._state = state
        if briefing_day is not None:
            self._briefing_day = briefing_day
        if ready_segments is not None:
            self._ready_segments = list(ready_segments)
        self.async_write_ha_state()

    def async_write_ha_state(self) -> None:
        """Write the state, also before the entity platform has added it."""
        if getattr(self, "entity_platform", None) is None:
            with contextlib.suppress(Exception):
                self.hass.states.async_set(
                    self.entity_id, self.native_value, self.extra_state_attributes
                )
            return
        super().async_write_ha_state()


def async_get_or_create_briefing_sensor(
    hass: HomeAssistant,
    person: Person,
    routine: Routine,
) -> CastStudioBriefingSensor:
    """
    Get or create the sensor for one routine subentry.

    A routine added after setup gets its sensor here on first use, through the
    platform's dynamic-add callback.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    sensors = domain_data.setdefault(BRIEFING_SENSORS, {})
    if routine.routine_id not in sensors:
        sensor = CastStudioBriefingSensor(hass, person, routine)
        sensors[routine.routine_id] = sensor
        cb = domain_data.get("async_add_entities")
        if cb is not None and callable(cb):
            try:
                cb([sensor])
            except Exception as err:  # noqa: BLE001
                LOGGER.debug("Error adding entity dynamically: %s", err)
        elif hasattr(hass, "states") and hasattr(hass.states, "async_set"):
            sensor.async_write_ha_state()
    return sensors[routine.routine_id]


def _briefing_sensors_for_entry(
    hass: HomeAssistant, entry: Any
) -> list[CastStudioBriefingSensor]:
    """Create a sensor for each of the entry's routine subentries."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    sensors = domain_data.setdefault(BRIEFING_SENSORS, {})
    people = {person.person_id: person for person in get_people(entry)}
    created: list[CastStudioBriefingSensor] = []
    for routine in get_routines(entry):
        if routine.routine_id in sensors:
            continue
        sensor = CastStudioBriefingSensor(hass, people[routine.person_id], routine)
        sensors[routine.routine_id] = sensor
        created.append(sensor)
    return created


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Any,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a sensor for each of the entry's routines."""
    hass.data.setdefault(DOMAIN, {})["async_add_entities"] = async_add_entities
    if briefing_sensors := _briefing_sensors_for_entry(hass, entry):
        async_add_entities(briefing_sensors)
