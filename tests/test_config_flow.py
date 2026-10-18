"""Tests for the CastStudio config and options flow."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant import config_entries, data_entry_flow

from custom_components.cast_studio.config_flow import (
    CastStudioFlowHandler,
    CastStudioOptionsFlowHandler,
)
from custom_components.cast_studio.const import (
    CONF_API_KEY,
    CONF_OUTPUT_DIR,
    CONF_PHONETIC_NAME,
    CONF_RECIPIENT_NAME,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PHONETIC_NAME,
    DEFAULT_RECIPIENT_NAME,
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


@pytest.mark.asyncio
async def test_options_flow_init_step_shows_form():
    """Test options flow displays current config entry values."""
    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry_id"
    mock_entry.data = {
        CONF_API_KEY: "original_key",
        CONF_RECIPIENT_NAME: "Javan",
        CONF_PHONETIC_NAME: "Jay-ven",
        CONF_OUTPUT_DIR: "/config/www/cast_studio",
    }
    mock_entry.options = {}

    options_flow = CastStudioOptionsFlowHandler(mock_entry)
    mock_hass = MagicMock()
    mock_hass.config_entries.async_get_known_entry = MagicMock(return_value=mock_entry)
    options_flow.hass = mock_hass

    result = await options_flow.async_step_init(user_input=None)

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "init"


@pytest.mark.asyncio
async def test_options_flow_updates_options():
    """Test submitting new options creates entry with updated values."""
    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry_id"
    mock_entry.data = {
        CONF_RECIPIENT_NAME: "Javan",
        CONF_PHONETIC_NAME: "Jay-ven",
        CONF_OUTPUT_DIR: "/config/www/cast_studio",
    }
    mock_entry.options = {}

    options_flow = CastStudioOptionsFlowHandler(mock_entry)
    mock_hass = MagicMock()
    mock_hass.config_entries.async_get_known_entry = MagicMock(return_value=mock_entry)
    options_flow.hass = mock_hass

    updated_input = {
        CONF_API_KEY: "new_api_key",
        CONF_RECIPIENT_NAME: "Bob",
        CONF_PHONETIC_NAME: "Bahb",
        CONF_OUTPUT_DIR: "/config/www/custom_dir",
    }

    result = await options_flow.async_step_init(user_input=updated_input)

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"] == updated_input


def test_async_get_options_flow():
    """Verify flow handler returns an OptionsFlow instance."""
    mock_entry = MagicMock()
    handler = CastStudioFlowHandler.async_get_options_flow(mock_entry)
    assert isinstance(handler, config_entries.OptionsFlow)
