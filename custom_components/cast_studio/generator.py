"""
Core Generator Engine for CastStudio.

Coordinates data ingestion, slide rendering via Pillow in executor,
TTS speech synthesis with per-step voice and personality conditioning,
native FFmpeg clip muxing and concatenation, companion metadata recording,
and intermediate scratchpad lifecycle management in a finally block.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import re
import wave
from collections.abc import Mapping
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .ai_client import (
    _enforce_recipient_separation,
    async_generate_ai_image,
    async_generate_step_content,
)
from .const import (
    CONF_COMMUTE_DESTINATION,
    CONF_DEFAULT_TRANSPORTATION_MODE,
    CONF_MAPS_API_KEY,
    DEFAULT_HOST_VOICE,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_TRANSPORTATION_MODE,
    DOMAIN,
    LOGGER,
)
from .ffmpeg_runner import (
    async_run_ffmpeg,
    build_slide_clip_command,
)
from .helpers import async_run_in_executor
from .people import find_person, get_first_person, get_loaded_entry, get_people
from .pronunciation import apply_pronunciations, build_lexicon
from .slide_renderer import render_slide
from .tts_client import async_synthesize_speech

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

DEFAULT_SLIDE_PAD_SECONDS = 0.5
FINAL_SLIDE_PAD_SECONDS = 2.5

_async_run_in_executor = async_run_in_executor


class GeneratorError(Exception):
    """Base exception for generator operations."""


def _get_step_entity_ids(step: dict[str, Any]) -> list[str]:
    """Extract ordered, deduplicated entity IDs configured for a step."""
    raw_ids: list[str] = []
    for key in (
        "entity_id",
        "entity_ids",
        "commute_sensor",
        "commute_sensors",
        "calendar_entity",
        "calendar_entities",
        "todo_entity",
        "todo_entities",
    ):
        val = step.get(key)
        if isinstance(val, list):
            for item in val:
                if item and str(item).strip():
                    raw_ids.extend(p.strip() for p in str(item).split(",") if p.strip())
        elif val and str(val).strip():
            raw_ids.extend(p.strip() for p in str(val).split(",") if p.strip())

    seen: set[str] = set()
    eids: list[str] = []
    for eid in raw_ids:
        if eid not in seen:
            seen.add(eid)
            eids.append(eid)
    return eids


DYNAMIC_IMAGE_STEP_KEYS = frozenset({"greeting", "fun_fact", "national_day", "closing"})

# The closing slide's AI image follows the day of the week and the season.
_CLOSING_WEEKDAY_MOODS = (
    "a fresh, energized start to the week",
    "steady momentum and good progress",
    "bright midweek sunshine and good cheer",
    "the week's finish line coming into view",
    "the happy feeling of the weekend almost here",
    "a relaxed, joyful weekend morning",
    "a calm, restful Sunday",
)
_CLOSING_SEASON_SCENES = {
    "winter": "a crisp winter morning with sparkling frost and a warm glowing sunrise",
    "spring": "a spring morning with blooming flowers and fresh green meadows",
    "summer": "a bright summer morning with blue skies and fields of sunflowers",
    "autumn": "a golden autumn morning along a sunlit path of colorful leaves",
}


def _season(month: int) -> str:
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


def closing_subtitle(now: datetime) -> str:
    """Return the closing slide's upbeat day line, e.g. 'Happy Thursday!'."""
    return f"Happy {now:%A}!"


def _closing_image_prompt(now: datetime) -> str:
    """Prompt for a closing image that captures having a great day today."""
    scene = _CLOSING_SEASON_SCENES[_season(now.month)]
    mood = _CLOSING_WEEKDAY_MOODS[now.weekday()]
    return (
        "A joyful, uplifting cinematic photograph that captures the feeling of "
        f"having a great day: {scene}, evoking {mood}. Warm golden light, bright "
        "optimistic colors, inviting and cheerful, 16:9 widescreen aspect ratio, "
        "no text, no words, no typography"
    )


# Seconds to wait for commute sensors to refresh before using their last value.
SENSOR_REFRESH_TIMEOUT = 15


def is_step_image_dynamic(step: dict[str, Any], step_name: str = "") -> bool:
    """Determine whether a step should generate a dynamic AI image or static asset."""
    # 1. Explicit dynamic_image boolean on step
    if "dynamic_image" in step and step["dynamic_image"] is not None:
        return bool(step["dynamic_image"])

    # 2. Explicit image_mode string on step
    if step.get("image_mode"):
        return str(step["image_mode"]).strip().lower() == "dynamic"

    # 3. Explicit static flag on step
    if step.get("static") is True or step.get("static_image") is True:
        return False

    # 4. Explicit local image_path or icon_path configured on step
    if step.get("image_path") or step.get("icon_path"):
        return False

    # 5. Strict allowlist: only these slides generate dynamic AI imagery.
    step_key = (
        str(step.get("type") or step_name or step.get("name") or "").strip().lower()
    )
    # 6. Everything else uses bundled artwork or local renderers.
    return step_key in DYNAMIC_IMAGE_STEP_KEYS


def _format_weather_service_response(
    eid: str,
    state_obj: Any,
    forecasts: list[Any],
) -> str:
    """Format weather state and weather.get_forecasts response into context string."""
    state_val = getattr(state_obj, "state", "unknown") if state_obj else "unknown"
    attrs = (getattr(state_obj, "attributes", None) or {}) if state_obj else {}
    temp = attrs.get("temperature")
    unit = attrs.get("temperature_unit") or "°F"
    line_parts = [f"Weather ({eid}): Condition: {state_val}"]
    if temp is not None:
        line_parts.append(f"Temperature: {temp}{unit}")
    if (humidity := attrs.get("humidity")) is not None:
        line_parts.append(f"Humidity: {humidity}%")

    forecast_parts: list[str] = []
    for idx, fc in enumerate(forecasts[:2]):
        if not isinstance(fc, dict):
            continue
        day_label = "Today" if idx == 0 else "Tomorrow"
        fc_bits: list[str] = []
        if cond := fc.get("condition"):
            fc_bits.append(f"Condition: {cond}")
        if (hi := fc.get("temperature")) is not None:
            fc_bits.append(f"High: {hi}{unit}")
        if (lo := fc.get("templow")) is not None:
            fc_bits.append(f"Low: {lo}{unit}")
        if (precip := fc.get("precipitation_probability")) is not None:
            fc_bits.append(f"Rain chance: {precip}%")
        if fc_bits:
            forecast_parts.append(f"{day_label} ({', '.join(fc_bits)})")

    if forecast_parts:
        line_parts.append(f"Forecast: {'; '.join(forecast_parts)}")
    return ", ".join(line_parts)


def _format_calendar_service_response(
    eid: str, events: list[Any], name: str | None = None
) -> str:
    """
    Format calendar.get_events response list into context string.

    The calendar's name leads the line so a briefing drawing on several
    calendars can say which one each event is on.
    """
    label = f"Calendar '{name}' ({eid})" if name else f"Calendar ({eid})"
    event_strs: list[str] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        summary = (
            ev.get("summary")
            or ev.get("message")
            or ev.get("description")
            or "Scheduled Event"
        )
        parts = [f"Summary: {summary}"]
        if start := ev.get("start"):
            parts.append(f"Start: {start}")
        if end := ev.get("end"):
            parts.append(f"End: {end}")
        if location := ev.get("location"):
            parts.append(f"Location: {location}")
        event_strs.append(", ".join(parts))

    if not event_strs:
        return f"{label}: No events on this calendar today."
    return f"{label}: {'; '.join(event_strs)}"


def _calendar_name(hass: HomeAssistant, eid: str) -> str | None:
    """Return a calendar's friendly name, if Home Assistant knows it."""
    states = getattr(hass, "states", None)
    state = states.get(eid) if states is not None else None
    name = state.attributes.get("friendly_name") if state is not None else None
    return name if isinstance(name, str) and name else None


def _event_start_key(start: Any) -> str:
    """
    Sort key for merging events from several calendars into one timeline.

    ISO dates and date-times sort as strings, and an all-day date sorts
    before that day's timed events. Missing starts go last.
    """
    if isinstance(start, dict):
        start = start.get("dateTime") or start.get("date")
    return str(start) if start else "~"


def _format_todo_service_response(eid: str, items: list[Any]) -> str:
    """Format todo.get_items response list into context string."""
    summaries = [
        str(item.get("summary")).strip()
        for item in items
        if isinstance(item, dict)
        and item.get("summary")
        and str(item.get("summary")).strip()
    ]
    if summaries:
        return f"To-do ({eid}): {len(summaries)} tasks pending: {', '.join(summaries)}"
    return f"To-do ({eid}): 0 tasks pending"


ISO_DATE_LENGTH = 10
ISO_DATE_DASH_COUNT = 2


def _format_event_time_range(start: Any, end: Any) -> str:
    """Format calendar start and end into a human-readable time chip string."""

    def _parse_part(val: Any) -> tuple[str, bool]:
        if isinstance(val, dict):
            val = val.get("dateTime") or val.get("date")
        if not val:
            return "", False
        val_str = str(val).strip()
        if not val_str:
            return "", False
        if (
            len(val_str) == ISO_DATE_LENGTH
            and val_str.count("-") == ISO_DATE_DASH_COUNT
        ):
            return "All Day", True
        if "AM" in val_str.upper() or "PM" in val_str.upper():
            return val_str, False
        for parse_fn in (datetime.fromisoformat, time.fromisoformat):
            try:
                parsed = parse_fn(val_str)
                if isinstance(parsed, datetime) and parsed.tzinfo is not None:
                    parsed = dt_util.as_local(parsed)
                return parsed.strftime("%I:%M %p").lstrip("0"), False
            except (ValueError, TypeError):
                continue
        return val_str, False

    start_str, is_start_all_day = _parse_part(start)
    end_str, _ = _parse_part(end)

    if is_start_all_day or start_str == "All Day":
        return "All Day"
    if start_str and end_str and start_str != end_str:
        return f"{start_str} \u2013 {end_str}"
    return start_str or end_str or "All Day"


MIN_COMMUTE_ENTRIES_FOR_COMPARISON = 2


def _format_commute_comparison(
    commute_entries: list[tuple[str, float, str]],
) -> str | None:
    """Compare 2+ numeric commute sensors and state which option is faster."""
    if len(commute_entries) < MIN_COMMUTE_ENTRIES_FOR_COMPARISON:
        return None
    sorted_entries = sorted(commute_entries, key=lambda item: item[1])
    fastest_label, fastest_val, fastest_unit = sorted_entries[0]
    second_label, second_val, second_unit = sorted_entries[1]
    unit_str = f" {fastest_unit}" if fastest_unit else " min"
    sec_unit_str = f" {second_unit}" if second_unit else " min"
    delta = round(second_val - fastest_val, 1)
    if delta <= 0:
        return (
            f"Commute Comparison: {fastest_label} and {second_label} "
            f"have the same travel time ({fastest_val:g}{unit_str})."
        )
    return (
        f"Commute Comparison: {fastest_label} ({fastest_val:g}{unit_str}) is "
        f"{delta:g} minutes faster than {second_label} ({second_val:g}{sec_unit_str})."
    )


HTTP_OK = 200

DARK_MAP_STYLES = (
    "element:geometry|color:0x212121",
    "element:labels.icon|visibility:off",
    "element:labels.text.fill|color:0x757575",
    "element:labels.text.stroke|color:0x212121",
    "feature:administrative|element:geometry|color:0x757575",
    "feature:administrative.country|element:labels.text.fill|color:0x9e9e9e",
    "feature:administrative.land_parcel|visibility:off",
    "feature:administrative.locality|element:labels.text.fill|color:0xbdbdbd",
    "feature:poi|element:labels.text.fill|color:0x757575",
    "feature:poi.park|element:geometry|color:0x181818",
    "feature:poi.park|element:labels.text.fill|color:0x616161",
    "feature:poi.park|element:labels.text.stroke|color:0x1b1b1b",
    "feature:road|element:geometry.fill|color:0x2c2c2c",
    "feature:road|element:labels.text.fill|color:0x8a8a8a",
    "feature:road.arterial|element:geometry|color:0x373737",
    "feature:road.highway|element:geometry|color:0x3c3c3c",
    "feature:road.highway.controlled_access|element:geometry|color:0x4e4e4e",
    "feature:road.local|element:labels.text.fill|color:0x616161",
    "feature:transit|element:labels.text.fill|color:0x757575",
    "feature:water|element:geometry|color:0x000000",
    "feature:water|element:labels.text.fill|color:0x3d3d3d",
)


def _write_bytes_to_file(path: Path, data: bytes) -> None:
    """Write binary data to file atomically or ensuring parent dir exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _first_key(names: tuple[str, ...], *sources: Any) -> str | None:
    """Return the first non-blank value for any of ``names``, name by name."""
    for name in names:
        for source in sources:
            if (val := (source or {}).get(name)) and (val_str := str(val).strip()):
                return val_str
    return None


async def async_resolve_google_maps_api_key(
    hass: HomeAssistant,
) -> str | None:
    """Resolve Google Maps API key from config entry, secrets.yaml, or hass.data."""
    # 1. Check CastStudio config entries
    for entry in hass.config_entries.async_entries(DOMAIN):
        if key := _first_key(
            (
                CONF_MAPS_API_KEY,
                "google_maps_api_key",
                "google_maps_key",
                "gmaps_api_key",
            ),
            getattr(entry, "options", None),
            getattr(entry, "data", None),
        ):
            return key

    # 2. Check secrets.yaml via hass.config.path("secrets.yaml")
    try:
        secrets_path = Path(hass.config.path("secrets.yaml"))
        if secrets_path.is_file():

            def _read_secrets() -> dict[str, Any]:
                with secrets_path.open("r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}

            secrets_data = await _async_run_in_executor(hass, _read_secrets)
            if isinstance(secrets_data, dict) and (
                key := _first_key(
                    (
                        "google_maps_api_key",
                        "google_maps_key",
                        "gmaps_api_key",
                        "maps_api_key",
                        "google_directions_api_key",
                    ),
                    secrets_data,
                )
            ):
                return key
    except Exception as err:  # noqa: BLE001
        LOGGER.debug("Could not inspect secrets.yaml for Google Maps key: %s", err)

    # 3. Check other integrations in config_entries and hass.data
    for domain in ("google_travel_time", "google_maps"):
        for entry in hass.config_entries.async_entries(domain):
            if key := _first_key(
                ("api_key",),
                getattr(entry, "options", None),
                getattr(entry, "data", None),
            ):
                return key

    domain_data = hass.data.get(DOMAIN, {})
    if isinstance(domain_data, dict) and domain_data.get(CONF_MAPS_API_KEY):
        return str(domain_data[CONF_MAPS_API_KEY]).strip()
    return _first_key(("google_maps_api_key", "maps_api_key"), hass.data)


async def async_fetch_commute_map(  # noqa: PLR0913
    hass: HomeAssistant,
    origin: tuple[float, float],
    destination: tuple[float, float],
    mode: str,
    api_key: str,
    output_dir: str,
) -> dict[str, Any] | None:
    """Fetch route polyline and dark-mode static map image from Google Maps API."""
    if not api_key or not origin or not destination:
        return None

    try:
        session = async_get_clientsession(hass)
        dir_mode = "transit" if mode == "transit" else "driving"
        dir_params = {
            "origin": f"{origin[0]},{origin[1]}",
            "destination": f"{destination[0]},{destination[1]}",
            "mode": dir_mode,
            "key": api_key,
        }

        async with asyncio.timeout(10):
            async with session.get(
                "https://maps.googleapis.com/maps/api/directions/json",
                params=dir_params,
            ) as resp:
                if resp.status != HTTP_OK:
                    LOGGER.warning("Google Directions API error HTTP %s", resp.status)
                    return None
                dir_data = await resp.json()

        if dir_data.get("status") != "OK" or not dir_data.get("routes"):
            LOGGER.warning(
                "Google Directions API response not OK: status=%s, msg=%s",
                dir_data.get("status"),
                dir_data.get("error_message"),
            )
            return None

        route = dir_data["routes"][0]
        summary = route.get("summary") or (
            "Driving" if dir_mode == "driving" else "Transit"
        )
        overview_polyline = route.get("overview_polyline", {}).get("points", "")
        legs = route.get("legs", [])
        leg = legs[0] if legs else {}
        distance = leg.get("distance", {}).get("text", "")
        duration = leg.get("duration", {}).get("text", "")
        duration_sec = leg.get("duration", {}).get("value")

        date_str = dt_util.now().strftime("%Y-%m-%d")
        cache_dir = Path(output_dir) / "cache"
        map_image_path = cache_dir / f"commute_map_{mode}_{date_str}.png"

        if not map_image_path.is_file() or map_image_path.stat().st_size == 0:
            static_params: list[tuple[str, str]] = [
                # 512x300 at scale 2 returns exactly 1024x600 and stays within
                # Google's documented 640x640 size limit.
                ("size", "512x300"),
                ("scale", "2"),
                ("format", "png"),
                ("maptype", "roadmap"),
                ("key", api_key),
            ]
            static_params.extend(("style", style) for style in DARK_MAP_STYLES)
            if overview_polyline:
                static_params.append(
                    ("path", f"weight:6|color:0x38bdf8ff|enc:{overview_polyline}")
                )
            static_params.append(
                ("markers", f"color:blue|label:H|{origin[0]},{origin[1]}")
            )
            static_params.append(
                ("markers", f"color:red|label:W|{destination[0]},{destination[1]}")
            )

            async with asyncio.timeout(15):
                async with session.get(
                    "https://maps.googleapis.com/maps/api/staticmap",
                    params=static_params,
                ) as resp:
                    if resp.status != HTTP_OK:
                        LOGGER.warning(
                            "Google Static Maps API error HTTP %s", resp.status
                        )
                        return None
                    image_bytes = await resp.read()

            await _async_run_in_executor(
                hass, _write_bytes_to_file, map_image_path, image_bytes
            )

        return {
            "summary": summary,
            "distance": distance,
            "duration": duration,
            "duration_sec": duration_sec,
            "polyline": overview_polyline,
            "map_image_path": str(map_image_path),
        }
    except Exception as err:  # noqa: BLE001
        LOGGER.warning("Failed to fetch commute map from Google Maps: %s", err)
        return None


def _parse_coordinates(value: Any) -> tuple[float, float] | None:
    """Parse a ``"lat,lon"`` string into a coordinate tuple."""
    if not isinstance(value, str) or "," not in value:
        return None
    lat_str, _, lon_str = value.partition(",")
    try:
        lat, lon = float(lat_str.strip()), float(lon_str.strip())
    except ValueError:
        return None
    if -90 <= lat <= 90 and -180 <= lon <= 180:  # noqa: PLR2004
        return (lat, lon)
    return None


def _resolve_location(  # noqa: PLR0911
    hass: HomeAssistant, value: Any
) -> tuple[float, float] | None:
    """Resolve a zone/entity id with lat/lon attributes, or a "lat,lon" string."""
    if not value or not isinstance(value, str):
        return None
    if coords := _parse_coordinates(value):
        return coords
    if not hasattr(hass, "states"):
        return None
    state = hass.states.get(value.strip())
    attrs = getattr(state, "attributes", None) or {}
    if not hasattr(attrs, "get"):
        return None
    lat, lon = attrs.get("latitude"), attrs.get("longitude")
    if not isinstance(lat, (int, float, str)) or not isinstance(lon, (int, float, str)):
        return None
    try:
        return (float(lat), float(lon))
    except (TypeError, ValueError):
        return None


def _get_entry_setting(hass: HomeAssistant, key: str) -> Any:
    """Read a CastStudio config entry option, falling back to entry data."""
    if not hasattr(hass, "config_entries"):
        return None
    try:
        entries = hass.config_entries.async_entries(DOMAIN)
    except Exception:  # noqa: BLE001
        return None
    if not entries:
        return None
    entry = entries[0]
    options = getattr(entry, "options", None) or {}
    data = getattr(entry, "data", None) or {}
    if not hasattr(options, "get") or not hasattr(data, "get"):
        return None
    value = options.get(key)
    return value if value not in (None, "") else data.get(key)


def _configured_commute_destination(hass: HomeAssistant) -> str | None:
    """Return the first person's destination zone, else the entry setting."""
    if (person := get_first_person(hass)) is not None:
        return person.commute_destination_zone
    return _get_entry_setting(hass, CONF_COMMUTE_DESTINATION)


def _resolve_commute_origin(hass: HomeAssistant) -> tuple[float, float] | None:
    """Resolve the commute origin: zone.home, then the HA configured location."""
    if coords := _resolve_location(hass, "zone.home"):
        return coords
    config = getattr(hass, "config", None)
    lat = getattr(config, "latitude", None)
    lon = getattr(config, "longitude", None)
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return (float(lat), float(lon))
    return None


def _resolve_commute_destination(
    hass: HomeAssistant,
    step: dict[str, Any],
    sensor_destination: str | None,
) -> tuple[float, float] | None:
    """
    Resolve the commute destination from configuration only.

    Order: step ``destination_zone``/``destination``, the configured
    destination (the first person's zone when people exist, else the
    ``commute_destination_zone`` entry setting), the drive sensor's ``destination``
    attribute (zone id or "lat,lon"), then the generic ``zone.work`` and
    ``zone.office``. Returns None when nothing resolves so the map fetch is
    skipped and the procedural map is used.

    New-engine segments (``standalone_segment``) use the person's own
    ``destination_zone`` only, never a household-wide fallback.
    """
    if step.get("standalone_segment"):
        return _resolve_location(hass, step.get("destination_zone"))
    for candidate in (
        step.get("destination_zone"),
        step.get("destination"),
        _configured_commute_destination(hass),
        sensor_destination,
        "zone.work",
        "zone.office",
    ):
        if coords := _resolve_location(hass, candidate):
            return coords
    return None


async def _async_refresh_sensors(
    hass: HomeAssistant,
    entity_ids: list[str | None],
    *,
    wait_seconds: float = SENSOR_REFRESH_TIMEOUT,
) -> None:
    """
    Ask the commute sensors for a fresh reading before they are read.

    Travel-time sensors often have polling off to save API calls; a failure
    only means the last reading is used.
    """
    ids = [eid for eid in dict.fromkeys(entity_ids) if eid]
    if not ids:
        return
    try:
        async with asyncio.timeout(wait_seconds):
            await hass.services.async_call(
                "homeassistant", "update_entity", {"entity_id": ids}, blocking=True
            )
    except Exception as err:  # noqa: BLE001 - the last reading still works
        LOGGER.debug("Could not refresh commute sensors %s: %s", ids, err)


def _sensor_minutes(hass: HomeAssistant, entity_id: str | None) -> int | None:
    """Return a travel-time sensor's state in whole minutes, if it has one."""
    if not entity_id or not hasattr(hass, "states"):
        return None
    state = hass.states.get(entity_id)
    if state is None:
        return None
    try:
        return round(float(state.state))
    except (TypeError, ValueError):
        return None


async def async_extract_commute_data(  # noqa: C901, PLR0912, PLR0915
    hass: HomeAssistant,
    step: dict[str, Any],
    mode_override: str | None = None,
) -> dict[str, Any]:
    """Extract commute data, compare drive and transit times, and fetch route map."""
    raw_candidates = (
        step.get("commute_sensors")
        or step.get("commute_sensor")
        or step.get("entity_ids")
        or step.get("entity_id")
        or []
    )
    if isinstance(raw_candidates, str):
        candidates = [p.strip() for p in raw_candidates.split(",") if p.strip()]
    elif isinstance(raw_candidates, (list, tuple)):
        candidates = []
        for item in raw_candidates:
            if isinstance(item, str):
                candidates.extend(p.strip() for p in item.split(",") if p.strip())
            elif item is not None:
                candidates.append(str(item))
    else:
        candidates = []

    # New-engine segments only use the person's own sensors (no household scan).
    standalone = bool(step.get("standalone_segment"))
    if standalone:
        candidates = []
    elif (
        not candidates and hasattr(hass, "states") and hasattr(hass.states, "async_all")
    ):
        try:
            candidates = [
                s.entity_id
                for s in hass.states.async_all()
                if "commute" in s.entity_id.lower()
                or "travel_time" in s.entity_id.lower()
            ]
        except Exception:  # noqa: BLE001
            candidates = []

    drive_sensor: str | None = None
    transit_sensor: str | None = None

    for eid in candidates:
        if not isinstance(eid, str):
            continue
        lower_eid = eid.lower()
        state_obj = hass.states.get(eid) if hasattr(hass, "states") else None
        attrs = getattr(state_obj, "attributes", {}) or {}
        if not isinstance(attrs, dict):
            attrs = {}
        mode_attr = str(attrs.get("mode", "")).lower()
        line_attr = str(attrs.get("line", "")).lower()
        dev_class = str(attrs.get("device_class", "")).lower()
        icon = str(attrs.get("icon", "")).lower()

        is_transit = (
            any(k in lower_eid for k in ("metro", "transit", "subway", "train", "bus"))
            or mode_attr in ("transit", "subway", "train", "bus")
            or bool(line_attr)
            or icon in ("mdi:train", "mdi:subway", "mdi:bus", "mdi:train-car")
        )
        is_drive = (
            any(k in lower_eid for k in ("drive", "waze", "car", "traffic"))
            or mode_attr in ("driving", "drive", "car")
            or "destination" in attrs
            or dev_class == "duration"
        )

        if is_transit and not transit_sensor:
            transit_sensor = eid
        elif (is_drive and not drive_sensor) or not drive_sensor:
            drive_sensor = eid
        elif not transit_sensor:
            transit_sensor = eid

    if standalone:
        drive_sensor = step.get("commute_drive_sensor") or None
        transit_sensor = step.get("commute_transit_sensor") or None
        # A driving or transit person hears (and sees) that one mode only.
        single_mode = str(step.get("transportation_mode") or "").lower()
        if single_mode == "driving":
            transit_sensor = None
        elif single_mode == "transit":
            drive_sensor = None
        drive_return_sensor = (
            step.get("commute_drive_return_sensor") if drive_sensor else None
        )
        transit_return_sensor = (
            step.get("commute_transit_return_sensor") if transit_sensor else None
        )
        await _async_refresh_sensors(
            hass,
            [drive_sensor, drive_return_sensor, transit_sensor, transit_return_sensor],
            wait_seconds=float(
                step.get("sensor_refresh_timeout") or SENSOR_REFRESH_TIMEOUT
            ),
        )
    else:
        drive_return_sensor = transit_return_sensor = None

    drive_time: int | None = None
    drive_summary = "Driving"
    destination_zone: str | None = None
    distance_str: str | None = None
    traffic_condition = "typical"

    if drive_sensor and hasattr(hass, "states"):
        s_obj = hass.states.get(drive_sensor)
        if s_obj and s_obj.state not in ("unknown", "unavailable", None):
            with contextlib.suppress(ValueError, TypeError):
                drive_time = round(float(s_obj.state))
            attrs = getattr(s_obj, "attributes", {}) or {}
            if isinstance(attrs, dict):
                dest_val = attrs.get("destination")
                if isinstance(dest_val, str):
                    destination_zone = dest_val
                if attrs.get("route"):
                    drive_summary = str(attrs["route"])
                if attrs.get("distance"):
                    distance_str = str(attrs["distance"])
                if attrs.get("traffic_condition"):
                    traffic_condition = str(attrs["traffic_condition"])

    transit_time: int | None = None
    transit_summary = "Metro Transit"
    if transit_sensor and hasattr(hass, "states"):
        s_obj = hass.states.get(transit_sensor)
        if s_obj and s_obj.state not in ("unknown", "unavailable", None):
            with contextlib.suppress(ValueError, TypeError):
                transit_time = round(float(s_obj.state))
            attrs = getattr(s_obj, "attributes", {}) or {}
            if isinstance(attrs, dict):
                if attrs.get("line"):
                    transit_summary = str(attrs["line"])
                elif attrs.get("route"):
                    transit_summary = str(attrs["route"])

    diff_minutes: int | None = None
    if drive_time is not None and transit_time is not None:
        diff_minutes = abs(transit_time - drive_time)
        faster_mode = "driving" if drive_time <= transit_time else "transit"
    elif transit_time is not None and drive_time is None:
        faster_mode = "transit"
    else:
        faster_mode = "driving"

    effective_mode_pref = (
        mode_override or step.get("transportation_mode") or step.get("mode_override")
    )
    if not effective_mode_pref and hasattr(hass, "config_entries"):
        entries = hass.config_entries.async_entries(DOMAIN)
        if entries:
            effective_mode_pref = entries[0].options.get(
                CONF_DEFAULT_TRANSPORTATION_MODE,
                entries[0].data.get(
                    CONF_DEFAULT_TRANSPORTATION_MODE, DEFAULT_TRANSPORTATION_MODE
                ),
            )
    if not effective_mode_pref:
        effective_mode_pref = "fastest"

    effective_mode_pref = str(effective_mode_pref).lower().strip()
    if effective_mode_pref == "transit" and transit_time is not None:
        selected_mode = "transit"
    elif effective_mode_pref == "driving" and drive_time is not None:
        selected_mode = "driving"
    elif effective_mode_pref == "fastest":
        selected_mode = faster_mode
    else:
        selected_mode = "driving" if drive_time is not None else "transit"

    origin_coords = _resolve_commute_origin(hass)
    dest_coords = _resolve_commute_destination(hass, step, destination_zone)

    api_key = await async_resolve_google_maps_api_key(hass) or ""
    output_dir = step.get("output_dir") or (
        hass.data.get(DOMAIN, {}).get("output_dir", DEFAULT_OUTPUT_DIR)
        if hasattr(hass, "data")
        else DEFAULT_OUTPUT_DIR
    )

    map_image_path: str | None = None
    map_info = None
    audio_only_segment = step.get("standalone_segment") and not step.get("visual")
    if audio_only_segment:
        LOGGER.debug("Audio-only segment; skipping the commute map")
    elif origin_coords and dest_coords:
        map_info = await async_fetch_commute_map(
            hass, origin_coords, dest_coords, selected_mode, api_key, output_dir
        )
    else:
        LOGGER.debug("No commute origin/destination configured; using fallback map")
    if map_info:
        if map_info.get("summary") and selected_mode == "driving":
            drive_summary = str(map_info["summary"])
        if map_info.get("distance"):
            distance_str = str(map_info["distance"])
        map_image_path = map_info.get("map_image_path")

    selected_time = drive_time if selected_mode == "driving" else transit_time
    if selected_time is None:
        selected_time = drive_time if drive_time is not None else transit_time
    return_sensor = (
        drive_return_sensor if selected_mode == "driving" else transit_return_sensor
    )
    return_time = _sensor_minutes(hass, return_sensor)
    round_trip_time = (
        selected_time + return_time
        if selected_time is not None and return_time is not None
        else None
    )
    eta_is_relative = bool(step.get("relative_eta"))
    if selected_time is None:
        eta_str = ""
    elif eta_is_relative:
        # Pregenerated media plays hours later, so a clock time would be wrong.
        eta_str = f"{selected_time} min trip"
    else:
        arrival_dt = dt_util.now() + timedelta(minutes=float(selected_time))
        eta_str = arrival_dt.strftime("%I:%M %p").lstrip("0")

    summary_label = drive_summary if selected_mode == "driving" else transit_summary
    if not summary_label.startswith("via ") and summary_label not in (
        "Driving",
        "Transit",
    ):
        route_summary = f"via {summary_label}"
    else:
        route_summary = summary_label

    commute_data = {
        "selected_mode": selected_mode,
        "drive_time": drive_time,
        "transit_time": transit_time,
        "diff_minutes": diff_minutes,
        "faster_mode": faster_mode,
        "drive_summary": drive_summary,
        "transit_summary": transit_summary,
        "summary": route_summary,
        "distance": distance_str or "",
        "eta": eta_str,
        "eta_is_relative": eta_is_relative,
        "selected_time": selected_time,
        "return_time": return_time,
        "round_trip_time": round_trip_time,
        "traffic_condition": traffic_condition,
        "map_image_path": map_image_path,
    }
    step.setdefault("card", {})["commute_data"] = commute_data
    return commute_data


def _format_scripture_sensor(
    hass: HomeAssistant,
    eid: str,
    attrs: dict[str, Any],
) -> str:
    """Format daily scripture sensor and optional companion commentary sensor."""
    lower_attrs = {str(k).lower(): v for k, v in attrs.items()}
    parts = [f"Daily Scripture ({eid}):"]
    if ref := lower_attrs.get("scripture"):
        parts.append(f"Citation: {ref}")
    if txt := (lower_attrs.get("text") or lower_attrs.get("commentary")):
        parts.append(f"Text: {txt}")
    if lower_attrs.get("text") and lower_attrs.get("commentary"):
        parts.append(f"Commentary: {lower_attrs['commentary']}")
    elif not lower_attrs.get("commentary") and eid.endswith("_daily_text_today"):
        comment_obj = hass.states.get(f"{eid}_comment")
        comment_attrs = getattr(comment_obj, "attributes", None) or {}
        comment_text = (
            comment_attrs.get("text") if hasattr(comment_attrs, "get") else None
        )
        if comment_text and comment_text != lower_attrs.get("text"):
            parts.append(f"Commentary: {comment_text}")
    return "\n".join(parts)


def _extract_verbatim_scripture_data(
    hass: HomeAssistant,
    step: dict[str, Any],
) -> tuple[str | None, str | None, str | None]:
    """Extract scripture citation, text, and commentary verbatim from step or hass."""
    # 1. Try entities configured on the step or default daily text sensor
    candidates = _get_step_entity_ids(step)
    if not candidates:
        candidates = ["sensor.jw_library_daily_text_today"]

    for eid in candidates:
        if hasattr(hass, "states") and hasattr(hass.states, "get"):
            state_obj = hass.states.get(eid)
            attrs = getattr(state_obj, "attributes", None) or {}
            lower_attrs = (
                {str(k).lower(): v for k, v in attrs.items()}
                if isinstance(attrs, Mapping)
                else {}
            )
            citation = lower_attrs.get("scripture")
            text = lower_attrs.get("text")
            comment = lower_attrs.get("commentary")
            if not comment and eid.endswith("_daily_text_today"):
                comment_obj = hass.states.get(f"{eid}_comment")
                c_attrs = getattr(comment_obj, "attributes", None) or {}
                if isinstance(c_attrs, Mapping):
                    comment = c_attrs.get("text")
            if citation and text:
                return (
                    str(citation).strip(),
                    str(text).strip(),
                    str(comment).strip() if comment else None,
                )

    # 2. Try parsing from step context if async_extract_entity_context
    # already formatted it
    context = str(step.get("context") or "")
    if "Citation:" in context and "Text:" in context:
        citation, text, comment = None, None, None
        for line in context.splitlines():
            line_str = line.strip()
            if line_str.startswith("Citation:"):
                citation = line_str.removeprefix("Citation:").strip()
            elif line_str.startswith("Text:"):
                text = line_str.removeprefix("Text:").strip()
            elif line_str.startswith("Commentary:"):
                comment = line_str.removeprefix("Commentary:").strip()
        if citation and text:
            return citation, text, comment

    return None, None, None


def _format_single_entity_state(  # noqa: PLR0912
    hass: HomeAssistant,
    eid: str,
    state_obj: Any,
    commute_candidates: list[tuple[str, float, str]],
    *,
    is_commute_step: bool = False,
) -> str:
    """Format a single Home Assistant entity state into a context line."""
    state_val = getattr(state_obj, "state", "unknown")
    attrs = getattr(state_obj, "attributes", {}) or {}
    if not isinstance(attrs, dict):
        attrs = {}

    if eid.startswith("weather."):
        temp = attrs.get("temperature")
        unit = attrs.get("temperature_unit") or "°"
        line_parts = [f"Weather ({eid}): Condition: {state_val}"]
        if temp is not None:
            line_parts.append(f"Temperature: {temp}{unit}")
        if forecast := attrs.get("forecast"):
            if isinstance(forecast, list):
                line_parts.append(f"Forecast: {forecast[:3]}")
            else:
                line_parts.append(f"Forecast: {forecast}")
        return ", ".join(line_parts)

    if eid.startswith("calendar."):
        summary = (
            attrs.get("message") or attrs.get("summary") or attrs.get("description")
        )
        start_time = attrs.get("start_time")
        if state_val == "off" and not summary:
            return (
                f"Calendar ({eid}): No scheduled meetings today. "
                "The user's schedule is completely clear."
            )
        line_parts = [f"Calendar ({eid}): State: {state_val}"]
        if summary:
            line_parts.append(f"Summary: {summary}")
        if start_time:
            line_parts.append(f"Start: {start_time}")
        return ", ".join(line_parts)

    if eid.startswith("todo."):
        return f"To-do ({eid}): {state_val} tasks pending"

    if any(str(k).lower() in ("scripture", "text", "commentary") for k in attrs):
        return _format_scripture_sensor(hass, eid, attrs)

    unit = str(attrs.get("unit_of_measurement", "")).strip()
    suffix = f" {unit}" if unit else ""
    friendly = str(attrs.get("friendly_name", "")).strip()
    base_line = f"Sensor ({eid}): {state_val}{suffix}".strip()
    if friendly:
        base_line = f"{base_line} ({friendly})"

    lower_eid = eid.lower()
    if is_commute_step or "commute" in lower_eid or "travel_time" in lower_eid:
        with contextlib.suppress(ValueError, TypeError):
            num_val = float(str(state_val).strip())
            label = friendly or eid.removeprefix("sensor.").replace("_", " ").title()
            commute_candidates.append((label, num_val, unit or "min"))

    return base_line


def _extract_entity_context(
    hass: HomeAssistant,
    step: dict[str, Any],
    service_overrides: dict[str, str] | None = None,
) -> str:
    """Extract live state context for configured entity IDs from Home Assistant."""
    eids = _get_step_entity_ids(step)
    entity_lines: list[str] = []
    step_name = str(step.get("name", "")).lower()
    step_type = str(step.get("type", "")).lower()
    is_fun_fact = step_name == "fun_fact" or step_type == "fun_fact"
    is_national_day = step_name == "national_day" or step_type == "national_day"
    is_encouragement = "daily_encouragement" in (step_name, step_type)
    is_commute = (
        step_name in ("commute", "traffic", "transit")
        or step_type in ("commute", "traffic", "transit")
        or "commute_sensors" in step
        or "commute_sensor" in step
    )
    has_states_getter = hasattr(hass, "states") and hasattr(hass.states, "get")
    commute_candidates: list[tuple[str, float, str]] = []

    for eid in eids:
        if service_overrides and eid in service_overrides:
            entity_lines.append(service_overrides[eid])
            continue

        if not has_states_getter:
            continue

        state_obj = hass.states.get(eid)
        if state_obj is None:
            continue

        state_val = getattr(state_obj, "state", "unknown")
        if (is_fun_fact or is_national_day or is_encouragement) and str(
            state_val
        ).lower() in (
            "unknown",
            "unavailable",
            "",
        ):
            continue

        entity_lines.append(
            _format_single_entity_state(
                hass,
                eid,
                state_obj,
                commute_candidates,
                is_commute_step=is_commute,
            )
        )

    if comparison_line := _format_commute_comparison(commute_candidates):
        entity_lines.append(comparison_line)

    entity_context_str = "\n".join(entity_lines)
    existing_context = step.get("context")

    if existing_context and str(existing_context).strip() and entity_context_str:
        return f"{str(existing_context).strip()}\n{entity_context_str}"
    if entity_context_str:
        return entity_context_str
    if existing_context and str(existing_context).strip():
        return str(existing_context).strip()
    if is_national_day:
        return "Share today's official National Day celebration and its history."
    if is_fun_fact:
        return "Share an engaging, surprising morning fun fact."
    return ""


async def _async_call_service_for_entity(
    async_call: Any,
    domain: str,
    service: str,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    """Invoke a Home Assistant service and validate dict response output."""
    try:
        res = async_call(
            domain,
            service,
            data,
            blocking=True,
            return_response=True,
        )
        if inspect.isawaitable(res):
            res = await res
        if isinstance(res, dict):
            return res
    except Exception as err:  # noqa: BLE001
        LOGGER.debug("Service %s.%s failed for %s: %s", domain, service, data, err)
    return None


def _extract_forecast_list(res: Any, eid: str) -> list[dict[str, Any]]:
    """Safely extract forecast list from weather.get_forecasts response."""
    if isinstance(res, list):
        return [item for item in res if isinstance(item, dict)]
    if not isinstance(res, dict):
        return []
    target = res.get(eid)
    if isinstance(target, dict):
        fc = target.get("forecast")
        if isinstance(fc, list):
            return [item for item in fc if isinstance(item, dict)]
    elif isinstance(target, list):
        return [item for item in target if isinstance(item, dict)]
    fc = res.get("forecast")
    if isinstance(fc, list):
        return [item for item in fc if isinstance(item, dict)]
    return []


AQI_UNHEALTHY_THRESHOLD = 100
RAIN_PROBABILITY_THRESHOLD = 30
RAIN_CONDITION_KEYWORDS = ("rain", "storm", "pour", "drizzle", "lightning", "shower")
COAT_MAX_F = 50
COAT_HIGH_MAX_F = 55
JACKET_MAX_F = 65
JACKET_LOW_MAX_F = 50
HOT_MIN_F = 85


def _to_float(value: Any) -> float | None:
    """Convert a state or attribute value to float, ignoring non-numeric text."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_weather_advisory(  # noqa: PLR0911, PLR0913
    *,
    condition: str,
    current_temp: Any = None,
    high: Any = None,
    low: Any = None,
    precip_prob: Any = None,
    temperature_unit: str = "°F",
    aqi: Any = None,
) -> str:
    """Return one practical preparation tip for today's weather, or '' if no data."""
    cur = _to_float(current_temp)
    hi = _to_float(high)
    lo = _to_float(low)
    prob = _to_float(precip_prob)
    aqi_val = _to_float(aqi)
    cond = str(condition or "").lower()

    if "c" in str(temperature_unit).lower():
        cur, hi, lo = (None if v is None else v * 9 / 5 + 32 for v in (cur, hi, lo))

    if aqi_val is not None and aqi_val > AQI_UNHEALTHY_THRESHOLD:
        return "Limit outdoor exertion due to poor air quality"
    if (prob is not None and prob >= RAIN_PROBABILITY_THRESHOLD) or any(
        word in cond for word in RAIN_CONDITION_KEYWORDS
    ):
        return "Pack an umbrella for rain today"
    if (cur is not None and cur <= COAT_MAX_F) or (
        hi is not None and hi <= COAT_HIGH_MAX_F
    ):
        return "Wear a warm coat today, it's chilly"
    if (cur is not None and cur <= JACKET_MAX_F) or (
        lo is not None and lo <= JACKET_LOW_MAX_F
    ):
        return "Grab a light jacket or sweater"
    if (cur is not None and cur >= HOT_MIN_F) or (hi is not None and hi >= HOT_MIN_F):
        return "Dress light and stay hydrated, it's going to be hot"
    if cur is None and hi is None and lo is None and prob is None:
        return ""
    return "Comfortable weather expected today"


def _find_outdoor_aqi(hass: HomeAssistant, attrs: dict[str, Any]) -> float | None:
    """Find an outdoor AQI from weather attributes or outdoor air-quality sensors."""
    for key in ("air_quality_index", "aqi"):
        if (val := _to_float(attrs.get(key))) is not None:
            return val
    try:
        states = hass.states.async_all("sensor")
    except (AttributeError, TypeError):
        return None
    if not isinstance(states, (list, tuple)):
        return None
    for state in states:
        eid = str(getattr(state, "entity_id", "")).lower()
        is_outdoor = "airnow" in eid or "waqi" in eid or "outdoor" in eid
        if (
            is_outdoor
            and ("aqi" in eid or "air_quality" in eid)
            and (val := _to_float(getattr(state, "state", None))) is not None
        ):
            return val
    return None


async def async_extract_weather_data(  # noqa: PLR0912, PLR0915
    hass: HomeAssistant,
    eid: str,
) -> dict[str, Any]:
    """Extract live and forecast weather data, querying weather.get_forecasts."""
    state_obj = (
        hass.states.get(eid)
        if hasattr(hass, "states") and hasattr(hass.states, "get")
        else None
    )
    attrs = (getattr(state_obj, "attributes", None) or {}) if state_obj else {}
    condition = getattr(state_obj, "state", "unknown") if state_obj else "unknown"
    current_temp = attrs.get("temperature")
    humidity = attrs.get("humidity")
    wind_speed = attrs.get("wind_speed")
    precip_prob = attrs.get("precipitation_probability")
    location = str(attrs.get("friendly_name") or "")
    temp_unit = (
        attrs.get("temperature_unit")
        or getattr(
            getattr(getattr(hass, "config", None), "units", None),
            "temperature_unit",
            "°F",
        )
        or "°F"
    )

    condition_map = {
        "partlycloudy": "Partly Cloudy",
        "clear-night": "Clear",
        "sunny": "Sunny",
        "cloudy": "Cloudy",
        "rainy": "Rainy",
        "pouring": "Heavy Rain",
        "lightning": "Thunderstorm",
        "lightning-rainy": "Thunderstorm",
        "snowy": "Snow",
        "snowy-rainy": "Sleet",
        "windy": "Windy",
        "fog": "Fog",
        "hail": "Hail",
    }
    condition_text = condition_map.get(
        condition,
        str(condition).replace("-", " ").replace("_", " ").title()
        if condition
        else "Unknown",
    )

    hourly_forecast: list[dict[str, Any]] = []
    daily_forecast: list[dict[str, Any]] = []

    services = getattr(hass, "services", None)
    async_call = getattr(services, "async_call", None) if services else None
    if callable(async_call):
        h_res = await _async_call_service_for_entity(
            async_call,
            "weather",
            "get_forecasts",
            {"entity_id": eid, "type": "hourly"},
        )
        hourly_forecast = _extract_forecast_list(h_res, eid)

        d_res = await _async_call_service_for_entity(
            async_call,
            "weather",
            "get_forecasts",
            {"entity_id": eid, "type": "daily"},
        )
        daily_forecast = _extract_forecast_list(d_res, eid)

    high = None
    low = None
    if daily_forecast:
        today_daily = daily_forecast[0]
        high = today_daily.get("temperature")
        low = today_daily.get("templow")
        if precip_prob is None:
            precip_prob = today_daily.get("precipitation_probability")

    # Min/max aggregation fallback if daily did not provide high/low
    if hourly_forecast:
        hourly_temps = [
            pt["temperature"]
            for pt in hourly_forecast
            if isinstance(pt, dict) and pt.get("temperature") is not None
        ]
        if hourly_temps:
            if high is None:
                high = max(hourly_temps)
            if low is None:
                low = min(hourly_temps)
        if precip_prob is None and "precipitation_probability" in hourly_forecast[0]:
            precip_prob = hourly_forecast[0].get("precipitation_probability")

    # Fallback to state attributes if still None
    if high is None:
        high = attrs.get("temperature_high")
    if low is None:
        low = attrs.get("temperature_low")

    # Build forecast_points (next 5-6 points)
    forecast_points: list[dict[str, Any]] = []
    source_points = hourly_forecast or daily_forecast
    for idx, pt in enumerate(source_points[:6]):
        if not isinstance(pt, dict):
            continue
        p = dict(pt)
        if "time" not in p:
            if "datetime" in p:
                try:
                    dt_val = datetime.fromisoformat(str(p["datetime"]))
                    if dt_val.tzinfo is not None:
                        dt_val = dt_util.as_local(dt_val)
                    if hourly_forecast:
                        p["time"] = dt_val.strftime("%I %p").lstrip("0")
                    elif idx == 0:
                        p["time"] = "Today"
                    elif idx == 1:
                        p["time"] = "Tomorrow"
                    else:
                        p["time"] = dt_val.strftime("%a")
                except (ValueError, TypeError, OSError):
                    p["time"] = str(p["datetime"])
            elif idx == 0:
                p["time"] = "Now" if hourly_forecast else "Today"
        forecast_points.append(p)

    return {
        "current_temp": current_temp,
        "condition": condition,
        "condition_text": condition_text,
        "high": high,
        "low": low,
        "humidity": humidity,
        "wind_speed": wind_speed,
        "precipitation_probability": precip_prob,
        "temperature_unit": temp_unit,
        "location": location,
        "advisory": compute_weather_advisory(
            condition=str(condition),
            current_temp=current_temp,
            high=high,
            low=low,
            precip_prob=precip_prob,
            temperature_unit=str(temp_unit),
            aqi=_find_outdoor_aqi(hass, attrs),
        ),
        "forecast_points": forecast_points,
        "daily_forecast": daily_forecast,
        "hourly_forecast": hourly_forecast,
    }


async def async_extract_entity_context(  # noqa: C901, PLR0912, PLR0915
    hass: HomeAssistant,
    step: dict[str, Any],
) -> str:
    """Asynchronously extract entity context for weather, calendar, and tasks."""
    eids = _get_step_entity_ids(step)
    service_overrides: dict[str, str] = {}

    services = getattr(hass, "services", None)
    async_call = getattr(services, "async_call", None) if services else None
    has_states_getter = hasattr(hass, "states") and hasattr(hass.states, "get")

    for eid in eids:
        if eid.startswith("weather."):
            weather_data = await async_extract_weather_data(hass, eid)
            step.setdefault("card", {})["weather_data"] = weather_data
            if has_states_getter:
                state_obj = hass.states.get(eid)
                forecasts = (
                    weather_data.get("daily_forecast")
                    or weather_data.get("forecast_points")
                    or []
                )
                if forecasts:
                    service_overrides[eid] = _format_weather_service_response(
                        eid, state_obj, forecasts
                    )

    step_name = str(step.get("name", "")).lower()
    step_type = str(step.get("type", "")).lower()
    is_agenda_step = (
        any(eid.startswith(("calendar.", "todo.")) for eid in eids)
        or step_name in ("agenda", "calendar")
        or step_type in ("agenda", "calendar")
    )
    agenda_events: list[dict[str, str]] = []
    event_starts: list[str] = []
    agenda_tasks: list[str] = []

    if callable(async_call):
        now = dt_util.now()
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end_dt = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()

        for eid in eids:
            if eid.startswith("calendar."):
                res = await _async_call_service_for_entity(
                    async_call,
                    "calendar",
                    "get_events",
                    {
                        "entity_id": eid,
                        "start_date_time": start_dt,
                        "end_date_time": end_dt,
                    },
                )
                if res and isinstance(res.get(eid), dict):
                    events = res[eid].get("events")
                    if isinstance(events, list):
                        service_overrides[eid] = _format_calendar_service_response(
                            eid, events, _calendar_name(hass, eid)
                        )
                        for ev in events:
                            if not isinstance(ev, dict):
                                continue
                            ev_time = str(ev.get("time") or "")
                            if not ev_time:
                                ev_time = _format_event_time_range(
                                    ev.get("start"), ev.get("end")
                                )
                            ev_title = str(
                                ev.get("title")
                                or ev.get("summary")
                                or ev.get("message")
                                or ev.get("description")
                                or "Scheduled Event"
                            ).strip()
                            ev_loc = str(ev.get("location") or "").strip()
                            agenda_events.append(
                                {
                                    "time": ev_time,
                                    "title": ev_title,
                                    "location": ev_loc,
                                }
                            )
                            event_starts.append(_event_start_key(ev.get("start")))

            elif eid.startswith("todo."):
                res = await _async_call_service_for_entity(
                    async_call,
                    "todo",
                    "get_items",
                    {"entity_id": eid, "status": ["needs_action"]},
                )
                if res and isinstance(res.get(eid), dict):
                    items = res[eid].get("items")
                    if isinstance(items, list):
                        service_overrides[eid] = _format_todo_service_response(
                            eid, items
                        )
                        for item in items:
                            if isinstance(item, dict):
                                summary = (
                                    item.get("summary")
                                    or item.get("description")
                                    or item.get("title")
                                )
                                if summary and str(summary).strip():
                                    agenda_tasks.append(str(summary).strip())
                            elif isinstance(item, str) and item.strip():
                                agenda_tasks.append(item.strip())

    if is_agenda_step:
        now_dt = dt_util.now()
        try:
            date_str = now_dt.strftime("%A, %b %-d")
        except (ValueError, TypeError):
            date_str = f"{now_dt.strftime('%A, %b')} {now_dt.day}"

        step.setdefault("card", {})["agenda_data"] = {
            "date_str": date_str,
            "calendar_events": [
                event
                for _, event in sorted(
                    zip(event_starts, agenda_events, strict=True),
                    key=lambda pair: pair[0],
                )
            ],
            "todo_items": agenda_tasks,
        }

    is_commute_step = (
        step_name in ("commute", "traffic", "transit")
        or step_type in ("commute", "traffic", "transit")
        or "commute_sensors" in step
        or "commute_sensor" in step
    )
    commute_line = ""
    if is_commute_step:
        mode_override = step.get("transportation_mode") or step.get("mode_override")
        commute_data = await async_extract_commute_data(
            hass, step, mode_override=mode_override
        )
        commute_line = _format_commute_narration_hint(commute_data)

    context = _extract_entity_context(hass, step, service_overrides=service_overrides)
    if "daily_encouragement" in (step_name, step_type) and not context.strip():
        context = NO_DAILY_TEXT_CONTEXT
    advisory = (step.get("card") or {}).get("weather_data", {}).get("advisory")
    if advisory:
        context = (
            f"{context}\nWeather Advisory (tell the listener): {advisory}"
        ).strip()
    if commute_line:
        context = f"{context}\n{commute_line}".strip()
    return context


def _format_commute_narration_hint(commute_data: dict[str, Any]) -> str:
    """
    Describe the HUD's selected commute so the narration matches the slide.

    Pregenerated media (``eta_is_relative``) plays hours after compilation,
    so it gets a relative trip length and an explicit ban on clock times.
    """
    minutes = commute_data.get("selected_time")
    if minutes is None:
        return ""
    mode = "transit" if commute_data.get("selected_mode") == "transit" else "driving"
    route = str(commute_data.get("summary") or "").strip()
    route_part = f" {route}" if route.startswith("via ") else ""
    home = _format_trip_home_hint(commute_data)
    if commute_data.get("eta_is_relative"):
        return (
            f"Commute (tell the listener): {mode}{route_part}, "
            f"about {minutes} minutes.{home} Do not mention a clock arrival time."
        )
    eta = str(commute_data.get("eta") or "").strip()
    eta_part = f", arriving around {eta}" if eta else ""
    return (
        f"Commute (tell the listener): {mode}{route_part}, "
        f"{minutes} minutes{eta_part}.{home}"
    )


def _spoken_duration(minutes: int) -> str:
    """Say a trip length the way a person would: '1 hour 23 minutes'."""
    hours, mins = divmod(int(minutes), 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins or not hours:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    return " ".join(parts)


def _format_trip_home_hint(commute_data: dict[str, Any]) -> str:
    """Describe the evening trip home and round trip, when a sensor gives one."""
    home = commute_data.get("return_time")
    total = commute_data.get("round_trip_time")
    if home is None or total is None:
        return ""
    return (
        f" Heading home this evening, about {_spoken_duration(home)}, "
        f"so plan on about {_spoken_duration(total)} round trip."
    )


NO_DAILY_TEXT_CONTEXT = (
    "No daily scripture or daily text is available today. Give a short, general "
    "encouraging remark for having a good day."
)


def _is_unconfigured_optional_step(hass: HomeAssistant, step: dict[str, Any]) -> bool:
    """Check if an entity step is unconfigured or missing with no fallback."""
    step_name = str(step.get("name", "")).lower()
    step_type = str(step.get("type", "")).lower()
    always_run = ("fun_fact", "national_day", "daily_encouragement")
    if step_name in always_run or step_type in always_run:
        return False

    has_entity_key = (
        "entity_id" in step
        or "entity_ids" in step
        or "commute_sensor" in step
        or "commute_sensors" in step
        or "calendar_entity" in step
        or "calendar_entities" in step
        or "todo_entity" in step
        or "todo_entities" in step
    )
    if not has_entity_key:
        return False

    eids = _get_step_entity_ids(step)
    has_valid_state = False
    if hasattr(hass, "states") and hasattr(hass.states, "get"):
        has_valid_state = any(hass.states.get(eid) is not None for eid in eids)

    if not has_valid_state:
        has_context = bool(step.get("context") and str(step.get("context")).strip())
        has_actions = bool(step.get("actions") or step.get("actions_on_display"))
        if not has_context and not has_actions:
            return True

    return False


def get_audio_duration(
    audio_path: str,
    fallback_text: str | None = None,
) -> float:
    """
    Determine audio duration in seconds from WAV header or estimate from text.

    Args:
        audio_path: Path to audio file.
        fallback_text: Narration script for estimating duration if file is unavailable.

    Returns:
        Duration in seconds as a float rounded to 2 decimal places.

    """
    path = Path(audio_path)
    if path.is_file():
        try:
            with wave.open(str(path), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate > 0:
                    return round(frames / float(rate), 2)
        except (wave.Error, OSError, EOFError) as err:
            LOGGER.debug("Could not parse audio duration from %s: %s", audio_path, err)

    if fallback_text:
        words = len(fallback_text.split())
        return round(max(3.0, words / 2.5), 2)

    return 5.0


def _detect_step_weather_condition(
    hass: HomeAssistant,
    step: dict[str, Any],
) -> str | None:
    """Return weather state condition if step references a weather entity."""
    if not (hasattr(hass, "states") and hasattr(hass.states, "get")):
        return None
    for eid in _get_step_entity_ids(step):
        if eid.startswith("weather."):
            state_obj = hass.states.get(eid)
            state_val = getattr(state_obj, "state", None)
            if isinstance(state_val, str) and state_val not in (
                "unknown",
                "unavailable",
                "",
            ):
                return state_val
    return None


async def _async_resolve_step_visual_assets(  # noqa: PLR0912, PLR0913, PLR0915
    hass: HomeAssistant,
    step_to_process: dict[str, Any],
    step_name: str,
    card_data: dict[str, Any],
    ai_creds: dict[str, Any],
    scratch_dir: str | None = None,
    narration: str = "",
) -> None:
    """Resolve dynamic and AI-generated visual background assets for a visual slide."""
    is_dynamic = is_step_image_dynamic(step_to_process, step_name)

    if card_data.get("image_path"):
        card_data.setdefault("image_type", "dynamic" if is_dynamic else "static")
        card_data.setdefault("dynamic_image", is_dynamic)
        card_data.setdefault("layout", "photo")
        return

    step_key = str(step_to_process.get("type") or step_name or "").strip().lower()
    name_key = str(step_name or "").strip().lower()

    # Determine fallback static asset and prompt per slide theme
    if step_key in ("greeting", "welcome") or name_key in ("greeting", "welcome"):
        fallback_asset = "greeting_coffee.jpg"
        dynamic_prompt = (
            "A peaceful morning scene with a warm ceramic mug of fresh coffee "
            "on a wooden table, soft golden morning sunrise light streaming "
            "through a window, cozy atmosphere, photorealistic cinematic 16:9"
        )
    elif step_key in ("national_day", "holiday", "observance") or name_key in (
        "national_day",
        "holiday",
        "observance",
    ):
        fallback_asset = "national_day.jpg"
        topic = (
            card_data.get("subtitle")
            or card_data.get("title")
            or step_to_process.get("context")
            or "National Day celebration"
        )
        dynamic_prompt = (
            f"A vibrant, festive cinematic photograph celebrating {topic}, "
            "holiday atmosphere, joyful, detailed, beautiful natural lighting, "
            "16:9 widescreen aspect ratio"
        )
    elif step_key in ("fun_fact", "trivia") or name_key in ("fun_fact", "trivia"):
        fallback_asset = "fun_fact.jpg"
        bullets = card_data.get("bullet_points") or []
        fact_parts = [
            str(card_data.get("title") or ""),
            *(str(b) for b in bullets if isinstance(bullets, list)),
        ]
        fact_text = " ".join(p.strip() for p in fact_parts if p.strip())
        if not fact_text:
            fact_text = (
                str(narration).strip()[:300]
                or str(card_data.get("subtitle") or "")
                or str(step_to_process.get("context") or "")
                or "morning fun fact"
            )
        dynamic_prompt = (
            f"A cinematic photograph that visually illustrates this specific fact: "
            f"{fact_text}. Show the actual subject of the fact, "
            "detailed, beautiful natural lighting, 16:9 widescreen aspect ratio, "
            "no text, no words, no typography"
        )
    elif step_key in ("daily_encouragement", "encouragement") or name_key in (
        "daily_encouragement",
        "encouragement",
    ):
        fallback_asset = "greeting_rooster.jpg"
        dynamic_prompt = (
            "A serene, uplifting cinematic photograph of a calm sunrise over a "
            "peaceful path through open countryside, warm golden light, hopeful "
            "fresh start to a good day, 16:9 widescreen aspect ratio, "
            "no text, no words, no typography"
        )
    elif step_key == "weather" or name_key == "weather":
        cond = str(
            card_data.get("weather_condition")
            or _detect_step_weather_condition(hass, step_to_process)
            or ""
        ).lower()
        if any(
            w in cond for w in ("rain", "pour", "drizzle", "storm", "thunder", "shower")
        ):
            fallback_asset = "weather_rainy.jpg"
            sky_desc = "dramatic rainy skies with visible rainfall"
        elif any(
            w in cond for w in ("snow", "sleet", "hail", "ice", "blizzard", "winter")
        ):
            fallback_asset = "weather_snow.jpg"
            sky_desc = "wintry overcast sky with gentle falling snow"
        elif any(
            w in cond for w in ("cloud", "overcast", "fog", "mist", "wind", "haze")
        ):
            fallback_asset = "weather_cloudy.jpg"
            sky_desc = "dramatic partly cloudy atmospheric morning skies"
        else:
            fallback_asset = "weather_sunny.jpg"
            sky_desc = "bright clear sunny blue morning skies with soft golden sunlight"

        weather_summary = (
            card_data.get("subtitle")
            or card_data.get("title")
            or cond
            or "clear morning"
        )
        bullets = card_data.get("bullet_points") or []
        bullet_text = (
            ", ".join(str(b) for b in bullets) if isinstance(bullets, list) else ""
        )
        weather_details = f"{weather_summary}. {bullet_text}".strip(". ")

        dynamic_prompt = (
            "A professional television weather broadcast background graphic, "
            "modern Weather Channel style visual outlook, "
            "photorealistic depiction of current weather conditions: "
            f"{sky_desc}, {weather_details}. "
            "Atmospheric skies matching real-time weather conditions, "
            "modern broadcast studio digital weather backdrop, "
            "cinematic studio lighting, 16:9 widescreen aspect ratio, "
            "ultra-high resolution, no text, no words, no numbers, no typography"
        )
    elif step_key in (
        "agenda",
        "calendar",
        "schedule",
        "todo",
        "tasks",
    ) or name_key in (
        "agenda",
        "calendar",
        "schedule",
        "todo",
        "tasks",
    ):
        fallback_asset = "calendar_agenda.jpg"
        cal_items = card_data.get("calendar_items") or []
        todo_items = card_data.get("todo_items") or []
        cal_summary = (
            f"{len(cal_items)} calendar events" if cal_items else "clear schedule"
        )
        todo_summary = (
            f"{len(todo_items)} pending tasks" if todo_items else "tasks completed"
        )
        dynamic_prompt = (
            "A sleek modern digital calendar and schedule visual background, "
            f"illustrating a structured morning schedule with {cal_summary} "
            f"and {todo_summary}, aesthetic modern minimalist office desk "
            "with a live calendar and planner layout, warm ambient morning lighting, "
            "high contrast, elegant dark slate theme, 16:9 widescreen, "
            "clean cinematic composition, no gibberish text"
        )
    elif step_key in ("commute", "traffic", "transit") or name_key in (
        "commute",
        "traffic",
        "transit",
    ):
        fallback_asset = "commute_traffic.jpg"
        dynamic_prompt = (
            "An aerial cinematic 16:9 widescreen view of morning highway traffic "
            "and city skyline bathed in soft golden morning sunlight, "
            "clear commute route"
        )
    elif step_key in ("closing", "all_heard"):
        fallback_asset = "closing_good_day.jpg"
        dynamic_prompt = _closing_image_prompt(dt_util.now())
    elif step_key in ("scripture", "bible", "daily_text") or name_key in (
        "scripture",
        "bible",
        "daily_text",
    ):
        fallback_asset = "bible_scripture.jpg"
        dynamic_prompt = (
            "A peaceful morning Bible study scene with an open leather Bible on a "
            "warm wooden table, soft morning window light, cinematic 16:9 widescreen"
        )
    else:
        fallback_asset = "weather_sunny.jpg"
        dynamic_prompt = str(
            card_data.get("image_prompt")
            or f"A beautiful cinematic morning photograph for {step_name}, 16:9"
        )

    if not is_dynamic:
        card_data["image_path"] = fallback_asset
        card_data["image_type"] = "static"
        card_data["dynamic_image"] = False
        card_data.setdefault("layout", "photo")
        return

    ai_img_path = (
        str(Path(scratch_dir) / f"bg_{step_name}.png") if scratch_dir else None
    )
    if ai_img_path and ai_creds.get("platform") == "gemini" and ai_creds.get("api_key"):
        try:
            generated = await async_generate_ai_image(
                hass, dynamic_prompt, ai_img_path, ai_creds=ai_creds
            )
            if generated and Path(generated).is_file():
                card_data["image_path"] = generated
                card_data["image_type"] = "dynamic"
                card_data["dynamic_image"] = True
                card_data.setdefault("layout", "photo")
                return
        except Exception as err:  # noqa: BLE001
            LOGGER.warning(
                "Dynamic AI image generation failed for step '%s': %s",
                step_name,
                err,
            )

    # Fallback to static asset if dynamic generation fails or credentials unavailable
    card_data["image_path"] = fallback_asset
    card_data["image_type"] = "static"
    card_data["dynamic_image"] = False
    card_data.setdefault("layout", "photo")
    LOGGER.info(
        "Using static fallback asset '%s' for step '%s'",
        fallback_asset,
        step_name,
    )


# A sentence that is *entirely* a generic send-off, e.g. "Have a wonderful
# day!", "I hope you have a great day, Alex!" or "Have a blessed day ahead."
_GENERIC_SENDOFF_SENTENCE_RE = re.compile(
    r"^(?:(?:i\s+)?hope\s+you\s+|may\s+you\s+|and\s+)?"
    r"have\s+an?\s+(?:\w+\s+){0,2}day(?:\s+(?:today|ahead))?"
    r"(?:\s*,\s*[\w' .-]{1,40})?\s*[.!?]*$",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _signoff_line(name: str) -> str:
    """Return the fixed sign-off, 'Have a great day, <name>!'."""
    name = name.strip()
    return f"Have a great day, {name}!" if name else "Have a great day!"


def _ensure_encouragement_signoff(narration: str, recipient: dict[str, Any]) -> str:
    """
    Ensure narration ends with 'Have a great day, <name>!'.

    Only a final sentence that is wholly a generic send-off is replaced; any
    other ending is kept intact and the sign-off is appended as a new
    sentence, so a sentence is never cut mid-way.
    """
    name = str(recipient.get("name") or "").strip()
    signoff = _signoff_line(str(recipient.get("phonetic_name") or name))
    text = narration.strip()
    if text.lower().endswith(signoff.lower()):
        return text
    sentences = [p for p in _SENTENCE_SPLIT_RE.split(text) if p.strip()]
    if sentences and _GENERIC_SENDOFF_SENTENCE_RE.match(sentences[-1].strip()):
        sentences.pop()
    text = " ".join(sentences).strip()
    if text and text[-1] not in ".!?":
        text += "."
    return f"{text} {signoff}".strip()


def _respell_for_speech(
    hass: HomeAssistant, narration: str, recipient: dict[str, Any]
) -> str:
    """
    Apply the pronunciation lexicon to narration, for the speech engine only.

    The listener is the person whose name matches the recipient's name. With
    no match only household phonetic names apply. Card text never goes through here.
    """
    if (entry := get_loaded_entry(hass)) is None:
        return narration
    people = get_people(entry)
    name = str(recipient.get("name") or "").strip()
    listener = find_person(entry, name) if name else None
    return apply_pronunciations(narration, build_lexicon(people, listener))


async def _async_compile_single_step(  # noqa: C901, PLR0912, PLR0913, PLR0915
    hass: HomeAssistant,
    step_to_process: dict[str, Any],
    step_name: str,
    recipient: dict[str, Any],
    ai_creds: dict[str, Any],
    *,
    is_visual: bool,
    default_pad_seconds: float,
    audio_target_path: str,
    slide_target_path: str | None = None,
    clip_target_path: str | None = None,
) -> tuple[str, float, float]:
    """Compile a single routine or static step into audio and (if visual) MP4 clip."""
    step_key = str(step_to_process.get("type") or step_name or "").strip().lower()
    name_key = str(step_name or "").strip().lower()
    is_scripture = step_key in ("scripture", "daily_text", "bible") or name_key in (
        "scripture",
        "daily_text",
        "bible",
    )
    is_first = step_to_process.get("is_first_step")
    is_closing = step_to_process.get("type") == "closing"
    is_encouragement = step_key in ("daily_encouragement", "encouragement")
    wants_signoff = is_encouragement and (
        step_to_process.get("include_signoff", True) is not False
    )

    explicit_narration = (
        step_to_process.get("narration")
        or step_to_process.get("message")
        or step_to_process.get("text")
        or step_to_process.get("alert_message")
    )
    if is_closing:
        # New-engine closing segment: fixed copy, no LLM (spec §5.2).
        real_name = str(recipient.get("name") or "")
        spoken_name = str(recipient.get("phonetic_name") or real_name)
        content = {
            "visual_card": {
                "title": _signoff_line(real_name),
                "subtitle": step_to_process.get("subtitle")
                or closing_subtitle(dt_util.now()),
                "bullet_points": [],
            },
            "narration_script": _signoff_line(spoken_name),
        }
    elif explicit_narration and str(explicit_narration).strip():
        raw_content = {
            "visual_card": {
                "title": step_to_process.get(
                    "title",
                    step_to_process.get(
                        "alert_title", step_name.replace("_", " ").title()
                    ),
                ),
                "subtitle": step_to_process.get("subtitle", ""),
                "bullet_points": step_to_process.get("bullet_points", []),
            },
            "narration_script": str(explicit_narration).strip(),
        }
        content = _enforce_recipient_separation(
            raw_content, recipient, is_first=is_first
        )
    elif (
        is_scripture
        and (scripture_data := _extract_verbatim_scripture_data(hass, step_to_process))[
            0
        ]
    ):
        citation, text, comment = scripture_data
        if comment:
            verbatim_narration = (
                f"Today's text is from {citation}. It reads: {text}.\n{comment}"
            )
        else:
            verbatim_narration = f"Today's text is from {citation}. It reads: {text}."
        raw_content = {
            "visual_card": {
                "title": "Daily Scripture",
                "subtitle": str(citation),
                "bullet_points": [f'"{text}"'],
            },
            "narration_script": verbatim_narration.strip(),
        }
        content = _enforce_recipient_separation(
            raw_content, recipient, is_first=is_first
        )
    else:
        content = await async_generate_step_content(
            hass, step_to_process, recipient, ai_creds
        )

    card_data = dict(content.get("visual_card", {}))
    if "card" in step_to_process and isinstance(step_to_process["card"], dict):
        for c_key, c_val in step_to_process["card"].items():
            card_data.setdefault(c_key, c_val)
        if "weather_data" in step_to_process["card"]:
            card_data["weather_data"] = step_to_process["card"]["weather_data"]
        if "agenda_data" in step_to_process["card"]:
            card_data["agenda_data"] = step_to_process["card"]["agenda_data"]
        if "commute_data" in step_to_process["card"]:
            card_data["commute_data"] = step_to_process["card"]["commute_data"]
    for key in (
        "image_path",
        "icon_path",
        "icon",
        "layout",
        "avatar_fallback",
        "theme",
        "badge",
    ):
        if key in step_to_process:
            card_data[key] = step_to_process[key]
    if "image_path" not in card_data and "image_asset" in step_to_process:
        card_data["image_path"] = step_to_process["image_asset"]
    if not card_data.get("title"):
        if step_to_process.get("title"):
            card_data["title"] = step_to_process["title"]
        elif step_to_process.get("alert_title"):
            card_data["title"] = step_to_process["alert_title"]
        else:
            card_data.setdefault("title", step_name.replace("_", " ").title())
    if not card_data.get("subtitle") and step_to_process.get("subtitle"):
        card_data["subtitle"] = step_to_process["subtitle"]

    card_data.setdefault("step_name", step_name)
    if wants_signoff:
        card_data["host_signoff"] = _signoff_line(str(recipient.get("name") or ""))
    if weather_cond := _detect_step_weather_condition(hass, step_to_process):
        card_data.setdefault("weather_condition", weather_cond)

    if is_visual:
        scratch_dir = str(Path(slide_target_path).parent) if slide_target_path else None
        await _async_resolve_step_visual_assets(
            hass,
            step_to_process,
            step_name,
            card_data,
            ai_creds,
            scratch_dir=scratch_dir,
            narration=str(content.get("narration_script", "")),
        )

    narration_script = content.get("narration_script", "")
    if wants_signoff:
        narration_script = _ensure_encouragement_signoff(
            str(narration_script), recipient
        )
    if not narration_script:
        fallback_msg = (
            step_to_process.get("context")
            or card_data.get("title")
            or step_name.replace("_", " ")
        )
        narration_script = str(fallback_msg)

    voice = step_to_process.get("voice", DEFAULT_HOST_VOICE)
    personality = (
        step_to_process.get("voice_personality")
        or step_to_process.get("personality")
        or "news_anchor"
    )

    synth_audio_path = await async_synthesize_speech(
        hass,
        _respell_for_speech(hass, str(narration_script), recipient),
        voice,
        personality,
        audio_target_path,
        creds=ai_creds,
        voice_profile=step_to_process.get("voice_profile"),
        openai_voice=step_to_process.get("openai_voice"),
    )
    effective_audio_path = synth_audio_path or audio_target_path

    step_duration_override = step_to_process.get("duration")
    pad_seconds = 0.0
    if step_duration_override is not None:
        if is_visual:
            pad_seconds = float(step_to_process.get("pad_seconds", 0.0))
        duration = float(step_duration_override)
    else:
        raw_duration = await _async_run_in_executor(
            hass,
            get_audio_duration,
            effective_audio_path,
            fallback_text=narration_script,
        )
        if is_visual:
            pad_seconds = float(step_to_process.get("pad_seconds", default_pad_seconds))
            duration = round(raw_duration + pad_seconds, 2)
        else:
            duration = raw_duration

    # Without a clip target only the slide is rendered; the caller encodes the
    # clip itself (new-engine segments use one uniform encode).
    if is_visual and slide_target_path:
        phonetic = step_to_process.get("phonetic_name") or recipient.get(
            "phonetic_name"
        )
        slide_img_path = await _async_run_in_executor(
            hass,
            render_slide,
            card_data,
            slide_target_path,
            phonetic,
        )
        if clip_target_path:
            clip_cmd = build_slide_clip_command(
                slide_img_path or slide_target_path,
                effective_audio_path,
                clip_target_path,
                duration=duration,
                pad_seconds=pad_seconds,
            )
            clip_ok = await async_run_ffmpeg(hass, clip_cmd)
            if not clip_ok:
                msg = f"FFmpeg clip compilation failed for step '{step_name}'"
                raise GeneratorError(msg)

    image_info = {
        "image_type": card_data.get("image_type", "static"),
        "dynamic_image": card_data.get("dynamic_image", False),
        "image_path": card_data.get("image_path"),
    }

    return effective_audio_path, duration, pad_seconds, image_info
