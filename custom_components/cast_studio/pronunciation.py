"""Respell names and words in narration right before text-to-speech (spec §5.8)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from .people import Person


def build_lexicon(
    people: Sequence[Person], listener: Person | None
) -> list[tuple[str, str]]:
    """
    Return the (written, spoken) pairs for one listener, longest written first.

    Every person's phonetic name applies in every briefing. The listener's own
    pronunciation rows apply only to the listener, and win any conflict.
    """
    return _lexicon(people, listener.pronunciations if listener else ())


def build_household_lexicon(people: Sequence[Person]) -> list[tuple[str, str]]:
    """
    Return the (written, spoken) pairs for the whole household, longest first.

    For announcements, which have no single listener: every person's phonetic
    name and every person's pronunciation rows apply; a later person's row
    wins a conflict.
    """
    return _lexicon(people, [row for p in people for row in p.pronunciations])


def _lexicon(
    people: Sequence[Person], pronunciations: Iterable[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Merge phonetic names, then pronunciation rows over them; longest first."""
    merged: dict[str, tuple[str, str]] = {}
    for person in people:
        if person.phonetic_name != person.name:
            merged[person.name.casefold()] = (person.name, person.phonetic_name)
    for written, spoken in pronunciations:
        merged[written.casefold()] = (written, spoken)
    rows = [
        (written, spoken)
        for written, spoken in merged.values()
        if written.strip() and written.casefold() != spoken.casefold()
    ]
    return sorted(rows, key=lambda row: len(row[0]), reverse=True)


def apply_pronunciations(text: str, lexicon: Sequence[tuple[str, str]]) -> str:
    """
    Replace whole-word matches in a single pass, longest written form first.

    A match must not touch a word character or a hyphenated word part, so an
    already-respelled name such as "Al-ex" is never matched again as "Al".
    """
    if not text or not lexicon:
        return text
    ordered = sorted(lexicon, key=lambda row: len(row[0]), reverse=True)
    spoken_by_key = {written.casefold(): spoken for written, spoken in ordered}
    alternation = "|".join(re.escape(written) for written, _ in ordered)
    pattern = re.compile(
        rf"(?<![\w-])(?<!\w-)(?:{alternation})(?!\w)(?!-\w)",
        flags=re.IGNORECASE,
    )
    return pattern.sub(lambda m: spoken_by_key[m.group(0).casefold()], text)
