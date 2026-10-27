"""Tests for the built-in persona catalog and the daily pick."""

from __future__ import annotations

import re
from datetime import date, timedelta

from custom_components.cast_studio.personas import (
    PERSONAS,
    ROLE_HOST,
    ROLE_READER,
    get_persona,
    personas_for_role,
    pick_persona,
)
from custom_components.cast_studio.tts_client import PERSONALITY_PROMPTS

DAY = date(2026, 1, 5)


def test_catalog_ids_unique_and_roles_valid() -> None:
    """Every persona has a unique id and only known roles."""
    ids = [persona.persona_id for persona in PERSONAS]
    assert len(ids) == len(set(ids)) == 12
    assert all(
        persona.roles and persona.roles <= {ROLE_HOST, ROLE_READER}
        for persona in PERSONAS
    )


def test_defaults_reproduce_todays_voice_and_style() -> None:
    """The default personas sound exactly like today's host and reader."""
    host = get_persona("default_host")
    reader = get_persona("default_reader")
    assert (host.gemini_voice, host.character, host.openai_voice) == (
        "Pulcherrima",
        "news_anchor",
        "nova",
    )
    assert host.delivery == PERSONALITY_PROMPTS["news_anchor"]
    assert (reader.gemini_voice, reader.character, reader.openai_voice) == (
        "Enceladus",
        "life_coach",
        "onyx",
    )
    assert reader.delivery == PERSONALITY_PROMPTS["life_coach"]


def test_personas_for_role_filters() -> None:
    """A role lists only the personas suited to it."""
    readers = {persona.persona_id for persona in personas_for_role(ROLE_READER)}
    assert {"malenfant", "sage", "default_reader", "elena"} <= readers
    assert "theo" not in readers
    assert "default_host" not in readers


def test_pick_none_or_unknown_returns_none() -> None:
    """No selection, or only removed personas, means today's behaviour."""
    assert pick_persona(None, ROLE_HOST, "p1", DAY) is None
    assert pick_persona([], ROLE_HOST, "p1", DAY) is None
    assert pick_persona(["gone_persona"], ROLE_HOST, "p1", DAY) is None


def test_pick_ignores_wrong_role() -> None:
    """A persona saved for a role it doesn't suit is skipped."""
    assert pick_persona(["theo"], ROLE_READER, "p1", DAY) is None
    picked = pick_persona(["theo", "sage"], ROLE_READER, "p1", DAY)
    assert picked.persona_id == "sage"


def test_pick_single_and_stable_daily() -> None:
    """One persona always wins; several give one stable pick per day."""
    assert pick_persona(["arthur"], ROLE_HOST, "p1", DAY).persona_id == "arthur"
    ids = ["arthur", "theo", "ray"]
    first = pick_persona(ids, ROLE_HOST, "p1", DAY)
    assert all(
        pick_persona(list(reversed(ids)), ROLE_HOST, "p1", DAY) == first
        for _ in range(5)
    )
    picks = {
        pick_persona(ids, ROLE_HOST, "p1", DAY + timedelta(days=n)).persona_id
        for n in range(30)
    }
    assert len(picks) > 1


def test_characters_never_name_the_persona() -> None:
    """The writer never learns the persona's name, so it can't say it."""
    for persona in PERSONAS:
        if persona.persona_id.startswith("default_"):
            continue
        assert not re.search(
            rf"\b{re.escape(persona.name)}\b", persona.character, re.IGNORECASE
        )
