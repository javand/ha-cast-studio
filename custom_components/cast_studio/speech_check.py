"""
Check that voiced speech says its script once, as written.

Gemini voice models now and then read a script twice, add words of their own
or drop some. The text model transcribes the audio and the transcript is
compared with the script word by word.
"""

from __future__ import annotations

import asyncio
import base64
import re
from difflib import SequenceMatcher
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

# Below this the speech is voiced again: a script read twice scores about
# 0.67, one short added line about 0.95.
MATCH_THRESHOLD = 0.85
# The first voicing plus two more when the speech does not match.
SPEECH_CHECK_ATTEMPTS = 3
TRANSCRIBE_TIMEOUT_SECONDS = 30
TRANSCRIBE_PROMPT = (
    "Transcribe this speech verbatim. Return only the words spoken, nothing else."
)

# Numbers come back as digits or words either way, so neither counts.
_NUMBER_WORDS = frozenset(
    [
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
        "twenty",
        "thirty",
        "forty",
        "fifty",
        "sixty",
        "seventy",
        "eighty",
        "ninety",
        "hundred",
        "thousand",
        "million",
        "and",
        "point",
        "percent",
    ]
)
_WORD_RE = re.compile(r"[a-z']+")


def _words(text: str) -> list[str]:
    text = (
        text.lower().replace("\N{RIGHT SINGLE QUOTATION MARK}", "'").replace("-", " ")
    )
    return [w for w in _WORD_RE.findall(text) if w.strip("'") not in _NUMBER_WORDS]


def speech_match_score(script: str, transcript: str) -> float:
    """Return how closely ``transcript`` follows ``script``, from 0 to 1."""
    return SequenceMatcher(
        None, _words(script), _words(transcript), autojunk=False
    ).ratio()


async def async_transcribe(
    session: aiohttp.ClientSession, api_key: str, model: str, wav_path: str
) -> str:
    """Return the words spoken in ``wav_path``; raise when Gemini cannot say."""
    audio = await asyncio.to_thread(Path(wav_path).read_bytes)
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
        f":generateContent?key={api_key}"
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "inlineData": {
                            "mimeType": "audio/wav",
                            "data": base64.b64encode(audio).decode("ascii"),
                        }
                    },
                    {"text": TRANSCRIBE_PROMPT},
                ]
            }
        ],
        "generationConfig": {"temperature": 0},
    }
    async with (
        asyncio.timeout(TRANSCRIBE_TIMEOUT_SECONDS),
        session.post(url, json=payload) as resp,
    ):
        if resp.status != HTTPStatus.OK:
            msg = f"Transcription returned status {resp.status}: {await resp.text()}"
            raise RuntimeError(msg)
        data = await resp.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError) as err:
        msg = "Transcription returned no text"
        raise RuntimeError(msg) from err
    return "".join(str(p.get("text", "")) for p in parts).strip()
