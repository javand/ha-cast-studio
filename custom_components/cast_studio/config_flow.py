"""Config flow for CastStudio integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_API_KEY,
    CONF_OUTPUT_DIR,
    CONF_PHONETIC_NAME,
    CONF_RECIPIENT_NAME,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PHONETIC_NAME,
    DEFAULT_RECIPIENT_NAME,
    DOMAIN,
)


class CastStudioFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CastStudio."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="CastStudio",
                data=user_input,
            )

        data_schema = vol.Schema(
            {
                vol.Optional(CONF_API_KEY): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.PASSWORD,
                    ),
                ),
                vol.Required(
                    CONF_RECIPIENT_NAME,
                    default=DEFAULT_RECIPIENT_NAME,
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,
                    ),
                ),
                vol.Required(
                    CONF_PHONETIC_NAME,
                    default=DEFAULT_PHONETIC_NAME,
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,
                    ),
                ),
                vol.Required(
                    CONF_OUTPUT_DIR,
                    default=DEFAULT_OUTPUT_DIR,
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,
                    ),
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> CastStudioOptionsFlowHandler:
        """Get the options flow for this handler."""
        return CastStudioOptionsFlowHandler(config_entry)


class CastStudioOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for CastStudio."""

    def __init__(self, config_entry: config_entries.ConfigEntry | None = None) -> None:
        """Initialize options flow."""
        super().__init__()
        if config_entry is not None:
            self._custom_config_entry = config_entry

    @property
    def config_entry(self) -> config_entries.ConfigEntry:
        """Return the config entry."""
        if hasattr(self, "_custom_config_entry"):
            return self._custom_config_entry
        return super().config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        entry = self.config_entry
        options = entry.options if entry else {}
        data = entry.data if entry else {}

        current_api_key = options.get(CONF_API_KEY, data.get(CONF_API_KEY, ""))
        current_recipient = options.get(
            CONF_RECIPIENT_NAME, data.get(CONF_RECIPIENT_NAME, DEFAULT_RECIPIENT_NAME)
        )
        current_phonetic = options.get(
            CONF_PHONETIC_NAME, data.get(CONF_PHONETIC_NAME, DEFAULT_PHONETIC_NAME)
        )
        current_output_dir = options.get(
            CONF_OUTPUT_DIR, data.get(CONF_OUTPUT_DIR, DEFAULT_OUTPUT_DIR)
        )

        schema_fields: dict[Any, Any] = {}
        if current_api_key:
            schema_fields[vol.Optional(CONF_API_KEY, default=current_api_key)] = (
                selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.PASSWORD,
                    )
                )
            )
        else:
            schema_fields[vol.Optional(CONF_API_KEY)] = selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD,
                )
            )

        schema_fields[
            vol.Required(CONF_RECIPIENT_NAME, default=current_recipient)
        ] = selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.TEXT,
            )
        )
        schema_fields[
            vol.Required(CONF_PHONETIC_NAME, default=current_phonetic)
        ] = selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.TEXT,
            )
        )
        schema_fields[
            vol.Required(CONF_OUTPUT_DIR, default=current_output_dir)
        ] = selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.TEXT,
            )
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_fields),
        )


# Backward-compatible aliases
AutomationGeneratorFlowHandler = CastStudioFlowHandler
AutomationGeneratorOptionsFlowHandler = CastStudioOptionsFlowHandler
