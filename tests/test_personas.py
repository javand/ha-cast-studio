"""Tests for the built-in persona catalog and the daily pick."""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from custom_components.cast_studio.personas import (
    PERSONAS,
    Persona,
    get_persona,
    pick_persona,
)
from custom_components.cast_studio.tts_client import PERSONALITY_PROMPTS

DAY = date(2026, 1, 5)


def test_catalog_ids_unique() -> None:
    """Every persona has a unique id and no roles: any persona voices anything."""
    ids = [persona.persona_id for persona in PERSONAS]
    assert len(ids) == len(set(ids)) == 12
    assert all(not hasattr(persona, "roles") for persona in PERSONAS)


def test_defaults_renamed() -> None:
    """With no host/reader roles, the defaults are named for their style."""
    assert get_persona("default_host").name == "News Anchor"
    assert get_persona("default_reader").name == "Life Coach"


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


def test_pick_none_or_unknown_returns_none() -> None:
    """No selection, or only removed personas, picks nobody."""
    assert pick_persona(None, "p1", DAY) is None
    assert pick_persona([], "p1", DAY) is None
    assert pick_persona(["gone_persona"], "p1", DAY) is None


def test_pick_skips_unknown_ids() -> None:
    """A removed persona in a list is skipped."""
    assert pick_persona(["gone_persona", "theo"], "p1", DAY).persona_id == "theo"


def test_pick_single_and_stable_daily() -> None:
    """One persona always wins; several give one stable pick per day."""
    assert pick_persona(["arthur"], "p1", DAY).persona_id == "arthur"
    ids = ["arthur", "theo", "ray"]
    first = pick_persona(ids, "p1", DAY)
    assert pick_persona(list(reversed(ids)), "p1", DAY) == first
    picks = {
        pick_persona(ids, "p1", DAY + timedelta(days=n)).persona_id for n in range(30)
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


@pytest.mark.parametrize("persona", PERSONAS, ids=lambda p: p.persona_id)
def test_every_persona_has_a_short_summary(persona: Persona) -> None:
    """The settings dropdown shows a one-line summary next to each name."""
    assert persona.summary
    assert len(persona.summary) <= 60
    assert persona.name.lower() not in persona.summary.lower()
