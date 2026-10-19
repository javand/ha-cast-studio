"""Unit tests for integration setup and unload lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from custom_components.cast_studio import (
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.cast_studio.const import (
    CONF_PHONETIC_NAME,
    CONF_RECIPIENT_NAME,
    DOMAIN,
)


@pytest.mark.asyncio
async def test_setup_component(hass: HomeAssistant) -> None:
    """Test setting up the cast_studio component via yaml."""
    assert await async_setup(hass, {}) is True
    assert DOMAIN in hass.data
    assert "recipient" in hass.data[DOMAIN]
    assert "routines" in hass.data[DOMAIN]
    assert hass.services.has_service(DOMAIN, "pregenerate_routine")
    assert hass.services.has_service(DOMAIN, "run_routine")
    assert hass.services.has_service(DOMAIN, "compile_clip")
    assert hass.services.has_service(DOMAIN, "reload")


@pytest.mark.asyncio
async def test_setup_unload_entry(hass: HomeAssistant) -> None:
    """Test setting up and unloading the integration via config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="test_cast_studio",
        data={
            CONF_RECIPIENT_NAME: "Alice",
            CONF_PHONETIC_NAME: "Al-iss",
        },
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        AsyncMock(return_value=True),
    ) as mock_forward:
        assert await async_setup_entry(hass, entry) is True
        mock_forward.assert_awaited_once()
        assert hass.data[DOMAIN]["recipient"] == {
            "name": "Alice",
            "phonetic_name": "Al-iss",
        }

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        AsyncMock(return_value=True),
    ) as mock_unload:
        assert await async_unload_entry(hass, entry) is True
        mock_unload.assert_awaited_once()


@pytest.mark.asyncio
async def test_reload_entry(hass: HomeAssistant) -> None:
    """Test reloading the integration via config entries reload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="test_cast_studio",
        data={
            CONF_RECIPIENT_NAME: "Alice",
            CONF_PHONETIC_NAME: "Al-iss",
        },
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    hass.config.components.add(DOMAIN)

    with (
        patch(
            "custom_components.cast_studio.async_unload_entry",
            AsyncMock(return_value=True),
        ) as mock_unload,
        patch(
            "custom_components.cast_studio.async_setup_entry",
            AsyncMock(return_value=True),
        ) as mock_setup,
    ):
        await hass.config_entries.async_reload(entry.entry_id)
        mock_unload.assert_awaited_once()
        mock_setup.assert_awaited_once()
