"""Tests for the CastStudio config and options flow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
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


@pytest.mark.asyncio
async def test_config_flow_user_step_shows_form():
    """Test initial user step presents form with expected fields."""
    flow = CastStudioFlowHandler()
    flow.hass = MagicMock()

    result = await flow.async_step_user(user_input=None)

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


@pytest.mark.asyncio
async def test_config_flow_user_step_creates_entry():
    """Test successful user flow submission creates entry with title and data."""
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
    assert result["data"] == user_input


@pytest.mark.asyncio
async def test_config_flow_aborts_if_already_configured():
    """Test that configuring a second instance aborts with already_configured."""
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

    assert exc_info.value.reason == "already_configured"


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
            CONF_RECIPIENT_NAME: "Javan",
            CONF_PHONETIC_NAME: "Jay-ven",
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
            CONF_RECIPIENT_NAME: "Javan",
            CONF_PHONETIC_NAME: "Jay-ven",
            CONF_OUTPUT_DIR: "/config/www/cast_studio",
        },
        options={
            CONF_RECIPIENT_NAME: "Updated Javan",
            CONF_PHONETIC_NAME: "Jay-ven",
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
            CONF_RECIPIENT_NAME: "Javan",
            CONF_PHONETIC_NAME: "Jay-ven",
            CONF_OUTPUT_DIR: "/config/www/cast_studio",
        },
        options={},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == data_entry_flow.FlowResultType.FORM

    updated_input = {
        CONF_API_KEY: "new_api_key",
        CONF_RECIPIENT_NAME: "Bob",
        CONF_PHONETIC_NAME: "Bahb",
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
    assert "already_configured" in data["config"]["abort"]

    assert "options" in data
    assert "step" in data["options"]
    assert "init" in data["options"]["step"]
    init_step = data["options"]["step"]["init"]
    assert init_step["title"] == "CastStudio Options"
    assert "recipient_name" in init_step["data"]


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
    """Both flows offer an optional zone selector for the commute destination."""
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
    opt_key = next(
        k for k in result["data_schema"].schema if str(k) == CONF_COMMUTE_DESTINATION
    )
    assert opt_key.description == {"suggested_value": "zone.fake_office"}


def test_commute_destination_zone_has_translations() -> None:
    """strings.json and en.json label the destination field and mode options."""
    base = Path(__file__).parent.parent / "custom_components" / "cast_studio"
    for path in (base / "strings.json", base / "translations" / "en.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert CONF_COMMUTE_DESTINATION in data["config"]["step"]["user"]["data"]
        assert CONF_COMMUTE_DESTINATION in data["options"]["step"]["init"]["data"]
        assert set(data["selector"]["default_transportation_mode"]["options"]) == {
            "fastest",
            "driving",
            "transit",
        }
