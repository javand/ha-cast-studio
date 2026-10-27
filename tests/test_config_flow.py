"""Tests for the CastStudio config and options flow."""

from __future__ import annotations

import json
from datetime import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant import config_entries, data_entry_flow
from pytest_homeassistant_custom_component.common import MockConfigEntry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from custom_components.cast_studio.config_flow import (
    CastStudioFlowHandler,
    CastStudioOptionsFlowHandler,
)
from custom_components.cast_studio.const import (
    CONF_API_KEY,
    CONF_COMMUTE_DESTINATION,
    CONF_DEFAULT_TRANSPORTATION_MODE,
    CONF_MAPS_API_KEY,
    CONF_OUTPUT_DIR,
    CONF_PHONETIC_NAME,
    CONF_RECIPIENT_NAME,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PHONETIC_NAME,
    DEFAULT_RECIPIENT_NAME,
    DOMAIN,
)
from custom_components.cast_studio.people import (
    SEGMENT_TYPES,
    find_routine,
    get_people,
    get_routines,
)
from custom_components.cast_studio.pronunciation import (
    apply_pronunciations,
    build_lexicon,
)

from .conftest import ALEX_ID, SAM_ID


@pytest.mark.asyncio
async def test_config_flow_user_step_shows_form():
    """Test initial user step presents form with expected fields."""
    flow = CastStudioFlowHandler()
    flow.hass = MagicMock()
    # Spec §5.1: the step now checks for an existing entry before the form.
    flow.hass.config_entries.async_entry_for_domain_unique_id.return_value = None
    flow.context = {}

    result = await flow.async_step_user(user_input=None)

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


@pytest.mark.asyncio
async def test_config_flow_user_step_creates_entry():
    """Spec §5.1: shared settings go in data; the first person is a subentry."""
    flow = CastStudioFlowHandler()
    flow.hass = MagicMock()
    flow.hass.config_entries.async_entry_for_domain_unique_id.return_value = None
    flow.context = {}

    user_input: dict[str, Any] = {
        CONF_API_KEY: "test_gemini_key",
        CONF_RECIPIENT_NAME: "Alice",
        CONF_PHONETIC_NAME: "Al-iss",
        CONF_OUTPUT_DIR: "/config/www/briefings",
    }

    result = await flow.async_step_user(user_input=user_input)

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "CastStudio"
    assert result["data"] == {
        CONF_API_KEY: "test_gemini_key",
        CONF_OUTPUT_DIR: "/config/www/briefings",
    }
    (person,) = result["subentries"]
    assert person["subentry_type"] == "person"
    assert person["title"] == "Alice"
    assert person["data"] == {
        "name": "Alice",
        "phonetic_name": "Al-iss",
        "media_player": None,
        "commute_destination_zone": None,
        "transportation_mode": "fastest",
        "reset_time": "03:00:00",
        "max_age_minutes": 0,
    }


@pytest.mark.asyncio
async def test_config_flow_aborts_if_already_configured():
    """Spec §5.1: a second instance aborts and points to Add person."""
    flow = CastStudioFlowHandler()
    flow.hass = MagicMock()
    existing_entry = MagicMock()
    existing_entry.source = config_entries.SOURCE_USER
    existing_entry.state = config_entries.ConfigEntryState.LOADED
    flow.hass.config_entries.async_entry_for_domain_unique_id.return_value = (
        existing_entry
    )
    flow.context = {}

    user_input: dict[str, Any] = {
        CONF_RECIPIENT_NAME: DEFAULT_RECIPIENT_NAME,
        CONF_PHONETIC_NAME: DEFAULT_PHONETIC_NAME,
        CONF_OUTPUT_DIR: DEFAULT_OUTPUT_DIR,
    }

    with pytest.raises(data_entry_flow.AbortFlow) as exc_info:
        await flow.async_step_user(user_input=user_input)

    assert exc_info.value.reason == "single_instance_use_add_person"


def test_async_get_options_flow():
    """Verify flow handler returns an OptionsFlowWithReload instance."""
    mock_entry = MagicMock()
    handler = CastStudioFlowHandler.async_get_options_flow(mock_entry)
    assert isinstance(handler, CastStudioOptionsFlowHandler)
    assert isinstance(handler, config_entries.OptionsFlowWithReload)


@pytest.mark.asyncio
async def test_options_flow_init_step_shows_form_default_data(
    hass: HomeAssistant,
) -> None:
    """Test options flow displays current config entry values from data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="test_cast_studio",
        data={
            CONF_API_KEY: "original_key",
            CONF_RECIPIENT_NAME: "Alex",
            CONF_PHONETIC_NAME: "Al-ex",
            CONF_OUTPUT_DIR: "/config/www/cast_studio",
        },
        options={},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["data_schema"] is not None


@pytest.mark.asyncio
async def test_options_flow_init_step_shows_form_default_options(
    hass: HomeAssistant,
) -> None:
    """Test options flow prioritizing options over data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="test_cast_studio",
        data={
            CONF_RECIPIENT_NAME: "Alex",
            CONF_PHONETIC_NAME: "Al-ex",
            CONF_OUTPUT_DIR: "/config/www/cast_studio",
        },
        options={
            CONF_RECIPIENT_NAME: "Updated Alex",
            CONF_PHONETIC_NAME: "Al-ex",
            CONF_OUTPUT_DIR: "/config/www/cast_studio_updated",
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "init"


@pytest.mark.asyncio
async def test_options_flow_updates_options_and_reloads(
    hass: HomeAssistant,
) -> None:
    """Test submitting new options updates entry and triggers async_reload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="test_cast_studio",
        data={
            CONF_RECIPIENT_NAME: "Alex",
            CONF_PHONETIC_NAME: "Al-ex",
            CONF_OUTPUT_DIR: "/config/www/cast_studio",
        },
        options={},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == data_entry_flow.FlowResultType.FORM

    # Spec §5.1: the options flow holds only shared settings now.
    updated_input = {
        CONF_API_KEY: "new_api_key",
        CONF_OUTPUT_DIR: "/config/www/custom_dir",
    }

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_reload", AsyncMock()
    ) as mock_reload:
        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input=updated_input,
        )
        assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result2["data"] == updated_input
        assert entry.options == updated_input
        mock_reload.assert_awaited_once_with(entry.entry_id)


def test_translations_en_structure() -> None:
    """Test translations en.json schema."""
    en_path = (
        Path(__file__).parent.parent
        / "custom_components"
        / "cast_studio"
        / "translations"
        / "en.json"
    )
    with en_path.open(encoding="utf-8") as f:
        data = json.load(f)

    assert "config" in data
    assert "step" in data["config"]
    assert "user" in data["config"]["step"]
    user_step = data["config"]["step"]["user"]
    assert user_step["title"] == "CastStudio"
    assert "description" in user_step
    assert "recipient_name" in user_step["data"]

    assert "abort" in data["config"]
    assert "single_instance_use_add_person" in data["config"]["abort"]

    assert "options" in data
    assert "step" in data["options"]
    assert "init" in data["options"]["step"]
    init_step = data["options"]["step"]["init"]
    assert init_step["title"] == "CastStudio Options"
    # Spec §5.1: per-person fields moved from the options form to the person.
    for key in ("recipient_name", "phonetic_name", "commute_destination_zone"):
        assert key not in init_step["data"]


@pytest.mark.asyncio
async def test_config_flow_maps_api_key_and_transport_mode():
    """Test user step and options flow accept maps_api_key and default_transportation_mode."""
    flow = CastStudioFlowHandler()
    flow.hass = MagicMock()
    flow.hass.config_entries.async_entry_for_domain_unique_id.return_value = None
    flow.context = {}

    user_input = {
        CONF_API_KEY: "test_gemini_key",
        CONF_MAPS_API_KEY: "test_maps_key",
        CONF_DEFAULT_TRANSPORTATION_MODE: "transit",
        CONF_RECIPIENT_NAME: "Alice",
        CONF_PHONETIC_NAME: "Al-iss",
        CONF_OUTPUT_DIR: "/config/www/briefings",
    }

    result = await flow.async_step_user(user_input=user_input)
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MAPS_API_KEY] == "test_maps_key"
    assert result["data"][CONF_DEFAULT_TRANSPORTATION_MODE] == "transit"


@pytest.mark.asyncio
async def test_commute_destination_zone_in_user_and_options_flows(
    hass: HomeAssistant,
) -> None:
    """The user step offers the zone selector; options no longer do (§5.1)."""
    flow = CastStudioFlowHandler()
    flow.hass = hass
    flow.context = {}
    result = await flow.async_step_user()
    user_keys = {str(k): k for k in result["data_schema"].schema}
    assert CONF_COMMUTE_DESTINATION in user_keys
    sel = result["data_schema"].schema[user_keys[CONF_COMMUTE_DESTINATION]]
    assert sel.config["domain"] == ["zone"]

    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="test_cast_studio",
        data={CONF_RECIPIENT_NAME: "Alice", CONF_OUTPUT_DIR: "/tmp/out"},  # noqa: S108
        options={CONF_COMMUTE_DESTINATION: "zone.fake_office"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    option_keys = {str(k) for k in result["data_schema"].schema}
    assert option_keys == {
        CONF_API_KEY,
        CONF_MAPS_API_KEY,
        CONF_DEFAULT_TRANSPORTATION_MODE,
        CONF_OUTPUT_DIR,
    }


def test_commute_destination_zone_has_translations() -> None:
    """strings.json and en.json label the destination field and mode options."""
    base = Path(__file__).parent.parent / "custom_components" / "cast_studio"
    for path in (base / "strings.json", base / "translations" / "en.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert CONF_COMMUTE_DESTINATION in data["config"]["step"]["user"]["data"]
        # Spec §5.1: the destination is per person, so not in options.
        assert CONF_COMMUTE_DESTINATION not in data["options"]["step"]["init"]["data"]
        assert set(data["selector"]["default_transportation_mode"]["options"]) == {
            "fastest",
            "driving",
            "transit",
        }


# --- Spec §5.1: people and routines as config subentries -----------------


@pytest.mark.asyncio
async def test_second_entry_aborts_with_add_person_hint(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A second setup attempt aborts at once and points to Add person."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "single_instance_use_add_person"


def test_supported_subentry_types(config_entry: MockConfigEntry) -> None:
    """Exactly the person, routine and announcement subentry types are offered."""
    types = CastStudioFlowHandler.async_get_supported_subentry_types(config_entry)
    assert set(types) == {"person", "routine", "announcement"}


async def _edit_routine(
    hass: HomeAssistant, entry: MockConfigEntry, routine_id: str
) -> dict[str, Any]:
    """Open a routine's gear dialog and choose Edit."""
    result = await entry.start_subentry_reconfigure_flow(hass, routine_id)
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "edit"}
    )


async def _start_subentry(
    hass: HomeAssistant, entry: MockConfigEntry, subentry_type: str
) -> dict[str, Any]:
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, subentry_type),
        context={"source": config_entries.SOURCE_USER},
    )


@pytest.mark.asyncio
async def test_add_person_subentry_creates_person(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Add person stores a person subentry with the per-person defaults."""
    result = await _start_subentry(hass, config_entry, "person")
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"
    keys = [str(k) for k in result["data_schema"].schema]
    assert keys[:4] == [
        "name",
        "phonetic_name",
        "media_player",
        "commute_destination_zone",
    ]
    assert "output_format" not in keys
    assert keys[-1] == "advanced"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "name": "Sam",
            "phonetic_name": "Sam",
            "media_player": "media_player.kitchen_speaker",
            "transportation_mode": "driving",
        },
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Sam"
    (sam,) = get_people(config_entry)
    assert sam.name == "Sam"
    assert sam.media_player == "media_player.kitchen_speaker"
    assert sam.transportation_mode == "driving"
    assert sam.commute_destination_zone is None
    assert sam.reset_time == time(3, 0)
    assert sam.max_age_minutes == 0
    assert sam.settings == {}


@pytest.mark.asyncio
async def test_add_person_flattens_advanced_section(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Advanced-section answers are stored flat and surface as settings."""
    result = await _start_subentry(hass, config_entry, "person")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "name": "Alex",
            "phonetic_name": "Al-ex",
            "transportation_mode": "transit",
            "commute_destination_zone": "zone.office",
            "reset_time": "04:00:00",
            "max_age_minutes": 45,
            "advanced": {
                "weather_entity": "weather.home",
                "host_voice": "Puck",
                "greeting_title": "Morning, Alex",
            },
        },
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    subentry = next(iter(config_entry.subentries.values()))
    assert "advanced" not in subentry.data
    assert subentry.data["weather_entity"] == "weather.home"
    assert subentry.data["reset_time"] == "04:00:00"
    (alex,) = get_people(config_entry)
    assert alex.reset_time == time(4, 0)
    assert alex.max_age_minutes == 45
    assert alex.settings == {
        "weather_entity": "weather.home",
        "host_voice": "Puck",
        "greeting_title": "Morning, Alex",
    }


@pytest.mark.asyncio
async def test_reconfigure_person_updates_subentry(
    hass: HomeAssistant, entry_with_sam: MockConfigEntry
) -> None:
    """Reconfigure edits the person in place and keeps its id."""
    result = await entry_with_sam.start_subentry_reconfigure_flow(hass, SAM_ID)
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_schedule_reload"
    ) as mock_reload:
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={
                "name": "Samantha",
                "phonetic_name": "Sa-man-tha",
                "transportation_mode": "fastest",
                "advanced": {"calendar_entities": ["calendar.sam", "calendar.family"]},
            },
        )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    # The legacy recipient is read at use time, so no reload is needed.
    mock_reload.assert_not_called()
    (person,) = get_people(entry_with_sam)
    assert person.person_id == SAM_ID
    assert person.name == "Samantha"
    assert person.settings == {"calendar_entities": ["calendar.sam", "calendar.family"]}
    assert entry_with_sam.subentries[SAM_ID].title == "Samantha"


@pytest.mark.asyncio
async def test_add_routine_aborts_without_people(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A routine needs a person to belong to."""
    result = await _start_subentry(hass, config_entry, "routine")
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "no_people"


@pytest.mark.asyncio
async def test_add_routine_form_selectors(
    hass: HomeAssistant, entry_with_alex_and_sam: MockConfigEntry
) -> None:
    """The routine form offers current people and the segment types."""
    result = await _start_subentry(hass, entry_with_alex_and_sam, "routine")
    schema = {str(k): v for k, v in result["data_schema"].schema.items()}

    people_options = schema["person_id"].config["options"]
    assert people_options == [
        {"value": ALEX_ID, "label": "Alex"},
        {"value": SAM_ID, "label": "Sam"},
    ]
    segments = schema["segments"].config
    assert segments["multiple"] is True
    assert segments["options"] == list(SEGMENT_TYPES)
    assert schema["dashboard_display"].config["domain"] == ["media_player"]
    assert schema["dashboard_display"].config["integration"] == "cast"


async def _add_routine(
    hass: HomeAssistant, entry: MockConfigEntry, user_input: dict[str, Any]
) -> dict[str, Any]:
    result = await _start_subentry(hass, entry, "routine")
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input=user_input
    )


@pytest.mark.asyncio
async def test_add_routine_subentry_rejects_duplicate_name_for_same_person(
    hass: HomeAssistant, entry_with_sam: MockConfigEntry
) -> None:
    """Routine names are unique per person, ignoring case."""
    result = await _add_routine(
        hass,
        entry_with_sam,
        {"person_id": SAM_ID, "name": "Quick", "segments": ["greeting", "weather"]},
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY

    result = await _add_routine(
        hass,
        entry_with_sam,
        {"person_id": SAM_ID, "name": "quick", "segments": ["weather"]},
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "duplicate_routine"}

    (routine,) = get_routines(entry_with_sam)
    assert routine.name == "Quick"
    assert routine.person_id == SAM_ID
    assert routine.segments == ("greeting", "weather")
    assert routine.dashboard_path is None
    assert routine.dashboard_display is None


@pytest.mark.asyncio
async def test_same_routine_name_allowed_for_different_people(
    hass: HomeAssistant, entry_with_alex_and_sam: MockConfigEntry
) -> None:
    """Alex and Sam may both have a routine called Quick."""
    for person_id in (ALEX_ID, SAM_ID):
        result = await _add_routine(
            hass,
            entry_with_alex_and_sam,
            {"person_id": person_id, "name": "Quick", "segments": ["greeting"]},
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY

    assert [r.person_id for r in get_routines(entry_with_alex_and_sam)] == [
        ALEX_ID,
        SAM_ID,
    ]


@pytest.mark.asyncio
async def test_add_routine_keeps_segment_order_and_dashboard(
    hass: HomeAssistant, entry_with_sam: MockConfigEntry
) -> None:
    """Segments keep the order picked; the picked dashboard view is stored."""
    result = await _add_routine(
        hass,
        entry_with_sam,
        {
            "person_id": SAM_ID,
            "name": "Full",
            "segments": ["closing", "greeting", "commute"],
            "dashboard": "dashboard-home/morning",
            "dashboard_display": "media_player.kitchen_display",
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY

    (routine,) = get_routines(entry_with_sam)
    assert routine.segments == ("closing", "greeting", "commute")
    assert routine.dashboard_path == "dashboard-home"
    assert routine.dashboard_view == "morning"
    assert routine.dashboard_display == "media_player.kitchen_display"


@pytest.mark.asyncio
async def test_routine_form_sets_output_format(
    hass: HomeAssistant, entry_with_sam: MockConfigEntry
) -> None:
    """The routine form offers Audio or Visual, defaulting to Visual."""
    result = await _start_subentry(hass, entry_with_sam, "routine")
    schema = {str(k): k for k in result["data_schema"].schema}
    assert schema["output_format"].default() == "visual"
    selectors = {str(k): v for k, v in result["data_schema"].schema.items()}
    assert selectors["output_format"].config["options"] == ["audio", "visual"]

    await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "person_id": SAM_ID,
            "name": "Night",
            "segments": ["closing"],
            "output_format": "audio",
        },
    )
    (routine,) = get_routines(entry_with_sam)
    assert routine.output_format == "audio"

    result = await _edit_routine(hass, entry_with_sam, routine.routine_id)
    schema = {str(k): k for k in result["data_schema"].schema}
    assert schema["output_format"].default() == "audio"
    await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "person_id": SAM_ID,
            "name": "Night",
            "segments": ["closing"],
            "output_format": "visual",
        },
    )
    (routine,) = get_routines(entry_with_sam)
    assert routine.output_format == "visual"


@pytest.mark.asyncio
async def test_add_routine_requires_segments(
    hass: HomeAssistant, entry_with_sam: MockConfigEntry
) -> None:
    """An empty segment list is rejected."""
    result = await _add_routine(
        hass, entry_with_sam, {"person_id": SAM_ID, "name": "Empty", "segments": []}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"segments": "no_segments"}


@pytest.mark.asyncio
async def test_reconfigure_routine_keeps_own_name_but_rejects_duplicate(
    hass: HomeAssistant, entry_with_sam: MockConfigEntry
) -> None:
    """Reconfigure may keep its own name but not take a sibling's."""
    for name in ("Quick", "Full"):
        await _add_routine(
            hass,
            entry_with_sam,
            {"person_id": SAM_ID, "name": name, "segments": ["greeting"]},
        )
    quick = find_routine(entry_with_sam, "Quick")

    result = await _edit_routine(hass, entry_with_sam, quick.routine_id)
    assert result["step_id"] == "edit"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"person_id": SAM_ID, "name": "full", "segments": ["greeting"]},
    )
    assert result["errors"] == {"base": "duplicate_routine"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "person_id": SAM_ID,
            "name": "Quick",
            "segments": ["weather", "greeting"],
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert find_routine(entry_with_sam, "Quick").segments == ("weather", "greeting")


def test_subentry_translations_present() -> None:
    """Both translation files cover the new abort, subentries and selectors."""
    base = Path(__file__).parent.parent / "custom_components" / "cast_studio"
    for path in (base / "strings.json", base / "translations" / "en.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        abort = data["config"]["abort"]["single_instance_use_add_person"]
        assert "Add person" in abort
        person = data["config_subentries"]["person"]
        assert person["initiate_flow"]["user"] == "Add person"
        for step in ("user", "reconfigure"):
            assert "advanced" in person["step"][step]["sections"]
        routine = data["config_subentries"]["routine"]
        assert routine["error"]["duplicate_routine"]
        assert routine["abort"]["no_people"]
        assert set(data["selector"]["segment_type"]["options"]) == set(SEGMENT_TYPES)
        assert set(data["selector"]["output_format"]["options"]) == {
            "audio",
            "visual",
        }
        for step in ("user", "edit"):
            assert "output_format" in routine["step"][step]["data"]
            assert "output_format" in routine["step"][step]["data_description"]
        for step in ("user", "reconfigure"):
            assert "output_format" not in person["step"][step]["data"]


def test_hacs_floor_supports_subentries() -> None:
    """Subentries need 2025.3; the multi-picture media selector needs 2025.12."""
    hacs = json.loads(
        (Path(__file__).parent.parent / "hacs.json").read_text(encoding="utf-8")
    )
    assert hacs["homeassistant"] == "2025.12.0"


@pytest.mark.asyncio
async def test_user_step_rejects_blank_name(hass: HomeAssistant) -> None:
    """A fresh install needs a real first-person name."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_RECIPIENT_NAME: "   ",
            CONF_PHONETIC_NAME: "Al-ex",
            CONF_OUTPUT_DIR: "/config/www/cast_studio",
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_name"}


@pytest.mark.asyncio
async def test_fresh_install_creates_first_person(hass: HomeAssistant) -> None:
    """Spec §5.1: the user step stores shared settings and the first person."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(
        "custom_components.cast_studio.async_setup_entry",
        AsyncMock(return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_MAPS_API_KEY: "fake_maps_key",
                CONF_DEFAULT_TRANSPORTATION_MODE: "transit",
                CONF_COMMUTE_DESTINATION: "zone.office",
                CONF_RECIPIENT_NAME: " Alex ",
                CONF_PHONETIC_NAME: "Al-ex",
                CONF_OUTPUT_DIR: "/config/www/cast_studio",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.version == 2
    assert dict(entry.data) == {
        CONF_MAPS_API_KEY: "fake_maps_key",
        CONF_DEFAULT_TRANSPORTATION_MODE: "transit",
        CONF_OUTPUT_DIR: "/config/www/cast_studio",
    }
    (alex,) = get_people(entry)
    assert alex.name == "Alex"
    assert alex.phonetic_name == "Al-ex"
    assert alex.commute_destination_zone == "zone.office"
    assert alex.transportation_mode == "transit"
    assert alex.media_player is None
    assert get_routines(entry) == []


@pytest.mark.asyncio
async def test_user_step_prefills_no_name_or_phonetic_name(
    hass: HomeAssistant,
) -> None:
    """A pre-filled phonetic name would respell the new person's own name."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    fields = {str(key): key for key in result["data_schema"].schema}
    name_field = fields[CONF_RECIPIENT_NAME]
    phonetic_field = fields[CONF_PHONETIC_NAME]
    assert isinstance(name_field, vol.Required)
    assert isinstance(phonetic_field, vol.Optional)
    assert not isinstance(phonetic_field, vol.Required)
    for field in (name_field, phonetic_field):
        assert field.default is vol.UNDEFINED
        assert not (field.description or {}).get("suggested_value")


@pytest.mark.asyncio
async def test_fresh_install_blank_phonetic_name_is_not_respelled(
    hass: HomeAssistant,
) -> None:
    """With no phonetic name, the first person is spoken as written."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(
        "custom_components.cast_studio.async_setup_entry",
        AsyncMock(return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_RECIPIENT_NAME: "Sam",
                CONF_OUTPUT_DIR: "/config/www/cast_studio",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    (sam,) = get_people(result["result"])
    assert sam.name == "Sam"
    assert sam.phonetic_name == "Sam"
    lexicon = build_lexicon([sam], sam)
    assert lexicon == []
    assert apply_pronunciations("Good morning, Sam!", lexicon) == "Good morning, Sam!"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["", "   ", "!!!"])
async def test_add_person_rejects_invalid_name(
    hass: HomeAssistant, config_entry: MockConfigEntry, name: str
) -> None:
    """Blank or symbol-only names can't make a sensor slug."""
    result = await _start_subentry(hass, config_entry, "person")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "name": name,
            "phonetic_name": "Sam",
            "transportation_mode": "driving",
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_name"}
    assert get_people(config_entry) == []


@pytest.mark.asyncio
async def test_add_person_rejects_duplicate_slug(
    hass: HomeAssistant, entry_with_sam: MockConfigEntry
) -> None:
    """Person names are unique by slug within the entry."""
    result = await _start_subentry(hass, entry_with_sam, "person")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "name": "SAM!",
            "phonetic_name": "Sam",
            "transportation_mode": "driving",
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "duplicate_person"}
    assert len(get_people(entry_with_sam)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "advanced", "error"),
    [
        (
            "driving",
            {"commute_transit_sensor": "sensor.to_office_transit"},
            "drive_sensor_missing",
        ),
        (
            "transit",
            {"commute_drive_sensor": "sensor.to_office_driving"},
            "transit_sensor_missing",
        ),
    ],
)
async def test_add_person_rejects_sensor_for_the_other_mode(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mode: str,
    advanced: dict[str, str],
    error: str,
) -> None:
    """A commute sensor only in the other mode's field would never be used."""
    result = await _start_subentry(hass, config_entry, "person")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "name": "Sam",
            "phonetic_name": "Sam",
            "transportation_mode": mode,
            "advanced": advanced,
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert get_people(config_entry) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "advanced"),
    [
        ("driving", {}),
        ("driving", {"commute_drive_sensor": "sensor.to_office_driving"}),
        (
            "driving",
            {
                "commute_drive_sensor": "sensor.to_office_driving",
                "commute_transit_sensor": "sensor.to_office_transit",
            },
        ),
        ("fastest", {"commute_transit_sensor": "sensor.to_office_transit"}),
    ],
)
async def test_add_person_accepts_usable_commute_sensors(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mode: str,
    advanced: dict[str, str],
) -> None:
    """No sensor, the mode's own sensor, or Fastest with either is fine."""
    result = await _start_subentry(hass, config_entry, "person")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "name": "Sam",
            "phonetic_name": "Sam",
            "transportation_mode": mode,
            "advanced": advanced,
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_reconfigure_person_keeps_own_name_but_rejects_other(
    hass: HomeAssistant, entry_with_alex_and_sam: MockConfigEntry
) -> None:
    """A person may keep their own name but not take another person's."""
    entry = entry_with_alex_and_sam
    base_input = {
        "phonetic_name": "Sam",
        "transportation_mode": "driving",
    }
    result = await entry.start_subentry_reconfigure_flow(hass, SAM_ID)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={"name": "alex", **base_input}
    )
    assert result["errors"] == {"base": "duplicate_person"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input={"name": "Sam", **base_input}
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["", "   "])
async def test_add_routine_rejects_blank_name(
    hass: HomeAssistant, entry_with_sam: MockConfigEntry, name: str
) -> None:
    """Routine names can't be blank or whitespace-only."""
    result = await _add_routine(
        hass,
        entry_with_sam,
        {"person_id": SAM_ID, "name": name, "segments": ["greeting"]},
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_name"}
    assert get_routines(entry_with_sam) == []


@pytest.mark.asyncio
async def test_add_routine_stores_segments_in_submitted_order(
    hass: HomeAssistant, entry_with_sam: MockConfigEntry
) -> None:
    """Stored segments keep exactly the submitted order, not SEGMENT_TYPES order."""
    submitted = ["daily_encouragement", "weather", "closing", "agenda", "greeting"]
    result = await _add_routine(
        hass,
        entry_with_sam,
        {"person_id": SAM_ID, "name": "Mixed", "segments": submitted},
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    (subentry,) = (
        sub
        for sub in entry_with_sam.subentries.values()
        if sub.subentry_type == "routine"
    )
    assert subentry.data["segments"] == submitted
    assert find_routine(entry_with_sam, "Mixed").segments == tuple(submitted)


@pytest.mark.asyncio
async def test_add_person_stores_commute_sensors_and_zone(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The person's own commute sensors are stored as settings beside the zone."""
    result = await _start_subentry(hass, config_entry, "person")
    schema = result["data_schema"].schema
    advanced = next(v for k, v in schema.items() if str(k) == "advanced")
    advanced_fields = {str(k): v for k, v in advanced.schema.schema.items()}
    for key in ("commute_drive_sensor", "commute_transit_sensor"):
        assert advanced_fields[key].config["domain"] == ["sensor"]
    zone_field = next(
        v for k, v in schema.items() if str(k) == "commute_destination_zone"
    )
    assert zone_field.config["domain"] == ["zone"]

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "name": "Sam",
            "phonetic_name": "Sam",
            "transportation_mode": "driving",
            "commute_destination_zone": "zone.office",
            "reset_time": "03:00:00",
            "max_age_minutes": 0,
            "advanced": {
                "commute_drive_sensor": "sensor.sam_commute_drive",
                "commute_transit_sensor": "sensor.sam_commute_transit",
            },
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    (sam,) = get_people(config_entry)
    assert sam.commute_destination_zone == "zone.office"
    assert sam.settings == {
        "commute_drive_sensor": "sensor.sam_commute_drive",
        "commute_transit_sensor": "sensor.sam_commute_transit",
    }


def test_commute_sensor_and_work_zone_translations() -> None:
    """Both translation files label the commute sensors and the work zone."""
    base = Path(__file__).parent.parent / "custom_components" / "cast_studio"
    for path in (base / "strings.json", base / "translations" / "en.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for step in ("user", "reconfigure"):
            person_step = data["config_subentries"]["person"]["step"][step]
            assert "zone" in person_step["data"]["commute_destination_zone"].lower()
            assert "work" in person_step["data"]["commute_destination_zone"].lower()
            assert person_step["data_description"]["commute_destination_zone"]
            labels = person_step["sections"]["advanced"]["data"]
            assert labels["commute_drive_sensor"]
            assert labels["commute_transit_sensor"]


@pytest.mark.asyncio
async def test_person_form_saves_pronunciations(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Spec §5.8: the advanced pronunciations list is stored and cleaned."""
    result = await _start_subentry(hass, config_entry, "person")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "name": "Alex",
            "phonetic_name": "Al-ex",
            "transportation_mode": "fastest",
            "reset_time": "03:00:00",
            "max_age_minutes": 0,
            "advanced": {
                "pronunciations": [
                    {"written": "Siobhan", "spoken": "Shiv-awn"},
                    {"written": "Blank", "spoken": ""},
                ]
            },
        },
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    (alex,) = get_people(config_entry)
    assert alex.pronunciations == (("Siobhan", "Shiv-awn"),)
    assert alex.settings == {}


@pytest.mark.asyncio
async def test_dashboard_options_list_dashboards_and_views(hass: HomeAssistant) -> None:
    """The picker lists each dashboard, then each of its views."""
    from types import SimpleNamespace

    from homeassistant.components.lovelace.const import LOVELACE_DATA

    from custom_components.cast_studio.config_flow import async_dashboard_options

    def _dashboard(config: dict | None, views: list | Exception) -> SimpleNamespace:
        async def _load(_force: bool) -> dict:  # noqa: FBT001
            if isinstance(views, Exception):
                raise views
            return {"views": views}

        return SimpleNamespace(config=config, async_load=_load)

    hass.data[LOVELACE_DATA] = SimpleNamespace(
        dashboards={
            None: _dashboard(None, [{"title": "Home", "path": "home"}, {}]),
            "dashboard-display": _dashboard(
                {"title": "Nest Display"}, [{"title": "Morning", "path": "morning"}]
            ),
            "dashboard-energy": _dashboard(None, RuntimeError("strategy")),
        }
    )

    options = await async_dashboard_options(hass)

    assert [(o["value"], o["label"]) for o in options] == [
        ("lovelace", "Overview"),
        ("lovelace/home", "Overview › Home"),  # noqa: RUF001
        ("lovelace/1", "Overview › 1"),  # noqa: RUF001
        ("dashboard-display", "Nest Display"),
        ("dashboard-display/morning", "Nest Display › Morning"),  # noqa: RUF001
        ("dashboard-energy", "dashboard-energy"),
    ]


@pytest.mark.asyncio
async def test_reconfigure_routine_prefills_and_clears_dashboard(
    hass: HomeAssistant, entry_with_sam: MockConfigEntry
) -> None:
    """A stored dashboard shows as one picker value; clearing it removes both."""
    await _add_routine(
        hass,
        entry_with_sam,
        {
            "person_id": SAM_ID,
            "name": "Full",
            "segments": ["greeting"],
            "dashboard": "dashboard-home",
        },
    )
    (routine,) = get_routines(entry_with_sam)
    assert (routine.dashboard_path, routine.dashboard_view) == ("dashboard-home", None)

    result = await _edit_routine(hass, entry_with_sam, routine.routine_id)
    schema = result["data_schema"].schema
    (key,) = [k for k in schema if k == "dashboard"]
    assert key.description == {"suggested_value": "dashboard-home"}

    await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"person_id": SAM_ID, "name": "Full", "segments": ["greeting"]},
    )
    (routine,) = get_routines(entry_with_sam)
    assert (routine.dashboard_path, routine.dashboard_view) == (None, None)


def test_daily_text_picker_offers_jw_library_sensors_and_text_helpers() -> None:
    """The daily text field is filtered; fun fact and National Day have none."""
    from custom_components.cast_studio.config_flow import _person_settings_selectors

    selectors = _person_settings_selectors()
    assert "fun_fact_entity" not in selectors
    assert "national_day_entity" not in selectors
    assert selectors["scripture_entity"].config["filter"] == [
        {"integration": "jw_library", "domain": ["sensor"]},
        {"domain": ["input_text"]},
    ]


def _advanced_selectors(schema: vol.Schema) -> dict[str, Any]:
    """Return the advanced section's selectors keyed by field name."""
    advanced = next(
        value for key, value in schema.schema.items() if str(key) == "advanced"
    )
    return {str(key): value for key, value in advanced.schema.schema.items()}


def _option_values(sel: Any) -> set[str]:
    return {option["value"] for option in sel.config["options"]}


@pytest.mark.asyncio
async def test_person_form_offers_role_personas(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Each persona picker lists only the personas suited to its role."""
    result = await _start_subentry(hass, config_entry, "person")
    selectors = _advanced_selectors(result["data_schema"])
    host = selectors["host_personas"]
    reader = selectors["reader_personas"]
    assert host.config["multiple"] is True
    assert "arthur" in _option_values(host)
    assert "malenfant" not in _option_values(host)
    assert "malenfant" in _option_values(reader)
    assert "theo" not in _option_values(reader)


@pytest.mark.asyncio
async def test_add_person_saves_persona_picks(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The picked personas are stored as the person's settings."""
    result = await _start_subentry(hass, config_entry, "person")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "name": "Sam",
            "phonetic_name": "Sam",
            "transportation_mode": "driving",
            "advanced": {
                "host_personas": ["arthur", "theo"],
                "reader_personas": ["sage"],
            },
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    (sam,) = get_people(config_entry)
    assert sam.settings["host_personas"] == ["arthur", "theo"]
    assert sam.settings["reader_personas"] == ["sage"]


def test_strings_cover_persona_fields() -> None:
    """Both translation files label and describe the persona pickers."""
    base = Path(__file__).parent.parent / "custom_components" / "cast_studio"
    for path in (base / "strings.json", base / "translations" / "en.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for step in ("user", "reconfigure"):
            advanced = data["config_subentries"]["person"]["step"][step]["sections"][
                "advanced"
            ]
            for key in ("host_personas", "reader_personas"):
                assert advanced["data"][key]
                assert advanced["data_description"][key]


@pytest.mark.asyncio
async def test_reconfigure_person_drops_removed_personas(
    hass: HomeAssistant, entry_with_sam: MockConfigEntry
) -> None:
    """A persona no longer in the catalog doesn't block saving the person."""
    subentry = entry_with_sam.subentries[SAM_ID]
    hass.config_entries.async_update_subentry(
        entry_with_sam,
        subentry,
        data={**subentry.data, "host_personas": ["gone_persona", "arthur"]},
    )
    result = await entry_with_sam.start_subentry_reconfigure_flow(hass, SAM_ID)
    advanced = next(
        value
        for key, value in result["data_schema"].schema.items()
        if str(key) == "advanced"
    )
    field = next(key for key in advanced.schema.schema if str(key) == "host_personas")
    assert field.description["suggested_value"] == ["arthur"]


# --- Announcements ------------------------------------------------------------

DOOR_PICTURE = "media-source://media_source/local/door.jpg"


def _announcement_input(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "Freezer Door",
        "description": "A cold blue slide with 'Close the freezer' across the top",
        "wording": "verbatim",
        "message": "Close the freezer door, please.",
        "freshness": "static",
        "personas": [],
        "rebuild_time": "03:00:00",
        "output_format": "visual",
    }
    data.update(overrides)
    return data


async def _add_announcement(
    hass: HomeAssistant, entry: MockConfigEntry, user_input: dict[str, Any]
) -> dict[str, Any]:
    result = await _start_subentry(hass, entry, "announcement")
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input=user_input
    )


async def test_add_announcement_stores_settings(
    hass: HomeAssistant, entry_with_sam: MockConfigEntry
) -> None:
    """The form stores every setting, pictures as their media ids."""
    result = await _add_announcement(
        hass,
        entry_with_sam,
        _announcement_input(
            pictures=[
                {"media_content_id": DOOR_PICTURE, "media_content_type": "image/jpeg"}
            ],
            target="media_player.kitchen_speaker",
        ),
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Freezer Door"
    assert result["data"] == {
        "name": "Freezer Door",
        "description": "A cold blue slide with 'Close the freezer' across the top",
        "wording": "verbatim",
        "message": "Close the freezer door, please.",
        "freshness": "static",
        "personas": ["default_host"],
        "rebuild_time": "03:00:00",
        "pictures": [DOOR_PICTURE],
        "output_format": "visual",
        "target": "media_player.kitchen_speaker",
    }


@pytest.mark.parametrize(
    ("overrides", "errors"),
    [
        ({"name": "  "}, {"base": "invalid_name"}),
        ({"name": "freezer door"}, {"base": "duplicate_announcement"}),
        ({"name": "Other", "message": " "}, {"message": "message_required"}),
        (
            {"name": "Other", "personas": ["elena", "cole"]},
            {"personas": "one_persona"},
        ),
        (
            {
                "name": "Other",
                "pictures": [
                    {
                        "media_content_id": f"{DOOR_PICTURE}{n}",
                        "media_content_type": "image/jpeg",
                    }
                    for n in range(5)
                ],
            },
            {"pictures": "too_many_pictures"},
        ),
        (
            {
                "name": "Other",
                "pictures": [
                    {
                        "media_content_id": "media-source://camera/camera.door",
                        "media_content_type": "image/jpeg",
                    }
                ],
            },
            {"pictures": "local_pictures_only"},
        ),
    ],
)
async def test_announcement_errors(
    hass: HomeAssistant,
    entry_with_sam: MockConfigEntry,
    overrides: dict[str, Any],
    errors: dict[str, str],
) -> None:
    """Each bad input shows its error and stores nothing."""
    await _add_announcement(hass, entry_with_sam, _announcement_input())
    result = await _add_announcement(
        hass, entry_with_sam, _announcement_input(**overrides)
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == errors


async def test_daily_announcement_allows_several_personas_and_no_message(
    hass: HomeAssistant, entry_with_sam: MockConfigEntry
) -> None:
    """Creative daily announcements need no message and may list personas."""
    result = await _add_announcement(
        hass,
        entry_with_sam,
        _announcement_input(
            wording="creative",
            message="",
            freshness="daily",
            personas=["elena", "cole"],
        ),
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"]["personas"] == ["elena", "cole"]


async def test_reconfigure_announcement_updates_data(
    hass: HomeAssistant, entry_with_sam: MockConfigEntry
) -> None:
    """Reconfigure keeps its own name and saves the change."""
    await _add_announcement(hass, entry_with_sam, _announcement_input())
    (subentry_id,) = [
        sid
        for sid, sub in entry_with_sam.subentries.items()
        if sub.subentry_type == "announcement"
    ]
    result = await entry_with_sam.start_subentry_reconfigure_flow(hass, subentry_id)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input=_announcement_input(message="Shut the freezer.")
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry_with_sam.subentries[subentry_id].data["message"] == "Shut the freezer."


def test_announcement_translations_present() -> None:
    """Both translation files cover the announcement form and its selectors."""
    base = Path(__file__).parent.parent / "custom_components" / "cast_studio"
    for path in (base / "strings.json", base / "translations" / "en.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        ann = data["config_subentries"]["announcement"]
        assert ann["initiate_flow"]["user"] == "Add announcement"
        for step in ("user", "reconfigure"):
            for field in _announcement_input() | {"pictures": [], "target": None}:
                assert field in ann["step"][step]["data"]
                assert field in ann["step"][step]["data_description"]
        for error in (
            "invalid_name",
            "duplicate_announcement",
            "message_required",
            "one_persona",
            "too_many_pictures",
            "local_pictures_only",
        ):
            assert ann["error"][error]
        assert set(data["selector"]["announcement_wording"]["options"]) == {
            "verbatim",
            "creative",
        }
        assert set(data["selector"]["announcement_freshness"]["options"]) == {
            "static",
            "daily",
        }
