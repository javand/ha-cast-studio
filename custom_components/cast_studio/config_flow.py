"""Config flow for CastStudio integration."""

from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector, template

from .ai_client import async_discover_ai_credentials
from .announcement_layout import async_plan_announcement_layout
from .announcements import (
    ANN_AUDIO_MODE,
    ANN_CHIME_SOUND,
    ANN_DESCRIPTION,
    ANN_FRESHNESS,
    ANN_HOLD_SECONDS,
    ANN_MESSAGE,
    ANN_NAME,
    ANN_OUTPUT_FORMAT,
    ANN_PERSONAS,
    ANN_PICTURES,
    ANN_REBUILD_TIME,
    ANN_TARGET,
    ANN_TEMPLATE_IMAGE,
    ANN_TEMPLATE_LAYOUT,
    ANN_TEMPLATE_PROMPT,
    ANN_VISUAL_MODE,
    ANN_WORDING,
    AUDIO_MODES,
    DEFAULT_HOLD_SECONDS,
    DEFAULT_PERSONA,
    DEFAULT_REBUILD_TIME,
    FRESHNESS,
    LOCAL_MEDIA_PREFIX,
    MAX_PICTURES,
    SUBENTRY_ANNOUNCEMENT,
    VISUAL_MODES,
    WORDINGS,
    get_announcements,
    resolve_local_media_file,
)
from .const import (
    CONF_API_KEY,
    CONF_COMMUTE_DESTINATION,
    CONF_DEFAULT_TRANSPORTATION_MODE,
    CONF_IMAGE_MODEL,
    CONF_MAPS_API_KEY,
    CONF_OUTPUT_DIR,
    CONF_PHONETIC_NAME,
    CONF_RECIPIENT_NAME,
    CONF_TEXT_MODEL,
    CONF_VOICE_MODEL,
    DEFAULT_IMAGE_MODEL,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_TEXT_MODEL,
    DEFAULT_TRANSPORTATION_MODE,
    DEFAULT_VOICE_MODEL,
    DOMAIN,
    IMAGE_MODELS,
    LOGGER,
    TEXT_MODELS,
    VOICE_MODELS,
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
    PERSON_MAX_AGE_MINUTES,
    PERSON_MEDIA_PLAYER,
    PERSON_NAME,
    PERSON_PHONETIC_NAME,
    PERSON_PRONUNCIATIONS,
    PERSON_RESET_TIME,
    PERSON_SETTINGS_KEYS,
    PERSON_TODOS,
    PERSON_TRANSPORTATION_MODE,
    PERSON_VOICE,
    ROUTINE_DASHBOARD,
    ROUTINE_DASHBOARD_DISPLAY,
    ROUTINE_DASHBOARD_PATH,
    ROUTINE_DASHBOARD_VIEW,
    ROUTINE_NAME,
    ROUTINE_OUTPUT_FORMAT,
    ROUTINE_PERSON_ID,
    ROUTINE_REFRESH_SEGMENTS,
    ROUTINE_SEGMENTS,
    ROUTINE_VOICE_CHANGES,
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
    person_todos,
    person_voice,
    slugify_name,
)
from .personas import (
    DEFAULT_VOICE_PERSONA,
    PERSONAS,
    get_persona,
)
from .segments import default_refresh_segments
from .slide_renderer import render_template_slide

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


def _cast_display_selector() -> selector.EntitySelector:
    """Pick a Google Cast media player: only Cast can show a dashboard."""
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain="media_player", integration="cast")
    )


def _entity_selector(domain: str | None = None) -> selector.EntitySelector:
    if domain is None:
        return selector.EntitySelector(selector.EntitySelectorConfig())
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))


def _persona_selector() -> selector.SelectSelector:
    """Pick one or more of the built-in personas."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(
                    value=persona.persona_id,
                    label=f"{persona.name}: {persona.summary}",
                )
                for persona in PERSONAS
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
        PERSON_TODOS: selector.EntitySelector(
            selector.EntitySelectorConfig(domain="todo", multiple=True)
        ),
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
        "conversation_agent": selector.ConversationAgentSelector(),
        "greeting_title": _text_selector(),
        "greeting_subtitle": _text_selector(),
        "greeting_image": selector.MediaSelector(
            selector.MediaSelectorConfig(accept=["image/*"])
        ),
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
        PERSON_TODOS: person_todos(current) or None,
        # A path typed before the image picker would fail the picker's validation.
        "greeting_image": (
            current.get("greeting_image")
            if isinstance(current.get("greeting_image"), dict)
            else None
        ),
        # A persona dropped from the catalog would fail the picker's validation.
        # With none picked, show the persona who speaks: the default voice.
        PERSON_VOICE: [pid for pid in person_voice(current) if get_persona(pid)]
        or [DEFAULT_VOICE_PERSONA],
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
                PERSON_VOICE, description=_suggested(current[PERSON_VOICE])
            ): _persona_selector(),
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
    data.pop(PERSON_VOICE, None)
    if voice := user_input.get(PERSON_VOICE):
        data[PERSON_VOICE] = voice
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
        # A form can't follow the segment chips live, so an edit offers the
        # segments the routine had when the form opened.
        segment_choices = (
            list(subentry.data.get(ROUTINE_SEGMENTS) or [])
            if subentry
            else list(SEGMENT_TYPES)
        )
        if user_input is not None:
            data = _routine_data(user_input)
            current = data
            own_id = subentry.subentry_id if subentry else None
            stray = [
                change
                for change in data[ROUTINE_VOICE_CHANGES]
                if change["segment"] not in data[ROUTINE_SEGMENTS]
            ]
            stray_refresh = [
                segment
                for segment in data[ROUTINE_REFRESH_SEGMENTS]
                if segment not in data[ROUTINE_SEGMENTS]
            ]
            if subentry is not None:
                # Removing a segment from a routine drops its voice change and
                # its refresh.
                data[ROUTINE_VOICE_CHANGES] = [
                    change
                    for change in data[ROUTINE_VOICE_CHANGES]
                    if change not in stray
                ]
                data[ROUTINE_REFRESH_SEGMENTS] = [
                    segment
                    for segment in data[ROUTINE_REFRESH_SEGMENTS]
                    if segment not in stray_refresh
                ]
            if not is_valid_name(data[ROUTINE_NAME]):
                errors["base"] = "invalid_name"
            elif not data[ROUTINE_SEGMENTS]:
                errors[ROUTINE_SEGMENTS] = "no_segments"
            elif subentry is None and stray:
                errors["base"] = "voice_change_not_in_routine"
            elif subentry is None and stray_refresh:
                errors["base"] = "refresh_not_in_routine"
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
                people,
                current,
                await async_dashboard_options(self.hass),
                segment_choices,
            ),
            errors=errors,
        )


def _routine_data(user_input: dict[str, Any]) -> dict[str, Any]:
    advanced = user_input.get(ADVANCED_SECTION) or {}
    return {
        ROUTINE_PERSON_ID: user_input[ROUTINE_PERSON_ID],
        ROUTINE_NAME: (user_input.get(ROUTINE_NAME) or "").strip(),
        ROUTINE_SEGMENTS: list(user_input.get(ROUTINE_SEGMENTS) or []),
        ROUTINE_OUTPUT_FORMAT: user_input.get(ROUTINE_OUTPUT_FORMAT)
        or DEFAULT_OUTPUT_FORMAT,
        **_split_dashboard(user_input.get(ROUTINE_DASHBOARD)),
        ROUTINE_DASHBOARD_DISPLAY: user_input.get(ROUTINE_DASHBOARD_DISPLAY) or None,
        ROUTINE_VOICE_CHANGES: [
            {"segment": row["segment"], "personas": list(row["personas"])}
            for row in advanced.get(ROUTINE_VOICE_CHANGES) or []
            if isinstance(row, dict) and row.get("segment") and row.get("personas")
        ],
        # Not shown yet (a new routine, or the section never opened): the
        # default, weather and commute.
        ROUTINE_REFRESH_SEGMENTS: _refresh_segments(
            advanced.get(ROUTINE_REFRESH_SEGMENTS),
            user_input.get(ROUTINE_SEGMENTS) or [],
        ),
    }


def _refresh_suggestion(current: dict[str, Any]) -> dict[str, Any] | None:
    """
    Pre-fill Refresh segments: the saved list (even empty), else the default.

    A new routine gets none, so an untouched picker saves the default.
    """
    if ROUTINE_REFRESH_SEGMENTS in current:
        return {"suggested_value": list(current[ROUTINE_REFRESH_SEGMENTS])}
    if segments := current.get(ROUTINE_SEGMENTS):
        return {"suggested_value": default_refresh_segments(segments)}
    return None


def _refresh_segments(picked: list[str] | None, segments: list[str]) -> list[str]:
    """Order the picked refresh segments like the routine; None: the default."""
    if picked is None:
        return default_refresh_segments(segments)
    order = list(dict.fromkeys(segments))
    known = [segment for segment in order if segment in picked]
    return known + [segment for segment in picked if segment not in order]


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
    segment_choices: list[str],
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
            ): _cast_display_selector(),
            vol.Optional(ADVANCED_SECTION): section(
                vol.Schema(
                    {
                        vol.Optional(
                            ROUTINE_VOICE_CHANGES,
                            description=_suggested(
                                current.get(ROUTINE_VOICE_CHANGES) or None
                            ),
                        ): _voice_changes_selector(segment_choices),
                        vol.Optional(
                            ROUTINE_REFRESH_SEGMENTS,
                            description=_refresh_suggestion(current),
                        ): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=segment_choices,
                                multiple=True,
                                mode=selector.SelectSelectorMode.DROPDOWN,
                                translation_key="segment_type",
                            )
                        ),
                    }
                ),
                {"collapsed": True},
            ),
        }
    )


def _voice_changes_selector(segment_choices: list[str]) -> selector.ObjectSelector:
    """Rows of one routine segment and the persona(s) that voice it."""
    return selector.ObjectSelector(
        selector.ObjectSelectorConfig(
            multiple=True,
            fields={
                "segment": {
                    "selector": {
                        "select": {
                            "options": segment_choices,
                            "translation_key": "segment_type",
                        }
                    },
                    "required": True,
                },
                "personas": {
                    "selector": {"select": _persona_selector().config},
                    "required": True,
                },
            },
            label_field="segment",
            translation_key=ROUTINE_VOICE_CHANGES,
        )
    )


class AnnouncementSubentryFlow(config_entries.ConfigSubentryFlow):
    """Add or edit a household announcement."""

    _staged_data: dict[str, Any] | None = None
    _staged_subentry: config_entries.ConfigSubentry | None = None

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

    async def async_step_preview(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Show preview and confirm creation/update."""
        if user_input is not None:
            data = getattr(self, "_staged_data", None)
            if data is None:
                return self.async_abort(reason="unknown_error")
            subentry = getattr(self, "_staged_subentry", None)
            entry = self._get_entry()
            if subentry is None:
                return self.async_create_entry(title=data[ANN_NAME], data=data)
            return self.async_update_and_abort(
                entry, subentry, title=data[ANN_NAME], data=data
            )

        preview_url = f"/local/cast_studio_previews/preview_{self.flow_id}.jpg"
        return self.async_show_form(
            step_id="preview",
            data_schema=vol.Schema({}),
            description_placeholders={"preview_url": preview_url},
        )

    async def _async_plan_and_show_preview(
        self,
        entry: config_entries.ConfigEntry,
        subentry: config_entries.ConfigSubentry | None,
        data: dict[str, Any],
    ) -> config_entries.SubentryFlowResult:
        """Plan layout via Gemini and show preview step."""
        template_img_id = data.get(ANN_TEMPLATE_IMAGE)
        bg_path = (
            resolve_local_media_file(self.hass, template_img_id)
            if template_img_id
            else None
        )
        if bg_path is None or not Path(bg_path).is_file():
            return self.async_show_form(
                step_id="user" if subentry is None else "reconfigure",
                data_schema=_announcement_schema(data),
                errors={ANN_TEMPLATE_IMAGE: "template_image_required"},
            )

        image_path = Path(bg_path)
        image_bytes = await self.hass.async_add_executor_job(image_path.read_bytes)
        suffix = image_path.suffix.lower()
        image_mime = (
            "image/png"
            if suffix == ".png"
            else "image/webp"
            if suffix == ".webp"
            else "image/jpeg"
        )

        ai_creds = await async_discover_ai_credentials(self.hass)
        if explicit_key := (
            entry.options.get(CONF_API_KEY) or entry.data.get(CONF_API_KEY)
        ):
            ai_creds["api_key"] = explicit_key

        try:
            layout = await async_plan_announcement_layout(
                hass=self.hass,
                image_bytes=image_bytes,
                image_mime=image_mime,
                prompt=data.get(ANN_TEMPLATE_PROMPT, ""),
                ai_creds=ai_creds,
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to plan announcement layout with AI")
            return self.async_show_form(
                step_id="user" if subentry is None else "reconfigure",
                data_schema=_announcement_schema(data),
                errors={"base": "template_planning_failed"},
            )

        # Render preview slide with live Jinja rendering
        evaluated_elements = []
        for el in layout.get("elements", []):
            tmpl = el.get("template", "")
            try:
                rendered_text = template.Template(tmpl, self.hass).async_render(
                    parse_result=False
                )
            except Exception as err:  # noqa: BLE001
                LOGGER.warning("Failed to render preview template '%s': %s", tmpl, err)
                rendered_text = tmpl
            elem_copy = dict(el)
            elem_copy["text"] = str(rendered_text)
            evaluated_elements.append(elem_copy)

        preview_dir = Path(self.hass.config.path("www", "cast_studio_previews"))
        preview_file = preview_dir / f"preview_{self.flow_id}.jpg"

        def _render_preview() -> None:
            preview_dir.mkdir(parents=True, exist_ok=True)
            render_template_slide(bg_path, evaluated_elements, preview_file)

        await self.hass.async_add_executor_job(_render_preview)

        data[ANN_TEMPLATE_LAYOUT] = layout
        self._staged_data = data
        self._staged_subentry = subentry

        return await self.async_step_preview()

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
                prompt_changed = subentry is not None and (
                    subentry.data.get(ANN_TEMPLATE_PROMPT)
                    != data.get(ANN_TEMPLATE_PROMPT)
                    or subentry.data.get(ANN_TEMPLATE_IMAGE)
                    != data.get(ANN_TEMPLATE_IMAGE)
                )
                if (
                    subentry
                    and not data.get(ANN_TEMPLATE_LAYOUT)
                    and subentry.data.get(ANN_TEMPLATE_LAYOUT)
                    and not prompt_changed
                ):
                    data[ANN_TEMPLATE_LAYOUT] = subentry.data[ANN_TEMPLATE_LAYOUT]

                if data.get(ANN_VISUAL_MODE) == "template" and not data.get(
                    ANN_TEMPLATE_LAYOUT
                ):
                    return await self._async_plan_and_show_preview(
                        entry, subentry, data
                    )

                # No reload: the update listener rebuilds what changed.
                if subentry is None:
                    return self.async_create_entry(title=data[ANN_NAME], data=data)
                return self.async_update_and_abort(
                    entry, subentry, title=data[ANN_NAME], data=data
                )
        return self.async_show_form(
            step_id=step_id, data_schema=_announcement_schema(current), errors=errors
        )


def _extract_media_id(val: Any) -> str | None:
    if isinstance(val, dict):
        return val.get("media_content_id")
    if isinstance(val, str) and val:
        return val
    return None


def _announcement_data(user_input: dict[str, Any]) -> dict[str, Any]:
    pictures = user_input.get(ANN_PICTURES) or []
    if isinstance(pictures, dict):
        pictures = [pictures]
    data: dict[str, Any] = {
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
    if ANN_VISUAL_MODE in user_input:
        data[ANN_VISUAL_MODE] = user_input[ANN_VISUAL_MODE]
    if (
        template_image := _extract_media_id(user_input.get(ANN_TEMPLATE_IMAGE))
    ) is not None:
        data[ANN_TEMPLATE_IMAGE] = template_image
    if user_input.get(ANN_TEMPLATE_PROMPT):
        data[ANN_TEMPLATE_PROMPT] = user_input[ANN_TEMPLATE_PROMPT].strip()
    if user_input.get(ANN_TEMPLATE_LAYOUT):
        data[ANN_TEMPLATE_LAYOUT] = user_input[ANN_TEMPLATE_LAYOUT]
    if ANN_AUDIO_MODE in user_input:
        data[ANN_AUDIO_MODE] = user_input[ANN_AUDIO_MODE]
    if (chime_sound := _extract_media_id(user_input.get(ANN_CHIME_SOUND))) is not None:
        data[ANN_CHIME_SOUND] = chime_sound
    if ANN_HOLD_SECONDS in user_input and user_input[ANN_HOLD_SECONDS] is not None:
        data[ANN_HOLD_SECONDS] = float(user_input[ANN_HOLD_SECONDS])
    return data


def _validate_announcement(data: dict[str, Any]) -> dict[str, str]:  # noqa: PLR0911
    if not is_valid_name(data[ANN_NAME]):
        return {"base": "invalid_name"}
    if (
        data[ANN_WORDING] == "verbatim"
        and not data[ANN_MESSAGE]
        and data.get(ANN_AUDIO_MODE, "voice") != "sound"
    ):
        return {ANN_MESSAGE: "message_required"}
    if data[ANN_FRESHNESS] == "static" and len(data[ANN_PERSONAS]) > 1:
        return {ANN_PERSONAS: "one_persona"}
    if len(data[ANN_PICTURES]) > MAX_PICTURES:
        return {ANN_PICTURES: "too_many_pictures"}
    if any(not p.startswith(LOCAL_MEDIA_PREFIX) for p in data[ANN_PICTURES]):
        return {ANN_PICTURES: "local_pictures_only"}
    if data.get(ANN_VISUAL_MODE) == "template" and not data.get(ANN_TEMPLATE_IMAGE):
        return {ANN_TEMPLATE_IMAGE: "template_image_required"}
    if data.get(ANN_VISUAL_MODE) == "template" and not data.get(ANN_TEMPLATE_PROMPT):
        return {ANN_TEMPLATE_PROMPT: "template_prompt_required"}
    if data.get(ANN_AUDIO_MODE) in ("sound", "chime_and_voice") and not data.get(
        ANN_CHIME_SOUND
    ):
        return {ANN_CHIME_SOUND: "chime_sound_required"}
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
    template_image = (
        {
            "media_content_id": current[ANN_TEMPLATE_IMAGE],
            "media_content_type": "image/jpeg",
        }
        if current.get(ANN_TEMPLATE_IMAGE)
        else None
    )
    chime_sound = (
        {
            "media_content_id": current[ANN_CHIME_SOUND],
            "media_content_type": "audio/mpeg",
        }
        if current.get(ANN_CHIME_SOUND)
        else None
    )
    return vol.Schema(
        {
            vol.Required(
                ANN_NAME, description=_suggested(current.get(ANN_NAME))
            ): _text_selector(),
            vol.Required(
                ANN_DESCRIPTION, description=_suggested(current.get(ANN_DESCRIPTION))
            ): _text_selector(multiline=True),
            vol.Required(
                ANN_VISUAL_MODE, default=current.get(ANN_VISUAL_MODE, "ai_painted")
            ): _list_selector(VISUAL_MODES, "announcement_visual_mode"),
            vol.Optional(
                ANN_TEMPLATE_IMAGE, description=_suggested(template_image)
            ): selector.MediaSelector(selector.MediaSelectorConfig(accept=["image/*"])),
            vol.Optional(
                ANN_TEMPLATE_PROMPT,
                description=_suggested(current.get(ANN_TEMPLATE_PROMPT)),
            ): _text_selector(multiline=True),
            vol.Required(
                ANN_AUDIO_MODE, default=current.get(ANN_AUDIO_MODE, "voice")
            ): _list_selector(AUDIO_MODES, "announcement_audio_mode"),
            vol.Optional(
                ANN_CHIME_SOUND, description=_suggested(chime_sound)
            ): selector.MediaSelector(selector.MediaSelectorConfig(accept=["audio/*"])),
            vol.Optional(
                ANN_HOLD_SECONDS,
                default=float(current.get(ANN_HOLD_SECONDS, DEFAULT_HOLD_SECONDS)),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=60,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="seconds",
                )
            ),
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
            ): _persona_selector(),
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
        for key, default, known in (
            (CONF_TEXT_MODEL, DEFAULT_TEXT_MODEL, TEXT_MODELS),
            (CONF_IMAGE_MODEL, DEFAULT_IMAGE_MODEL, IMAGE_MODELS),
            (CONF_VOICE_MODEL, DEFAULT_VOICE_MODEL, VOICE_MODELS),
        ):
            current = options.get(key, data.get(key)) or default
            schema_fields[
                vol.Optional(key, description={"suggested_value": current})
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(dict.fromkeys((*known, current))),
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_fields),
        )
