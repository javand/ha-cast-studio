"""Config flow for CastStudio integration."""

from __future__ import annotations

from datetime import time
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .announcements import (
    ANN_DESCRIPTION,
    ANN_FRESHNESS,
    ANN_MESSAGE,
    ANN_NAME,
    ANN_OUTPUT_FORMAT,
    ANN_PERSONAS,
    ANN_PICTURES,
    ANN_REBUILD_TIME,
    ANN_TARGET,
    ANN_WORDING,
    DEFAULT_PERSONA,
    DEFAULT_REBUILD_TIME,
    FRESHNESS,
    LOCAL_MEDIA_PREFIX,
    MAX_PICTURES,
    SUBENTRY_ANNOUNCEMENT,
    WORDINGS,
    get_announcements,
)
from .const import (
    CONF_API_KEY,
    CONF_COMMUTE_DESTINATION,
    CONF_DEFAULT_TRANSPORTATION_MODE,
    CONF_MAPS_API_KEY,
    CONF_OUTPUT_DIR,
    CONF_PHONETIC_NAME,
    CONF_RECIPIENT_NAME,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_TRANSPORTATION_MODE,
    DOMAIN,
    LOGGER,
)
from .people import (
    DEFAULT_MAX_AGE_MINUTES,
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_RESET_TIME,
    OUTPUT_FORMATS,
    PERSON_CALENDARS,
    PERSON_COMMUTE_DESTINATION,
    PERSON_COMMUTE_DRIVE_RETURN_SENSOR,
    PERSON_COMMUTE_DRIVE_SENSOR,
    PERSON_COMMUTE_TRANSIT_RETURN_SENSOR,
    PERSON_COMMUTE_TRANSIT_SENSOR,
    PERSON_CORE_KEYS,
    PERSON_HOST_PERSONAS,
    PERSON_MAX_AGE_MINUTES,
    PERSON_MEDIA_PLAYER,
    PERSON_NAME,
    PERSON_PHONETIC_NAME,
    PERSON_PRONUNCIATIONS,
    PERSON_READER_PERSONAS,
    PERSON_RESET_TIME,
    PERSON_SETTINGS_KEYS,
    PERSON_TRANSPORTATION_MODE,
    ROUTINE_DASHBOARD,
    ROUTINE_DASHBOARD_DISPLAY,
    ROUTINE_DASHBOARD_PATH,
    ROUTINE_DASHBOARD_VIEW,
    ROUTINE_NAME,
    ROUTINE_OUTPUT_FORMAT,
    ROUTINE_PERSON_ID,
    ROUTINE_SEGMENTS,
    SEGMENT_TYPES,
    SUBENTRY_PERSON,
    SUBENTRY_ROUTINE,
    TRANSPORTATION_MODES,
    build_person_data,
    clean_pronunciations,
    get_people,
    get_routines,
    is_valid_name,
    person_calendars,
    slugify_name,
)
from .personas import (
    PERSONAS,
    ROLE_HOST,
    ROLE_READER,
    get_persona,
    personas_for_role,
)

ADVANCED_SECTION = "advanced"

# Install-wide settings kept on the entry; everything per person is a subentry.
SHARED_SETTINGS_KEYS: tuple[str, ...] = (
    CONF_API_KEY,
    CONF_MAPS_API_KEY,
    CONF_DEFAULT_TRANSPORTATION_MODE,
    CONF_OUTPUT_DIR,
)


def _destination_selector() -> selector.EntitySelector:
    """Select the commute destination from the existing HA zones."""
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="zone"))


class CastStudioFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CastStudio."""

    VERSION = 2
    MINOR_VERSION = 2

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        # One entry per install; more people are added inside it (§5.1).
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured(error="single_instance_use_add_person")

        if user_input is not None:
            name = (user_input.get(CONF_RECIPIENT_NAME) or "").strip()
            if not is_valid_name(name):
                errors["base"] = "invalid_name"
            else:
                person = build_person_data(
                    name,
                    (user_input.get(CONF_PHONETIC_NAME) or "").strip() or None,
                    user_input.get(CONF_COMMUTE_DESTINATION),
                    user_input.get(CONF_DEFAULT_TRANSPORTATION_MODE),
                )
                return self.async_create_entry(
                    title="CastStudio",
                    data={
                        key: value
                        for key, value in user_input.items()
                        if key in SHARED_SETTINGS_KEYS
                    },
                    subentries=[
                        config_entries.ConfigSubentryData(
                            data=person,
                            subentry_type=SUBENTRY_PERSON,
                            title=name,
                            unique_id=None,
                        )
                    ],
                )

        data_schema = vol.Schema(
            {
                vol.Optional(CONF_API_KEY): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.PASSWORD,
                    ),
                ),
                vol.Optional(CONF_MAPS_API_KEY): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.PASSWORD,
                    ),
                ),
                vol.Optional(
                    CONF_DEFAULT_TRANSPORTATION_MODE,
                    description={"suggested_value": DEFAULT_TRANSPORTATION_MODE},
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["fastest", "driving", "transit"],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        translation_key="default_transportation_mode",
                    ),
                ),
                vol.Optional(CONF_COMMUTE_DESTINATION): _destination_selector(),
                # No defaults: a pre-filled name or phonetic name would end up
                # respelling the new person's own name in every briefing.
                vol.Required(CONF_RECIPIENT_NAME): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,
                    ),
                ),
                # Blank means the name is spoken as written.
                vol.Optional(CONF_PHONETIC_NAME): selector.TextSelector(
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
        config_entry: config_entries.ConfigEntry,  # noqa: ARG004
    ) -> CastStudioOptionsFlowHandler:
        """Get the options flow for this handler."""
        return CastStudioOptionsFlowHandler()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls,
        config_entry: config_entries.ConfigEntry,  # noqa: ARG003
    ) -> dict[str, type[config_entries.ConfigSubentryFlow]]:
        """Return the person, routine and announcement subentry flows."""
        return {
            SUBENTRY_PERSON: PersonSubentryFlow,
            SUBENTRY_ROUTINE: RoutineSubentryFlow,
            SUBENTRY_ANNOUNCEMENT: AnnouncementSubentryFlow,
        }


def _text_selector(*, multiline: bool = False) -> selector.TextSelector:
    return selector.TextSelector(
        selector.TextSelectorConfig(
            type=selector.TextSelectorType.TEXT, multiline=multiline
        )
    )


def _entity_selector(domain: str | None = None) -> selector.EntitySelector:
    if domain is None:
        return selector.EntitySelector(selector.EntitySelectorConfig())
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))


def _persona_selector(role: str | None) -> selector.SelectSelector:
    """Pick one or more of the built-in personas suited to a role (any if None)."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(value=persona.persona_id, label=persona.name)
                for persona in (PERSONAS if role is None else personas_for_role(role))
            ],
            multiple=True,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _person_settings_selectors() -> dict[str, selector.Selector]:
    """Selectors for the per-person segment settings, in form order."""
    return {
        "weather_entity": _entity_selector("weather"),
        PERSON_CALENDARS: selector.EntitySelector(
            selector.EntitySelectorConfig(domain="calendar", multiple=True)
        ),
        "todo_entity": _entity_selector("todo"),
        # The daily text comes from JW Library; a text helper lets any other
        # source (an automation, a REST sensor copied in) feed it.
        "scripture_entity": selector.EntitySelector(
            selector.EntitySelectorConfig(
                filter=[
                    selector.EntityFilterSelectorConfig(
                        integration="jw_library", domain="sensor"
                    ),
                    selector.EntityFilterSelectorConfig(domain="input_text"),
                ]
            )
        ),
        PERSON_COMMUTE_DRIVE_SENSOR: _entity_selector("sensor"),
        PERSON_COMMUTE_TRANSIT_SENSOR: _entity_selector("sensor"),
        PERSON_COMMUTE_DRIVE_RETURN_SENSOR: _entity_selector("sensor"),
        PERSON_COMMUTE_TRANSIT_RETURN_SENSOR: _entity_selector("sensor"),
        PERSON_HOST_PERSONAS: _persona_selector(ROLE_HOST),
        PERSON_READER_PERSONAS: _persona_selector(ROLE_READER),
        "host_voice": _text_selector(),
        "scripture_voice": _text_selector(),
        "host_personality": _text_selector(multiline=True),
        "scripture_personality": _text_selector(multiline=True),
        "conversation_agent": selector.ConversationAgentSelector(),
        "greeting_title": _text_selector(),
        "greeting_subtitle": _text_selector(),
        "greeting_image": _text_selector(),
        PERSON_PRONUNCIATIONS: selector.ObjectSelector(
            selector.ObjectSelectorConfig(
                multiple=True,
                fields={
                    "written": {"selector": {"text": {}}, "required": True},
                    "spoken": {"selector": {"text": {}}, "required": True},
                },
                label_field="written",
                translation_key=PERSON_PRONUNCIATIONS,
            )
        ),
    }


def _suggested(value: Any) -> dict[str, Any] | None:
    return None if value in (None, "") else {"suggested_value": value}


def _person_schema(current: dict[str, Any], default_mode: str) -> vol.Schema:
    """Build the person form: §5.1 fields first, settings in a section."""
    current = {
        **current,
        PERSON_CALENDARS: person_calendars(current) or None,
        # A persona dropped from the catalog would fail the picker's validation.
        **{
            key: [pid for pid in current.get(key) or () if get_persona(pid)] or None
            for key in (PERSON_HOST_PERSONAS, PERSON_READER_PERSONAS)
        },
    }
    settings_fields: dict[Any, Any] = {
        vol.Optional(key, description=_suggested(current.get(key))): sel
        for key, sel in _person_settings_selectors().items()
    }
    return vol.Schema(
        {
            vol.Required(
                PERSON_NAME, description=_suggested(current.get(PERSON_NAME))
            ): _text_selector(),
            vol.Required(
                PERSON_PHONETIC_NAME,
                description=_suggested(current.get(PERSON_PHONETIC_NAME)),
            ): _text_selector(),
            vol.Optional(
                PERSON_MEDIA_PLAYER,
                description=_suggested(current.get(PERSON_MEDIA_PLAYER)),
            ): _entity_selector("media_player"),
            vol.Optional(
                PERSON_COMMUTE_DESTINATION,
                description=_suggested(current.get(PERSON_COMMUTE_DESTINATION)),
            ): _destination_selector(),
            vol.Required(
                PERSON_TRANSPORTATION_MODE,
                default=current.get(PERSON_TRANSPORTATION_MODE, default_mode),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(TRANSPORTATION_MODES),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key="default_transportation_mode",
                )
            ),
            vol.Required(
                PERSON_RESET_TIME,
                default=current.get(PERSON_RESET_TIME, DEFAULT_RESET_TIME),
            ): selector.TimeSelector(),
            vol.Required(
                PERSON_MAX_AGE_MINUTES,
                default=current.get(PERSON_MAX_AGE_MINUTES, DEFAULT_MAX_AGE_MINUTES),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=1440,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="min",
                )
            ),
            vol.Optional(ADVANCED_SECTION): section(
                vol.Schema(settings_fields), {"collapsed": True}
            ),
        }
    )


def _person_data(user_input: dict[str, Any]) -> dict[str, Any]:
    """Flatten the form answers into the stored person subentry data."""
    advanced = user_input.get(ADVANCED_SECTION) or {}
    data: dict[str, Any] = {
        key: user_input.get(key) or None for key in PERSON_CORE_KEYS
    }
    for key in (PERSON_NAME, PERSON_PHONETIC_NAME):
        data[key] = (data[key] or "").strip() or None
    data[PERSON_RESET_TIME] = time.fromisoformat(
        user_input.get(PERSON_RESET_TIME) or DEFAULT_RESET_TIME
    ).isoformat()
    data[PERSON_MAX_AGE_MINUTES] = int(
        user_input.get(PERSON_MAX_AGE_MINUTES) or DEFAULT_MAX_AGE_MINUTES
    )
    for key in PERSON_SETTINGS_KEYS:
        if advanced.get(key) not in (None, "", []):
            data[key] = advanced[key]
    data.pop(PERSON_PRONUNCIATIONS, None)
    if rows := clean_pronunciations(advanced.get(PERSON_PRONUNCIATIONS)):
        data[PERSON_PRONUNCIATIONS] = [
            {"written": written, "spoken": spoken} for written, spoken in rows
        ]
    return data


def _validate_commute_sensors(data: dict[str, Any]) -> dict[str, str]:
    """
    Reject a commute sensor set only in the other mode's field.

    A driving person only reads the drive sensor and a transit person only the
    transit one, so a sensor in the wrong field silently drops the commute.
    """
    mode = data.get(PERSON_TRANSPORTATION_MODE)
    drive = data.get(PERSON_COMMUTE_DRIVE_SENSOR)
    transit = data.get(PERSON_COMMUTE_TRANSIT_SENSOR)
    if mode == "driving" and transit and not drive:
        return {"base": "drive_sensor_missing"}
    if mode == "transit" and drive and not transit:
        return {"base": "transit_sensor_missing"}
    return {}


class PersonSubentryFlow(config_entries.ConfigSubentryFlow):
    """Add or edit a person who receives briefings."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Add a person."""
        errors: dict[str, str] = {}
        current: dict[str, Any] = {}
        if user_input is not None:
            data = _person_data(user_input)
            if not (errors := self._validate(data, own_id=None)):
                return self.async_create_entry(title=data[PERSON_NAME], data=data)
            current = data
        return self.async_show_form(
            step_id="user", data_schema=self._schema(current), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Edit an existing person."""
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        current = dict(subentry.data)
        if user_input is not None:
            data = _person_data(user_input)
            if not (errors := self._validate(data, own_id=subentry.subentry_id)):
                # No reload needed: people are read from the entry at use time.
                return self.async_update_and_abort(
                    self._get_entry(), subentry, title=data[PERSON_NAME], data=data
                )
            current = data
        return self.async_show_form(
            step_id="reconfigure", data_schema=self._schema(current), errors=errors
        )

    def _validate(self, data: dict[str, Any], own_id: str | None) -> dict[str, str]:
        """Names must be real and unique by slug, since sensors key on the slug."""
        name = data[PERSON_NAME] or ""
        if not is_valid_name(name):
            return {"base": "invalid_name"}
        slug = slugify_name(name)
        if any(
            person.person_id != own_id and slugify_name(person.name) == slug
            for person in get_people(self._get_entry())
        ):
            return {"base": "duplicate_person"}
        return _validate_commute_sensors(data)

    def _schema(self, current: dict[str, Any]) -> vol.Schema:
        entry = self._get_entry()
        default_mode = entry.options.get(
            CONF_DEFAULT_TRANSPORTATION_MODE,
            entry.data.get(
                CONF_DEFAULT_TRANSPORTATION_MODE, DEFAULT_TRANSPORTATION_MODE
            ),
        )
        return _person_schema(current, default_mode)


class RoutineSubentryFlow(config_entries.ConfigSubentryFlow):
    """Add or edit a routine: one person and an ordered list of segments."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Add a routine."""
        return await self._async_step_form("user", None, user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Edit an existing routine."""
        return await self._async_step_form(
            "reconfigure", self._get_reconfigure_subentry(), user_input
        )

    async def _async_step_form(
        self,
        step_id: str,
        subentry: config_entries.ConfigSubentry | None,
        user_input: dict[str, Any] | None,
    ) -> config_entries.SubentryFlowResult:
        entry = self._get_entry()
        people = get_people(entry)
        if not people:
            return self.async_abort(reason="no_people")

        errors: dict[str, str] = {}
        current = dict(subentry.data) if subentry else {}
        if user_input is not None:
            data = _routine_data(user_input)
            current = data
            own_id = subentry.subentry_id if subentry else None
            if not is_valid_name(data[ROUTINE_NAME]):
                errors["base"] = "invalid_name"
            elif not data[ROUTINE_SEGMENTS]:
                errors[ROUTINE_SEGMENTS] = "no_segments"
            elif any(
                routine.routine_id != own_id
                and slugify_name(routine.name) == slugify_name(data[ROUTINE_NAME])
                for routine in get_routines(entry, data[ROUTINE_PERSON_ID])
            ):
                errors["base"] = "duplicate_routine"
            else:
                person_name = next(
                    p.name for p in people if p.person_id == data[ROUTINE_PERSON_ID]
                )
                title = f"{data[ROUTINE_NAME]} ({person_name})"
                if subentry is None:
                    return self.async_create_entry(title=title, data=data)
                return self.async_update_and_abort(
                    entry, subentry, title=title, data=data
                )

        return self.async_show_form(
            step_id=step_id,
            data_schema=_routine_schema(
                people, current, await async_dashboard_options(self.hass)
            ),
            errors=errors,
        )


def _routine_data(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        ROUTINE_PERSON_ID: user_input[ROUTINE_PERSON_ID],
        ROUTINE_NAME: (user_input.get(ROUTINE_NAME) or "").strip(),
        ROUTINE_SEGMENTS: list(user_input.get(ROUTINE_SEGMENTS) or []),
        ROUTINE_OUTPUT_FORMAT: user_input.get(ROUTINE_OUTPUT_FORMAT)
        or DEFAULT_OUTPUT_FORMAT,
        **_split_dashboard(user_input.get(ROUTINE_DASHBOARD)),
        ROUTINE_DASHBOARD_DISPLAY: user_input.get(ROUTINE_DASHBOARD_DISPLAY) or None,
    }


def _join_dashboard(path: str | None, view: str | None) -> str | None:
    """Return the picker value for a stored dashboard path and view."""
    if not path:
        return None
    return f"{path}/{view}" if view else path


def _split_dashboard(value: str | None) -> dict[str, str | None]:
    """Split a picker value ``<dashboard>[/<view>]`` into the stored fields."""
    path, _, view = (value or "").strip().strip("/").partition("/")
    return {
        ROUTINE_DASHBOARD_PATH: path or None,
        ROUTINE_DASHBOARD_VIEW: (view or None) if path else None,
    }


async def async_dashboard_options(
    hass: HomeAssistant,
) -> list[selector.SelectOptionDict]:
    """
    List every dashboard and each of its views for the routine's picker.

    Values are ``<dashboard url path>`` (its first view) or
    ``<dashboard url path>/<view path or index>``, the arguments
    ``cast.show_lovelace_view`` takes. A dashboard whose config cannot be
    loaded (a strategy dashboard, or one never saved) is listed without views.
    """
    try:
        from homeassistant.components.lovelace.const import (  # noqa: PLC0415
            LOVELACE_DATA,
        )
    except ImportError:
        return []
    lovelace = hass.data.get(LOVELACE_DATA)
    if lovelace is None:
        return []

    options: list[selector.SelectOptionDict] = []
    for url_path, dashboard in lovelace.dashboards.items():
        path = url_path or "lovelace"
        title = (dashboard.config or {}).get("title") or (
            "Overview" if url_path is None else path
        )
        options.append(selector.SelectOptionDict(value=path, label=title))
        try:
            config = await dashboard.async_load(False)  # noqa: FBT003
        except Exception as err:  # noqa: BLE001
            LOGGER.debug("Dashboard %s has no loadable views: %s", path, err)
            continue
        for index, view in enumerate(config.get("views") or []):
            if not isinstance(view, dict):
                continue
            view_path = str(view.get("path") or index)
            view_title = view.get("title") or view_path
            options.append(
                selector.SelectOptionDict(
                    value=f"{path}/{view_path}",
                    label=f"{title} › {view_title}",  # noqa: RUF001
                )
            )
    return options


def _routine_schema(
    people: list[Any],
    current: dict[str, Any],
    dashboards: list[selector.SelectOptionDict],
) -> vol.Schema:
    person_options = [
        selector.SelectOptionDict(value=p.person_id, label=p.name) for p in people
    ]
    person_default = current.get(ROUTINE_PERSON_ID) or people[0].person_id
    return vol.Schema(
        {
            vol.Required(
                ROUTINE_PERSON_ID, default=person_default
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=person_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                ROUTINE_NAME, description=_suggested(current.get(ROUTINE_NAME))
            ): _text_selector(),
            # Multi-select keeps the order the user picks the segments in.
            vol.Required(
                ROUTINE_SEGMENTS, default=list(current.get(ROUTINE_SEGMENTS) or [])
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(SEGMENT_TYPES),
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key="segment_type",
                )
            ),
            vol.Required(
                ROUTINE_OUTPUT_FORMAT,
                default=current.get(ROUTINE_OUTPUT_FORMAT, DEFAULT_OUTPUT_FORMAT),
            ): _list_selector(OUTPUT_FORMATS, "output_format"),
            # custom_value keeps a typed path working, and a stored one that is
            # no longer listed (renamed dashboard) still shows.
            vol.Optional(
                ROUTINE_DASHBOARD,
                description=_suggested(
                    _join_dashboard(
                        current.get(ROUTINE_DASHBOARD_PATH),
                        current.get(ROUTINE_DASHBOARD_VIEW),
                    )
                ),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=dashboards,
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                ROUTINE_DASHBOARD_DISPLAY,
                description=_suggested(current.get(ROUTINE_DASHBOARD_DISPLAY)),
            ): _entity_selector("media_player"),
        }
    )


class AnnouncementSubentryFlow(config_entries.ConfigSubentryFlow):
    """Add or edit a household announcement."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Add an announcement."""
        return await self._async_step_form("user", None, user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Edit an existing announcement."""
        return await self._async_step_form(
            "reconfigure", self._get_reconfigure_subentry(), user_input
        )

    async def _async_step_form(
        self,
        step_id: str,
        subentry: config_entries.ConfigSubentry | None,
        user_input: dict[str, Any] | None,
    ) -> config_entries.SubentryFlowResult:
        entry = self._get_entry()
        errors: dict[str, str] = {}
        current = dict(subentry.data) if subentry else {}
        if user_input is not None:
            data = _announcement_data(user_input)
            current = data
            own_id = subentry.subentry_id if subentry else None
            errors = _validate_announcement(data)
            if not errors and any(
                other.announcement_id != own_id
                and slugify_name(other.name) == slugify_name(data[ANN_NAME])
                for other in get_announcements(entry)
            ):
                errors = {"base": "duplicate_announcement"}
            if not errors:
                # No reload: the update listener rebuilds what changed.
                if subentry is None:
                    return self.async_create_entry(title=data[ANN_NAME], data=data)
                return self.async_update_and_abort(
                    entry, subentry, title=data[ANN_NAME], data=data
                )
        return self.async_show_form(
            step_id=step_id, data_schema=_announcement_schema(current), errors=errors
        )


def _announcement_data(user_input: dict[str, Any]) -> dict[str, Any]:
    pictures = user_input.get(ANN_PICTURES) or []
    if isinstance(pictures, dict):
        pictures = [pictures]
    return {
        ANN_NAME: (user_input.get(ANN_NAME) or "").strip(),
        ANN_DESCRIPTION: (user_input.get(ANN_DESCRIPTION) or "").strip(),
        ANN_WORDING: user_input.get(ANN_WORDING) or "verbatim",
        ANN_MESSAGE: (user_input.get(ANN_MESSAGE) or "").strip(),
        ANN_FRESHNESS: user_input.get(ANN_FRESHNESS) or "static",
        ANN_PERSONAS: list(user_input.get(ANN_PERSONAS) or [DEFAULT_PERSONA]),
        ANN_REBUILD_TIME: user_input.get(ANN_REBUILD_TIME) or DEFAULT_REBUILD_TIME,
        ANN_PICTURES: [
            str(item.get("media_content_id"))
            for item in pictures
            if isinstance(item, dict) and item.get("media_content_id")
        ],
        ANN_OUTPUT_FORMAT: user_input.get(ANN_OUTPUT_FORMAT) or DEFAULT_OUTPUT_FORMAT,
        **({ANN_TARGET: target} if (target := user_input.get(ANN_TARGET)) else {}),
    }


def _validate_announcement(data: dict[str, Any]) -> dict[str, str]:
    if not is_valid_name(data[ANN_NAME]):
        return {"base": "invalid_name"}
    if data[ANN_WORDING] == "verbatim" and not data[ANN_MESSAGE]:
        return {ANN_MESSAGE: "message_required"}
    if data[ANN_FRESHNESS] == "static" and len(data[ANN_PERSONAS]) > 1:
        return {ANN_PERSONAS: "one_persona"}
    if len(data[ANN_PICTURES]) > MAX_PICTURES:
        return {ANN_PICTURES: "too_many_pictures"}
    if any(not p.startswith(LOCAL_MEDIA_PREFIX) for p in data[ANN_PICTURES]):
        return {ANN_PICTURES: "local_pictures_only"}
    return {}


def _list_selector(options: tuple[str, ...], key: str) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(options),
            mode=selector.SelectSelectorMode.LIST,
            translation_key=key,
        )
    )


def _announcement_schema(current: dict[str, Any]) -> vol.Schema:
    pictures = [
        {"media_content_id": media_id, "media_content_type": "image/jpeg"}
        for media_id in current.get(ANN_PICTURES) or []
    ]
    return vol.Schema(
        {
            vol.Required(
                ANN_NAME, description=_suggested(current.get(ANN_NAME))
            ): _text_selector(),
            vol.Required(
                ANN_DESCRIPTION, description=_suggested(current.get(ANN_DESCRIPTION))
            ): _text_selector(multiline=True),
            vol.Required(
                ANN_WORDING, default=current.get(ANN_WORDING, "verbatim")
            ): _list_selector(WORDINGS, "announcement_wording"),
            vol.Optional(
                ANN_MESSAGE, description=_suggested(current.get(ANN_MESSAGE))
            ): _text_selector(multiline=True),
            vol.Required(
                ANN_FRESHNESS, default=current.get(ANN_FRESHNESS, "static")
            ): _list_selector(FRESHNESS, "announcement_freshness"),
            vol.Optional(
                ANN_PERSONAS,
                default=list(current.get(ANN_PERSONAS) or [DEFAULT_PERSONA]),
            ): _persona_selector(None),
            vol.Optional(
                ANN_REBUILD_TIME,
                default=current.get(ANN_REBUILD_TIME, DEFAULT_REBUILD_TIME),
            ): selector.TimeSelector(),
            vol.Optional(
                ANN_PICTURES, description=_suggested(pictures or None)
            ): selector.MediaSelector(
                selector.MediaSelectorConfig(accept=["image/*"], multiple=True)
            ),
            vol.Required(
                ANN_OUTPUT_FORMAT,
                default=current.get(ANN_OUTPUT_FORMAT, DEFAULT_OUTPUT_FORMAT),
            ): _list_selector(OUTPUT_FORMATS, "output_format"),
            vol.Optional(
                ANN_TARGET, description=_suggested(current.get(ANN_TARGET))
            ): _entity_selector("media_player"),
        }
    )


class CastStudioOptionsFlowHandler(config_entries.OptionsFlowWithReload):
    """
    Edit the shared, install-wide settings.

    Per-person values (name, phonetic name, commute destination) live on the
    person subentries and are edited there (§5.1).
    """

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
        current_maps_api_key = options.get(
            CONF_MAPS_API_KEY, data.get(CONF_MAPS_API_KEY, "")
        )
        current_transport_mode = options.get(
            CONF_DEFAULT_TRANSPORTATION_MODE,
            data.get(CONF_DEFAULT_TRANSPORTATION_MODE, DEFAULT_TRANSPORTATION_MODE),
        )
        current_output_dir = options.get(
            CONF_OUTPUT_DIR, data.get(CONF_OUTPUT_DIR, DEFAULT_OUTPUT_DIR)
        )

        def _password(key: str, current: str) -> tuple[Any, selector.TextSelector]:
            marker = (
                vol.Optional(key, default=current) if current else vol.Optional(key)
            )
            return marker, selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            )

        schema_fields: dict[Any, Any] = dict(
            [
                _password(CONF_API_KEY, current_api_key),
                _password(CONF_MAPS_API_KEY, current_maps_api_key),
            ]
        )
        schema_fields[
            vol.Optional(
                CONF_DEFAULT_TRANSPORTATION_MODE,
                description={"suggested_value": current_transport_mode},
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(TRANSPORTATION_MODES),
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key="default_transportation_mode",
            ),
        )
        schema_fields[vol.Required(CONF_OUTPUT_DIR, default=current_output_dir)] = (
            _text_selector()
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_fields),
        )


# Backward-compatible aliases
AutomationGeneratorFlowHandler = CastStudioFlowHandler
AutomationGeneratorOptionsFlowHandler = CastStudioOptionsFlowHandler
