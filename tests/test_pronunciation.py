"""Tests for pronunciation lexicon building and application (spec §5.8)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cast_studio.generator import _async_compile_single_step
from custom_components.cast_studio.people import Person, get_people
from custom_components.cast_studio.pronunciation import (
    apply_pronunciations,
    build_lexicon,
)

from .conftest import ALEX_ID, SAM_ID, make_entry, person_subentry

LEX = [
    ("Mary Anne", "Mary Ann"),
    ("Siobhan", "Shiv-awn"),
    ("Mary", "Mair-ee"),
    ("Al", "Owl"),
]


def _person(name: str, phonetic: str | None = None, rows: tuple = ()) -> Person:
    return Person(
        person_id=name.lower(),
        name=name,
        phonetic_name=phonetic or name,
        media_player=None,
        commute_destination_zone=None,
        transportation_mode="fastest",
        reset_time=time(3, 0),
        max_age_minutes=0,
        pronunciations=tuple(rows),
    )


def test_whole_word_only() -> None:
    assert apply_pronunciations("Alarm for Al.", LEX) == "Alarm for Owl."


def test_longest_first() -> None:
    assert apply_pronunciations("Mary Anne and Mary", LEX) == "Mary Ann and Mair-ee"


def test_case_insensitive_and_possessive() -> None:
    assert apply_pronunciations("siobhan's lunch", LEX) == "Shiv-awn's lunch"


def test_single_pass_no_rereplacement() -> None:
    lex = [*LEX, ("Owl", "Hoot")]
    assert apply_pronunciations("Al and Owl", lex) == "Owl and Hoot"


def test_already_respelled_hyphenated_name_not_matched_again() -> None:
    assert apply_pronunciations("Good morning, Al-ex.", [("Al", "Owl")]) == (
        "Good morning, Al-ex."
    )


def test_empty_inputs() -> None:
    assert apply_pronunciations("", LEX) == ""
    assert apply_pronunciations("Hello Al", []) == "Hello Al"


def test_household_names_apply_for_any_listener() -> None:
    alex = _person("Alex", "Al-ex")
    sam = _person("Sam", "Sahm")
    assert ("Sam", "Sahm") in build_lexicon([alex, sam], alex)
    assert ("Alex", "Al-ex") in build_lexicon([alex, sam], None)


def test_listener_row_wins_and_other_persons_rows_excluded() -> None:
    alex = _person("Alex", "Al-ex", rows=[("Sam", "Sammy")])
    sam = _person("Sam", "Sahm", rows=[("Siobhan", "Sho-bahn")])
    lexicon = build_lexicon([alex, sam], alex)
    assert ("Sam", "Sammy") in lexicon
    assert ("Sam", "Sahm") not in lexicon
    assert all(written != "Siobhan" for written, _ in lexicon)


def test_lexicon_sorted_longest_first_and_skips_identity_rows() -> None:
    alex = _person("Alex", rows=[("Mary Anne", "Mary Ann"), ("Mary", "Mair-ee")])
    lexicon = build_lexicon([alex], alex)
    assert lexicon == [("Mary Anne", "Mary Ann"), ("Mary", "Mair-ee")]


def test_person_pronunciations_keep_only_complete_rows() -> None:
    entry = make_entry(
        [
            person_subentry(
                ALEX_ID,
                "Alex",
                pronunciations=[
                    {"written": "Siobhan", "spoken": "Shiv-awn"},
                    {"written": "Blank", "spoken": " "},
                    {"written": "", "spoken": "x"},
                ],
            )
        ]
    )
    (alex,) = get_people(entry)
    assert alex.pronunciations == (("Siobhan", "Shiv-awn"),)
    assert "pronunciations" not in alex.settings


def _loaded(hass: HomeAssistant, subentries: list[dict]) -> MockConfigEntry:
    entry = make_entry(subentries)
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


async def _run_in_executor(
    _hass: HomeAssistant, func: Callable, *args: Any, **_kwargs: Any
) -> Any:
    """Run render_slide for real (patched), report a fixed audio duration."""
    if getattr(func, "__name__", "") == "get_audio_duration":
        return 2.0
    return func(*args)


async def _compile(hass: HomeAssistant, tmp_path: Path, step: dict, recipient: dict):
    with (
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value=str(tmp_path / "a.wav")),
        ) as synth,
        patch(
            "custom_components.cast_studio.generator._async_run_in_executor",
            new=AsyncMock(return_value=2.0),
        ),
    ):
        await _async_compile_single_step(
            hass,
            step,
            step["name"],
            recipient,
            {},
            is_visual=False,
            default_pad_seconds=0.0,
            audio_target_path=str(tmp_path / "a.wav"),
        )
    return synth


@pytest.mark.asyncio
async def test_tts_receives_respelled_text_but_card_keeps_spelling(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _loaded(
        hass,
        [
            person_subentry(
                ALEX_ID,
                "Alex",
                phonetic_name="Al-ex",
                pronunciations=[{"written": "Siobhan", "spoken": "Shiv-awn"}],
            )
        ],
    )
    step = {
        "name": "note",
        "narration": "Lunch with Siobhan",
        "title": "Lunch with Siobhan",
    }
    with (
        patch(
            "custom_components.cast_studio.generator.render_slide",
            return_value=str(tmp_path / "s.jpg"),
        ) as render,
        patch(
            "custom_components.cast_studio.generator.async_synthesize_speech",
            new=AsyncMock(return_value=str(tmp_path / "a.wav")),
        ) as synth,
        patch(
            "custom_components.cast_studio.generator._async_run_in_executor",
            new=AsyncMock(side_effect=_run_in_executor),
        ),
        patch(
            "custom_components.cast_studio.generator._async_resolve_step_visual_assets",
            new=AsyncMock(),
        ),
    ):
        await _async_compile_single_step(
            hass,
            step,
            "note",
            {"name": "Alex", "phonetic_name": "Al-ex"},
            {},
            is_visual=True,
            default_pad_seconds=0.0,
            audio_target_path=str(tmp_path / "a.wav"),
            slide_target_path=str(tmp_path / "s.jpg"),
        )
    assert synth.call_args.args[1] == "Lunch with Shiv-awn"
    assert render.call_args.args[0]["title"] == "Lunch with Siobhan"


@pytest.mark.asyncio
async def test_legacy_pipeline_applies_household_pronunciations(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _loaded(
        hass,
        [
            person_subentry(ALEX_ID, "Alex", phonetic_name="Al-ex"),
            person_subentry(
                SAM_ID,
                "Sam",
                phonetic_name="Sahm",
                pronunciations=[{"written": "Siobhan", "spoken": "Shiv-awn"}],
            ),
        ],
    )
    # Old pipeline recipient not matching a person: household names only.
    synth = await _compile(
        hass,
        tmp_path,
        {"name": "note", "narration": "Sam met Siobhan with Alex"},
        {"name": "Visitor", "phonetic_name": "Visitor"},
    )
    assert synth.call_args.args[1] == "Sahm met Siobhan with Al-ex"


@pytest.mark.asyncio
async def test_listener_rows_do_not_double_replace_recipient_name(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _loaded(
        hass,
        [
            person_subentry(
                ALEX_ID,
                "Alex",
                phonetic_name="Al-ex",
                pronunciations=[{"written": "Al", "spoken": "Owl"}],
            )
        ],
    )
    synth = await _compile(
        hass,
        tmp_path,
        {"name": "note", "narration": "Hello Al-ex, Al is here"},
        {"name": "Alex", "phonetic_name": "Al-ex"},
    )
    assert synth.call_args.args[1] == "Hello Al-ex, Owl is here"


@pytest.mark.asyncio
async def test_closing_segment_uses_lexicon(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _loaded(
        hass,
        [
            person_subentry(
                ALEX_ID,
                "Alex",
                phonetic_name="Al-ex",
                pronunciations=[{"written": "Have", "spoken": "Hav"}],
            ),
            person_subentry(SAM_ID, "Sam", phonetic_name="Sahm"),
        ],
    )
    synth = await _compile(
        hass,
        tmp_path,
        {"name": "closing", "type": "closing"},
        {"name": "Alex", "phonetic_name": "Al-ex"},
    )
    spoken = synth.call_args.args[1]
    assert spoken == "Hav a great day, Al-ex!"


def test_household_lexicon_merges_everyones_rows() -> None:
    """Announcements have no listener: everyone's names and rows apply."""
    from custom_components.cast_studio.pronunciation import build_household_lexicon

    alex = _person("Alex", "Al-ex", (("Siobhan", "Shiv-awn"),))
    sam = _person("Sam", "Sahm", (("Siobhan", "Shuh-vawn"), ("Mary", "Mair-ee")))
    lexicon = build_household_lexicon([alex, sam])
    assert ("Alex", "Al-ex") in lexicon
    assert ("Sam", "Sahm") in lexicon
    assert ("Mary", "Mair-ee") in lexicon
    assert ("Siobhan", "Shuh-vawn") in lexicon
    assert ("Siobhan", "Shiv-awn") not in lexicon
    assert [len(w) for w, _ in lexicon] == sorted(
        (len(w) for w, _ in lexicon), reverse=True
    )
