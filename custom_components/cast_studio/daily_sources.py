"""
Daily content from the web: the fun fact of the day and today's national days.

Each source is read at most once a day and never fails a build: when it
cannot be read, the segment is written without it.
"""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from datetime import date, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from pathlib import Path

    from homeassistant.core import HomeAssistant

FETCH_TIMEOUT_SECONDS = 15
FUN_FACT_URL = "https://www.beagreatteacher.com/daily-fun-fact/"
CHECKIDAY_DAY_URL = "https://www.checkiday.com/{day:%m}/{day:%d}/{day:%Y}"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
DATA_DAILY = "daily_sources"
DATA_FACT_HISTORY = "fun_fact_history"

# A topic per day for the AI's own fact, when the fact of the day is missing.
FUN_FACT_TOPICS = (
    "the ocean",
    "space",
    "the human body",
    "food and cooking",
    "language and words",
    "inventions",
    "insects",
    "birds",
    "music",
    "geography",
    "sports",
    "plants",
    "everyday objects",
    "ancient history",
)
RECENT_FACTS_IN_PROMPT = 30
FACT_HISTORY_SIZE = 60

_DASHES = "\N{EN DASH}\N{EM DASH}-"
_FUN_FACT_DATE_RE = re.compile(
    rf"Fun Facts\s*[{_DASHES}]\s*([A-Z][a-z]+ \d{{1,2}}, \d{{4}})"
)
_RANDOM_FACT_RE = re.compile(
    r"Random Fact of the Day:.*?<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE
)
_TAG_RE = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", fragment)).split())


def parse_fun_fact(page_html: str, day: date) -> str | None:
    """Return the page's random fact of the day if the page is for ``day``."""
    heading = _FUN_FACT_DATE_RE.search(html.unescape(page_html))
    if heading is None:
        return None
    try:
        page_day = datetime.strptime(heading.group(1), "%B %d, %Y").date()  # noqa: DTZ007
    except ValueError:
        return None
    if page_day != day:
        return None
    if (fact := _RANDOM_FACT_RE.search(page_html)) is None:
        return None
    return _text(fact.group(1)) or None


def _cache(hass: HomeAssistant) -> dict[tuple[str, date], Any]:
    return hass.data.setdefault(DOMAIN, {}).setdefault(DATA_DAILY, {})


async def _async_fetch_text(hass: HomeAssistant, url: str) -> str | None:
    """Return the page at ``url``, or None when it cannot be read."""
    try:
        async with (
            asyncio.timeout(FETCH_TIMEOUT_SECONDS),
            async_get_clientsession(hass).get(url) as resp,
        ):
            if resp.status != HTTPStatus.OK:
                LOGGER.warning("Could not read %s: status %s", url, resp.status)
                return None
            return await resp.text()
    except Exception as err:  # noqa: BLE001 - a source must never fail a build
        LOGGER.warning("Could not read %s: %s", url, err)
        return None


async def async_fun_fact_of_the_day(hass: HomeAssistant, day: date) -> str | None:
    """Return beagreatteacher.com's fact for ``day``, or None."""
    cache = _cache(hass)
    if (key := ("fun_fact", day)) in cache:
        return cache[key]
    page = await _async_fetch_text(hass, FUN_FACT_URL)
    fact = parse_fun_fact(page, day) if page else None
    if fact:
        cache[key] = fact
    elif page:
        LOGGER.warning("No fun fact for %s on %s yet", day, FUN_FACT_URL)
    return fact


def fun_fact_context(fact: str | None, recent: list[str], day: date) -> str:
    """Return the fun fact segment's context for the writer."""
    if fact:
        return (
            "Today's fun fact (retell this exact fact accurately in your own "
            f"words; do not swap in a different fact): {fact}"
        )
    topic = FUN_FACT_TOPICS[day.toordinal() % len(FUN_FACT_TOPICS)]
    context = f"Share a surprising, little-known fun fact about {topic}."
    if recent := recent[-RECENT_FACTS_IN_PROMPT:]:
        context += " Do not use any of these recent facts: " + " | ".join(recent)
    return context


class FactHistory:
    """The fun facts told lately, oldest first."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Start empty; load on first use."""
        self._store: Store[dict[str, Any]] = Store(hass, 1, "cast_studio.fun_facts")
        self._facts: list[str] | None = None

    async def async_recent(self) -> list[str]:
        """Return the remembered facts, oldest first."""
        if self._facts is None:
            stored = await self._store.async_load() or {}
            facts = stored.get("facts")
            self._facts = [str(f) for f in facts] if isinstance(facts, list) else []
        return list(self._facts)

    async def async_add(self, fact: str) -> None:
        """Remember ``fact``, keeping the last FACT_HISTORY_SIZE."""
        facts = [*await self.async_recent(), fact][-FACT_HISTORY_SIZE:]
        self._facts = facts
        self._store.async_delay_save(lambda: {"facts": facts}, 1)


def _history(hass: HomeAssistant) -> FactHistory:
    data = hass.data.setdefault(DOMAIN, {})
    if DATA_FACT_HISTORY not in data:
        data[DATA_FACT_HISTORY] = FactHistory(hass)
    return data[DATA_FACT_HISTORY]


async def async_fun_fact_context(hass: HomeAssistant, day: date) -> str:
    """Return the fun fact segment's context for ``day``."""
    fact = await async_fun_fact_of_the_day(hass, day)
    try:
        recent = await _history(hass).async_recent()
    except Exception as err:  # noqa: BLE001 - a source must never fail a build
        LOGGER.warning("Could not read the fun fact history: %s", err)
        recent = []
    return fun_fact_context(fact, recent, day)


async def async_remember_fun_fact(hass: HomeAssistant, fact: str | None) -> None:
    """Remember a fun fact that was told, so the AI does not repeat it."""
    if fact and fact.strip():
        try:
            await _history(hass).async_add(fact.strip())
        except Exception as err:  # noqa: BLE001 - a source must never fail a build
            LOGGER.warning("Could not remember the fun fact: %s", err)


@dataclass(frozen=True)
class CheckidayEvent:
    """One of the day's observances on checkiday.com, with its 16:9 photo."""

    name: str
    image_url: str


# Each observance's card carries its 300 px photo; the same file is also
# published at 1200 px (16:9), the size the slide needs.
_CHECKIDAY_CARD_RE = re.compile(
    r'<img src="(https://static\.checkiday\.com/img/)300/([^"]+)"[^>]*'
    r'alt="Image for ([^"]*)"'
)
# Checkiday's own link to its history page, shown among the day's cards.
_CHECKIDAY_NOT_EVENTS = frozenset({"On This Day in History"})
# Words most observance names share; they say nothing about which day it is.
_GENERIC_NAME_WORDS = frozenset(
    [
        "national",
        "international",
        "world",
        "day",
        "week",
        "month",
        "the",
        "of",
        "a",
        "an",
        "and",
        "for",
        "with",
    ]
)


def parse_checkiday_events(page_html: str) -> list[CheckidayEvent]:
    """Return the observances listed on a Checkiday date page."""
    events: dict[str, CheckidayEvent] = {}
    for base, file_name, raw_name in _CHECKIDAY_CARD_RE.findall(page_html):
        name = html.unescape(raw_name).strip()
        if name and name not in _CHECKIDAY_NOT_EVENTS and name not in events:
            events[name] = CheckidayEvent(name, f"{base}1200/{file_name}")
    return list(events.values())


async def async_checkiday_events(
    hass: HomeAssistant, day: date
) -> list[CheckidayEvent]:
    """Return checkiday.com's observances for ``day``; empty when unreadable."""
    cache = _cache(hass)
    if (key := ("checkiday", day)) in cache:
        return cache[key]
    page = await _async_fetch_text(hass, CHECKIDAY_DAY_URL.format(day=day))
    events = parse_checkiday_events(page) if page else []
    if events:
        cache[key] = events
    return events


def national_day_context(events: list[CheckidayEvent]) -> str:
    """Return the national day segment's context: the day's observances."""
    names = "; ".join(event.name for event in events)
    return f"Today's national days (choose only from this list): {names}"


def _name_words(name: str) -> set[str]:
    words = re.findall(r"[a-z]+", html.unescape(name).lower())
    return {w for w in words if w not in _GENERIC_NAME_WORDS}


def match_event(picked: str, events: list[CheckidayEvent]) -> CheckidayEvent | None:
    """
    Return the event the writer picked, by the name it gave.

    Names are compared by their distinctive words, so "Parfait Day" and
    'National "Eat with a Friend" Day!' still find their day, while two days
    that only share "National ... Day" never match.
    """
    wanted = _name_words(picked)
    if not wanted:
        return None
    best: tuple[float, CheckidayEvent] | None = None
    for event in events:
        words = _name_words(event.name)
        if not words:
            continue
        overlap = len(wanted & words) / len(wanted | words)
        if overlap >= 0.5 and (best is None or overlap > best[0]):  # noqa: PLR2004
            best = (overlap, event)
    return best[1] if best else None


async def async_download_image(hass: HomeAssistant, url: str, path: Path) -> bool:
    """Save the image at ``url`` to ``path``; return whether it was saved."""
    try:
        async with (
            asyncio.timeout(FETCH_TIMEOUT_SECONDS),
            async_get_clientsession(hass).get(url) as resp,
        ):
            if resp.status != HTTPStatus.OK or not str(resp.content_type).startswith(
                "image/"
            ):
                LOGGER.warning("Could not download %s: status %s", url, resp.status)
                return False
            if (resp.content_length or 0) > MAX_IMAGE_BYTES:
                return False
            body = await resp.read()
    except (TimeoutError, aiohttp.ClientError) as err:
        LOGGER.warning("Could not download %s: %s", url, err)
        return False
    if not body or len(body) > MAX_IMAGE_BYTES:
        return False

    def _write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    await asyncio.to_thread(_write)
    return True
