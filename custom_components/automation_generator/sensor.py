"""Sensor platform for automation_generator."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity

from .const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


class AutomationGeneratorRoutineSensor(SensorEntity):
    """Sensor tracking routine compilation and playback lifecycle."""

    _attr_has_entity_name = True

    def __init__(
        self,
        routine_name: str,
        hass: HomeAssistant | None = None,
    ) -> None:
        """Initialize the routine status sensor."""
        self.routine_name = routine_name
        self._attr_unique_id = f"automation_generator_{routine_name}"
        title = routine_name.replace("_", " ").title()
        self._attr_name = f"Automation Generator {title}"
        self.entity_id = f"sensor.automation_generator_{routine_name}"
        self._attr_icon = "mdi:play-circle-outline"
        self._state: str = "idle"
        self._attr_duration: float | None = None
        self._attr_last_pregenerated: str | None = None
        self._attr_video_path: str | None = None
        self._attr_audio_path: str | None = None
        self._attr_slide_count: int | None = None
        self._attr_active_task: str | None = None
        if hass is not None:
            self.hass = hass

    @property
    def native_value(self) -> str:
        """Return the native value of the sensor."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return sensor state attributes."""
        return {
            "duration": self._attr_duration,
            "last_pregenerated": self._attr_last_pregenerated,
            "video_path": self._attr_video_path,
            "audio_path": self._attr_audio_path,
            "slide_count": self._attr_slide_count,
            "active_task": self._attr_active_task,
        }

    def update_state(  # noqa: PLR0912, PLR0913
        self,
        state: str,
        *,
        duration: float | None = None,
        last_pregenerated: str | None = None,
        video_path: str | None = None,
        audio_path: str | None = None,
        slide_count: int | None = None,
        active_task: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Update sensor state and attributes."""
        self._state = state
        if duration is not None:
            self._attr_duration = duration
        if last_pregenerated is not None:
            self._attr_last_pregenerated = last_pregenerated
        if video_path is not None:
            self._attr_video_path = video_path
        if audio_path is not None:
            self._attr_audio_path = audio_path
        if slide_count is not None:
            self._attr_slide_count = slide_count

        if active_task is not None:
            self._attr_active_task = active_task
        elif attributes is not None and "active_task" in attributes:
            self._attr_active_task = attributes["active_task"]
        elif state in ("idle", "ready", "error") and active_task is None:
            self._attr_active_task = None

        if attributes:
            if "duration" in attributes:
                self._attr_duration = attributes["duration"]
            if "last_pregenerated" in attributes:
                self._attr_last_pregenerated = attributes["last_pregenerated"]
            if "video_path" in attributes:
                self._attr_video_path = attributes["video_path"]
            if "audio_path" in attributes:
                self._attr_audio_path = attributes["audio_path"]
            if "slide_count" in attributes:
                self._attr_slide_count = attributes["slide_count"]

        self.async_write_ha_state()

    def async_write_ha_state(self) -> None:
        """Write state to HA state machine, handling mock or real environments."""
        if (
            self.hass is not None
            and hasattr(self.hass, "states")
            and hasattr(self.hass.states, "async_set")
        ):
            with contextlib.suppress(Exception):
                self.hass.states.async_set(
                    self.entity_id,
                    self.native_value,
                    self.extra_state_attributes,
                )
        with contextlib.suppress(Exception):
            super().async_write_ha_state()


def async_get_or_create_sensor(
    hass: HomeAssistant,
    routine_name: str,
) -> AutomationGeneratorRoutineSensor:
    """Get existing or create and register a new routine sensor."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    sensors = domain_data.setdefault("sensors", {})
    if routine_name not in sensors:
        sensor = AutomationGeneratorRoutineSensor(routine_name, hass=hass)
        sensors[routine_name] = sensor
        cb = domain_data.get("async_add_entities")
        if cb is not None and callable(cb):
            try:
                cb([sensor])
            except Exception as err:  # noqa: BLE001
                LOGGER.debug("Error adding entity dynamically: %s", err)
        elif hasattr(hass, "states") and hasattr(hass.states, "async_set"):
            sensor.async_write_ha_state()
    return sensors[routine_name]


def async_update_routine_sensor(
    hass: HomeAssistant,
    routine_name: str,
    state: str,
    attributes: dict[str, Any] | None = None,
    **kwargs: Any,
) -> AutomationGeneratorRoutineSensor:
    """Update state and attributes of a routine sensor."""
    sensor = async_get_or_create_sensor(hass, routine_name)
    sensor.update_state(state, attributes=attributes, **kwargs)
    return sensor


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Any,  # noqa: ARG001 Unused function argument
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Automation Generator sensors from a config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data["async_add_entities"] = async_add_entities
    sensors = domain_data.setdefault("sensors", {})

    routines = domain_data.get("routines", {})
    for routine_name in routines:
        if routine_name not in sensors:
            sensors[routine_name] = AutomationGeneratorRoutineSensor(
                routine_name, hass=hass
            )

    if sensors:
        async_add_entities(list(sensors.values()))


async def async_setup_platform(
    hass: HomeAssistant,
    config: dict[str, Any],  # noqa: ARG001 Unused function argument
    async_add_entities: AddEntitiesCallback,
    discovery_info: dict[str, Any] | None = None,  # noqa: ARG001
) -> None:
    """Set up Automation Generator sensors from platform configuration."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data["async_add_entities"] = async_add_entities
    sensors = domain_data.setdefault("sensors", {})

    routines = domain_data.get("routines", {})
    for routine_name in routines:
        if routine_name not in sensors:
            sensors[routine_name] = AutomationGeneratorRoutineSensor(
                routine_name, hass=hass
            )

    if sensors:
        async_add_entities(list(sensors.values()))
