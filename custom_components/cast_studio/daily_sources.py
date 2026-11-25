"""
Daily content from the web: the fun fact of the day.

Each source is read at most once a day and never fails a build: when it
cannot be read, the segment is written without it.
"""

from __future__ import annotations

import asyncio
import html
import re
from datetime import date, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

FETCH_TIMEOUT_SECONDS = 15
FUN_FACT_URL = "https://www.beagreatteacher.com/daily-fun-fact/"
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
    except (TimeoutError, aiohttp.ClientError) as err:
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
    return fun_fact_context(fact, await _history(hass).async_recent(), day)


async def async_remember_fun_fact(hass: HomeAssistant, fact: str | None) -> None:
    """Remember a fun fact that was told, so the AI does not repeat it."""
    if fact and fact.strip():
        await _history(hass).async_add(fact.strip())
