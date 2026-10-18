"""Tests for CastStudio blueprints packaging and schema verification."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.components.blueprint.models import Blueprint

MORNING_BRIEFING_INPUT_COUNT = 15
BEDTIME_ROUTINE_INPUT_COUNT = 8
EVENT_ALERT_INPUT_COUNT = 9
WELCOME_HOME_INPUT_COUNT = 8
EXPECTED_TOTAL_BLUEPRINTS = 4

BLUEPRINTS_DIR = Path("blueprints/automation/cast_studio")
MORNING_BRIEFING_PATH = BLUEPRINTS_DIR / "morning_briefing.yaml"
BEDTIME_ROUTINE_PATH = BLUEPRINTS_DIR / "bedtime_routine.yaml"
EVENT_ALERT_PATH = BLUEPRINTS_DIR / "event_alert.yaml"
WELCOME_HOME_PATH = BLUEPRINTS_DIR / "welcome_home.yaml"

ALL_BLUEPRINT_PATHS = [
    MORNING_BRIEFING_PATH,
    BEDTIME_ROUTINE_PATH,
    EVENT_ALERT_PATH,
    WELCOME_HOME_PATH,
]


# Register custom yaml constructor for !input tag so standard yaml.safe_load succeeds
def _input_constructor(loader: yaml.SafeLoader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return None


yaml.SafeLoader.add_constructor("!input", _input_constructor)


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_morning_briefing_blueprint_structure():
    """Verify morning briefing blueprint exists, parses, and has required inputs and metadata."""
    assert os.path.exists(MORNING_BRIEFING_PATH), f"Missing {MORNING_BRIEFING_PATH}"
    bp = _load_yaml(MORNING_BRIEFING_PATH)

    meta = bp["blueprint"]
    assert meta["domain"] == "automation"
    assert meta["name"] == "CastStudio - Morning Briefing"
    assert meta["author"] == "Javan Dicks (@javand)"
    assert meta["homeassistant"]["min_version"] == "2024.10.0"
    assert (
        meta["source_url"]
        == "https://github.com/javand/ha-cast-studio/blob/main/blueprints/automation/cast_studio/morning_briefing.yaml"
    )

    inputs = meta["input"]
    expected_inputs = [
        "pregenerate_time",
        "wake_trigger",
        "recipient_name",
        "phonetic_name",
        "target_player",
        "weather_entity",
        "calendar_entities",
        "todo_entity",
        "scripture_sensor",
        "host_voice",
        "host_personality",
        "scripture_voice",
        "scripture_personality",
        "conversation_agent",
        "background_actions",
    ]
    for inp in expected_inputs:
        assert inp in inputs, f"Input '{inp}' missing from morning briefing blueprint"

    # Verify input defaults & selectors
    assert inputs["pregenerate_time"]["default"] == "04:30:00"
    assert "time" in inputs["pregenerate_time"]["selector"]

    assert "trigger" in inputs["wake_trigger"]["selector"]

    assert inputs["recipient_name"]["default"] == "Javan"
    assert "text" in inputs["recipient_name"]["selector"]

    assert inputs["phonetic_name"]["default"] == "Jay-ven"
    assert "text" in inputs["phonetic_name"]["selector"]

    assert "media_player" in str(inputs["target_player"]["selector"])
    assert "weather" in str(inputs["weather_entity"]["selector"])
    assert "calendar" in str(inputs["calendar_entities"]["selector"])
    assert inputs["calendar_entities"]["selector"]["entity"].get("multiple") is True
    assert "todo" in str(inputs["todo_entity"]["selector"])
    assert "sensor" in str(inputs["scripture_sensor"]["selector"])

    # Optional entity selectors should not have empty-string default
    assert "default" not in inputs["todo_entity"]
    assert "default" not in inputs["scripture_sensor"]
    assert "default" not in inputs["conversation_agent"]

    assert inputs["host_voice"]["default"] == "Pulcherrima"
    assert inputs["host_personality"]["default"] == "news_anchor"
    assert inputs["scripture_voice"]["default"] == "Enceladus"
    assert inputs["scripture_personality"]["default"] == "life_coach"
    assert "conversation_agent" in inputs["conversation_agent"]["selector"]
    assert "action" in inputs["background_actions"]["selector"]
    assert inputs["background_actions"]["default"] == []


def test_morning_briefing_triggers_and_actions():
    """Verify morning briefing triggers, mode, and choose action branches."""
    bp = _load_yaml(MORNING_BRIEFING_PATH)

    assert bp.get("mode") == "single"
    assert bp.get("max_exceeded") == "silent"

    # Verify triggers: pregenerate time trigger has id 'pregenerate', wake_trigger uses triggers: !input
    triggers = bp.get("trigger") or bp.get("triggers", [])
    assert isinstance(triggers, list)
    trigger_ids = [t.get("id") for t in triggers if isinstance(t, dict) and "id" in t]
    assert "pregenerate" in trigger_ids

    # wake_trigger referenced without id alongside triggers:
    triggers_str = str(triggers)
    assert "wake_trigger" in triggers_str

    # Verify choose action
    actions = bp.get("action") or bp.get("actions", [])
    assert isinstance(actions, list)
    choose_block = None
    for a in actions:
        if isinstance(a, dict) and "choose" in a:
            choose_block = a
            break
    assert choose_block is not None, "Choose action block missing in morning briefing"

    # Identify pregenerate branch in choose conditions
    choose_branches = choose_block["choose"]
    found_pregen = False
    for branch in choose_branches:
        branch_str = str(branch)
        if "pregenerate" in branch_str and "pregenerate_routine" in branch_str:
            found_pregen = True
    assert found_pregen, "pregenerate branch with pregenerate_routine call missing"
    assert "cast_studio.pregenerate_routine" in str(choose_branches)

    # Identify run_routine in default branch
    default_branch = choose_block.get("default")
    assert default_branch is not None, "Default branch missing from choose action"
    default_str = str(default_branch)
    assert "cast_studio.run_routine" in default_str
    assert "morning_briefing" in default_str


def test_morning_briefing_ha_blueprint_schema():
    """Validate morning briefing against Home Assistant core blueprint schema."""
    data = _load_yaml(MORNING_BRIEFING_PATH)
    ha_bp = Blueprint(data, schema=AUTOMATION_BLUEPRINT_SCHEMA)
    assert ha_bp.domain == "automation"
    assert ha_bp.name == "CastStudio - Morning Briefing"
    assert len(ha_bp.inputs) == MORNING_BRIEFING_INPUT_COUNT


def test_bedtime_routine_blueprint_structure():
    """Verify bedtime routine blueprint exists, parses, and has required inputs and metadata."""
    assert os.path.exists(BEDTIME_ROUTINE_PATH), f"Missing {BEDTIME_ROUTINE_PATH}"
    bp = _load_yaml(BEDTIME_ROUTINE_PATH)

    meta = bp["blueprint"]
    assert meta["domain"] == "automation"
    assert meta["name"] == "CastStudio - Bedtime Routine"
    assert meta["author"] == "Javan Dicks (@javand)"
    assert meta["homeassistant"]["min_version"] == "2024.10.0"
    assert (
        meta["source_url"]
        == "https://github.com/javand/ha-cast-studio/blob/main/blueprints/automation/cast_studio/bedtime_routine.yaml"
    )

    inputs = meta["input"]
    expected_inputs = [
        "bedtime_trigger",
        "recipient_name",
        "phonetic_name",
        "target_player",
        "bedtime_voice",
        "bedtime_personality",
        "background_shutdown_actions",
        "night_display_action",
    ]
    for inp in expected_inputs:
        assert inp in inputs, f"Input '{inp}' missing from bedtime routine blueprint"

    assert "trigger" in inputs["bedtime_trigger"]["selector"]
    assert inputs["recipient_name"]["default"] == "Javan"
    assert inputs["phonetic_name"]["default"] == "Jay-ven"
    assert "media_player" in str(inputs["target_player"]["selector"])
    assert inputs["bedtime_voice"]["default"] == "Enceladus"
    assert inputs["bedtime_personality"]["default"] == "calm_reassuring"
    assert "action" in inputs["background_shutdown_actions"]["selector"]
    assert inputs["background_shutdown_actions"]["default"] == []
    assert "action" in inputs["night_display_action"]["selector"]
    assert inputs["night_display_action"]["default"] == []


def test_bedtime_routine_triggers_and_actions():
    """Verify bedtime routine trigger and action call."""
    bp = _load_yaml(BEDTIME_ROUTINE_PATH)

    assert bp.get("mode") in ("restart", "queued")

    # Verify triggers
    triggers = bp.get("trigger") or bp.get("triggers", [])
    assert isinstance(triggers, list)
    trigger_str = str(triggers)
    assert "bedtime_trigger" in trigger_str

    # Verify action calls run_routine
    actions = bp.get("action") or bp.get("actions", [])
    assert isinstance(actions, list)
    action_str = str(actions)
    assert "cast_studio.run_routine" in action_str
    assert "bedtime_routine" in action_str


def test_bedtime_routine_ha_blueprint_schema():
    """Validate bedtime routine against Home Assistant core blueprint schema."""
    data = _load_yaml(BEDTIME_ROUTINE_PATH)
    ha_bp = Blueprint(data, schema=AUTOMATION_BLUEPRINT_SCHEMA)
    assert ha_bp.domain == "automation"
    assert ha_bp.name == "CastStudio - Bedtime Routine"
    assert len(ha_bp.inputs) == BEDTIME_ROUTINE_INPUT_COUNT


def test_event_alert_blueprint_structure():
    """Verify event alert blueprint exists, parses, and has required inputs and metadata."""
    assert os.path.exists(EVENT_ALERT_PATH), f"Missing {EVENT_ALERT_PATH}"
    bp = _load_yaml(EVENT_ALERT_PATH)

    meta = bp["blueprint"]
    assert meta["domain"] == "automation"
    assert meta["name"] == "CastStudio - Reusable Event Alert"
    assert meta["author"] == "Javan Dicks (@javand)"
    assert meta["homeassistant"]["min_version"] == "2024.10.0"
    assert (
        meta["source_url"]
        == "https://github.com/javand/ha-cast-studio/blob/main/blueprints/automation/cast_studio/event_alert.yaml"
    )
    assert "zero-latency visual alert clip" in meta["description"]

    inputs = meta["input"]
    expected_inputs = [
        "alert_trigger",
        "clip_name",
        "target_player",
        "alert_title",
        "alert_message",
        "image_asset",
        "alert_voice",
        "alert_personality",
        "background_actions",
    ]
    for inp in expected_inputs:
        assert inp in inputs, f"Input '{inp}' missing from event alert blueprint"

    assert "trigger" in inputs["alert_trigger"]["selector"]
    assert inputs["clip_name"]["default"] == "freezer_alert"
    assert "text" in inputs["clip_name"]["selector"]

    assert "media_player" in str(inputs["target_player"]["selector"])
    assert inputs["alert_title"]["default"] == "Freezer Door Open!"
    assert "text" in inputs["alert_title"]["selector"]

    assert (
        inputs["alert_message"]["default"]
        == "Attention: The kitchen freezer door has been left open."
    )
    assert "text" in inputs["alert_message"]["selector"]

    assert inputs["image_asset"]["default"] == "dripping_ice.png"
    assert "text" in inputs["image_asset"]["selector"]

    assert inputs["alert_voice"]["default"] == "Pulcherrima"
    assert "select" in inputs["alert_voice"]["selector"]
    assert "Pulcherrima" in inputs["alert_voice"]["selector"]["select"]["options"]
    assert "Enceladus" in inputs["alert_voice"]["selector"]["select"]["options"]

    assert inputs["alert_personality"]["default"] == "news_anchor"
    assert "select" in inputs["alert_personality"]["selector"]

    assert "action" in inputs["background_actions"]["selector"]
    assert inputs["background_actions"]["default"] == []


def test_event_alert_triggers_and_actions():
    """Verify event alert triggers, mode, and action payload."""
    bp = _load_yaml(EVENT_ALERT_PATH)

    assert bp.get("mode") in ("restart", "queued", "single")

    triggers = bp.get("trigger") or bp.get("triggers", [])
    assert isinstance(triggers, list)
    trigger_str = str(triggers)
    assert "alert_trigger" in trigger_str

    actions = bp.get("action") or bp.get("actions", [])
    assert isinstance(actions, list)
    assert actions[0]["action"] == "cast_studio.run_routine"
    r_config = actions[0]["data"]["routine_config"]
    assert r_config["static"] is True
    assert r_config["title"] == "alert_title"
    assert r_config["message"] == "alert_message"
    assert r_config["image_path"] == "image_asset"
    assert r_config["voice"] == "alert_voice"
    assert r_config["voice_personality"] == "alert_personality"
    assert r_config["background_actions"] == "background_actions"
    assert r_config["actions"] == "background_actions"


def test_event_alert_ha_blueprint_schema():
    """Validate event alert against Home Assistant core blueprint schema."""
    data = _load_yaml(EVENT_ALERT_PATH)
    ha_bp = Blueprint(data, schema=AUTOMATION_BLUEPRINT_SCHEMA)
    assert ha_bp.domain == "automation"
    assert ha_bp.name == "CastStudio - Reusable Event Alert"
    assert len(ha_bp.inputs) == EVENT_ALERT_INPUT_COUNT


def test_welcome_home_blueprint_structure():
    """Verify welcome home blueprint exists, parses, and has required inputs and metadata."""
    assert os.path.exists(WELCOME_HOME_PATH), f"Missing {WELCOME_HOME_PATH}"
    bp = _load_yaml(WELCOME_HOME_PATH)

    meta = bp["blueprint"]
    assert meta["domain"] == "automation"
    assert meta["name"] == "CastStudio - Welcome Home Routine"
    assert meta["author"] == "Javan Dicks (@javand)"
    assert meta["homeassistant"]["min_version"] == "2024.10.0"
    assert (
        meta["source_url"]
        == "https://github.com/javand/ha-cast-studio/blob/main/blueprints/automation/cast_studio/welcome_home.yaml"
    )
    assert "greeting" in meta["description"].lower()

    inputs = meta["input"]
    expected_inputs = [
        "person_entity",
        "recipient_name",
        "phonetic_name",
        "target_player",
        "avatar_image",
        "welcome_voice",
        "welcome_personality",
        "welcome_actions",
    ]
    for inp in expected_inputs:
        assert inp in inputs, f"Input '{inp}' missing from welcome home blueprint"

    assert "entity" in inputs["person_entity"]["selector"]
    assert inputs["person_entity"]["selector"]["entity"]["domain"] == "person"
    assert inputs["recipient_name"]["default"] == ""
    assert "text" in inputs["recipient_name"]["selector"]

    assert inputs["phonetic_name"]["default"] == ""
    assert "text" in inputs["phonetic_name"]["selector"]

    assert "media_player" in str(inputs["target_player"]["selector"])
    assert inputs["avatar_image"]["default"] == "welcome_avatar.png"
    assert "text" in inputs["avatar_image"]["selector"]

    assert inputs["welcome_voice"]["default"] == "Pulcherrima"
    assert "select" in inputs["welcome_voice"]["selector"]
    assert "Pulcherrima" in inputs["welcome_voice"]["selector"]["select"]["options"]
    assert "Enceladus" in inputs["welcome_voice"]["selector"]["select"]["options"]

    assert inputs["welcome_personality"]["default"] == "life_coach"
    assert "select" in inputs["welcome_personality"]["selector"]

    assert "action" in inputs["welcome_actions"]["selector"]
    assert inputs["welcome_actions"]["default"] == []


def test_welcome_home_triggers_and_actions():
    """Verify welcome home triggers, mode, and action payload."""
    bp = _load_yaml(WELCOME_HOME_PATH)

    assert bp.get("mode") == "single"
    assert bp.get("max_exceeded") == "silent"

    triggers = bp.get("trigger") or bp.get("triggers", [])
    assert isinstance(triggers, list)
    assert triggers[0]["trigger"] == "state"
    assert triggers[0]["entity_id"] == "person_entity"
    assert triggers[0]["to"] == "home"

    actions = bp.get("action") or bp.get("actions", [])
    assert isinstance(actions, list)
    assert actions[0]["action"] == "cast_studio.run_routine"
    assert actions[0]["data"]["routine_name"].startswith("welcome_home")
    assert actions[0]["data"].get("force_regenerate") is True
    assert (
        actions[0]["data"]["routine_config"]["background_actions"] == "welcome_actions"
    )


def test_welcome_home_ha_blueprint_schema():
    """Validate welcome home against Home Assistant core blueprint schema."""
    data = _load_yaml(WELCOME_HOME_PATH)
    ha_bp = Blueprint(data, schema=AUTOMATION_BLUEPRINT_SCHEMA)
    assert ha_bp.domain == "automation"
    assert ha_bp.name == "CastStudio - Welcome Home Routine"
    assert len(ha_bp.inputs) == WELCOME_HOME_INPUT_COUNT


def test_all_four_blueprints_exist_and_validate():
    """Verify all 4 blueprints exist in blueprints/automation/cast_studio and pass schema."""
    assert len(ALL_BLUEPRINT_PATHS) == EXPECTED_TOTAL_BLUEPRINTS
    for path in ALL_BLUEPRINT_PATHS:
        assert path.exists(), f"Blueprint file does not exist: {path}"
        data = _load_yaml(path)
        ha_bp = Blueprint(data, schema=AUTOMATION_BLUEPRINT_SCHEMA)
        assert ha_bp.domain == "automation"
        assert len(ha_bp.inputs) > 0
