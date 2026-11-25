"""Tests for checking voiced speech against its script."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.cast_studio.speech_check import (
    async_transcribe,
    speech_distance,
    speech_matches,
)

if TYPE_CHECKING:
    from pathlib import Path

PARFAIT = (
    "Today is National Parfait Day, which gives us all a solid excuse to layer "
    "up some Greek yogurt, fresh berries, and granola before noon. The word "
    "parfait actually comes from the French for perfect, and honestly, they "
    "aren't wrong."
)


def test_identical_speech_matches() -> None:
    assert speech_matches(PARFAIT, PARFAIT)
    assert speech_distance(PARFAIT, PARFAIT) == 0


def test_script_read_twice_does_not_match() -> None:
    assert not speech_matches(PARFAIT, f"{PARFAIT} {PARFAIT}")


def test_a_short_script_read_twice_does_not_match() -> None:
    line = "Close the freezer door, it was left open."
    assert not speech_matches(line, f"{line} {line}")


def test_half_the_script_does_not_match() -> None:
    first_half = PARFAIT.split(" The word", maxsplit=1)[0]
    assert not speech_matches(PARFAIT, first_half)


def test_numbers_and_name_spellings_still_match() -> None:
    """A transcriber writes numbers and names its own way."""
    script = "It is 48 degrees this morning, Javen, with a high of 56."
    heard = "It is forty-eight degrees this morning, Jayven, with a high of fifty six."
    assert speech_matches(script, heard)


def test_respelled_names_still_match() -> None:
    """The voice reads a respelled name; the transcriber writes the real one."""
    script = (
        "Good morning, Shi-vawn. It is Wednesday, November 25th, 2026. "
        "Shi-vawn, your day looks promising."
    )
    heard = (
        "Good morning, Siobhan. It is Wednesday, November 25th, 2026. "
        "Siobhan, your day looks promising."
    )
    assert speech_matches(script, heard)


def test_units_and_contractions_still_match() -> None:
    assert speech_matches(
        "It is 72°F with 40% humidity and wind at 10 mph.",
        "It is seventy-two degrees Fahrenheit with forty percent humidity and "
        "wind at ten miles per hour.",
    )
    assert speech_matches("Dinner's ready, everyone!", "Dinner is ready, everyone!")


def test_a_short_aside_still_matches() -> None:
    """One short added line is not worth voicing the segment again."""
    assert speech_matches(PARFAIT, f"{PARFAIT} What a treat.")


def test_distance_ranks_how_far_speech_strays() -> None:
    doubled = speech_distance(PARFAIT, f"{PARFAIT} {PARFAIT}")
    aside = speech_distance(PARFAIT, f"{PARFAIT} What a treat.")
    assert 0 < aside < doubled


def _session(payload: dict, status: int = 200) -> MagicMock:
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=payload)
    resp.text = AsyncMock(return_value="error body")
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.post.return_value = ctx
    return session


async def test_transcribe_sends_the_audio_and_returns_the_words(tmp_path: Path) -> None:
    wav = tmp_path / "speech.wav"
    wav.write_bytes(b"RIFFaudio")
    session = _session(
        {"candidates": [{"content": {"parts": [{"text": " Hello there. \n"}]}}]}
    )

    words = await async_transcribe(session, "key", "gemini-3.5-flash-lite", str(wav))

    assert words == "Hello there."
    url = session.post.call_args.args[0]
    assert "models/gemini-3.5-flash-lite:generateContent" in url
    assert "key=key" in url
    parts = session.post.call_args.kwargs["json"]["contents"][0]["parts"]
    assert parts[0] == {
        "inlineData": {
            "mimeType": "audio/wav",
            "data": base64.b64encode(b"RIFFaudio").decode("ascii"),
        }
    }
    assert "verbatim" in parts[1]["text"]


async def test_transcribe_raises_on_an_error_status(tmp_path: Path) -> None:
    wav = tmp_path / "speech.wav"
    wav.write_bytes(b"RIFFaudio")
    with pytest.raises(RuntimeError):
        await async_transcribe(_session({}, status=500), "k", "m", str(wav))
