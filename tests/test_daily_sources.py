"""Tests for the daily web sources: fun fact of the day and Checkiday."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.cast_studio import daily_sources
from custom_components.cast_studio.daily_sources import (
    CheckidayEvent,
    async_checkiday_events,
    async_download_image,
    async_fun_fact_context,
    async_fun_fact_of_the_day,
    async_remember_fun_fact,
    fun_fact_context,
    match_event,
    national_day_context,
    parse_checkiday_events,
    parse_fun_fact,
)

FIXTURES = Path(__file__).parent / "fixtures"
NOV_25 = date(2026, 11, 25)
TV_DINNER = (
    "The first frozen TV dinner was a turkey meal and was created in the "
    "mid-1950s after Swanson overestimated holiday turkey demand."
)


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_fun_fact_reads_the_random_fact_of_the_day() -> None:
    assert parse_fun_fact(_page("beagreatteacher_2026-11-25.html"), NOV_25) == (
        TV_DINNER
    )


def test_parse_fun_fact_ignores_a_page_for_another_day() -> None:
    page = _page("beagreatteacher_2026-11-25.html")
    assert parse_fun_fact(page, date(2026, 11, 26)) is None


def test_parse_fun_fact_without_a_fact_is_none() -> None:
    assert (
        parse_fun_fact("<h1>Fun Facts &#8211; November 25, 2026</h1>", NOV_25) is None
    )


def _session(text: str, status: int = 200) -> MagicMock:
    resp = AsyncMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.get.return_value = ctx
    return session


async def test_fun_fact_of_the_day_is_fetched_once_per_day(
    hass: HomeAssistant,
) -> None:
    session = _session(_page("beagreatteacher_2026-11-25.html"))
    with patch(
        f"{daily_sources.__name__}.async_get_clientsession", return_value=session
    ):
        assert await async_fun_fact_of_the_day(hass, NOV_25) == TV_DINNER
        assert await async_fun_fact_of_the_day(hass, NOV_25) == TV_DINNER
    assert session.get.call_count == 1
    assert session.get.call_args.args[0] == daily_sources.FUN_FACT_URL


async def test_fun_fact_of_the_day_is_none_when_the_site_fails(
    hass: HomeAssistant,
) -> None:
    session = _session("down", status=503)
    with patch(
        f"{daily_sources.__name__}.async_get_clientsession", return_value=session
    ):
        assert await async_fun_fact_of_the_day(hass, NOV_25) is None


def test_context_with_a_fact_asks_for_that_fact() -> None:
    context = fun_fact_context(TV_DINNER, ["Octopuses have three hearts."], NOV_25)
    assert TV_DINNER in context
    assert "do not swap in a different fact" in context
    assert "Octopuses" not in context


def test_context_without_a_fact_gives_a_topic_and_recent_facts() -> None:
    recent = [f"Fact {n}." for n in range(40)]
    context = fun_fact_context(None, recent, NOV_25)
    topic = daily_sources.FUN_FACT_TOPICS[NOV_25.toordinal() % 14]
    assert f"about {topic}" in context
    assert "Fact 39." in context
    assert "Fact 9." not in context  # only the last 30


async def test_remembered_facts_steer_the_next_fallback(hass: HomeAssistant) -> None:
    with patch(
        f"{daily_sources.__name__}.async_fun_fact_of_the_day",
        new=AsyncMock(return_value=None),
    ):
        await async_remember_fun_fact(hass, "Octopuses have three hearts.")
        await async_remember_fun_fact(hass, "")
        context = await async_fun_fact_context(hass, NOV_25)
    assert "Octopuses have three hearts." in context


async def test_fun_fact_history_keeps_the_last_sixty(hass: HomeAssistant) -> None:
    for n in range(65):
        await async_remember_fun_fact(hass, f"Fact {n}.")
    recent: Any = await daily_sources._history(hass).async_recent()
    assert len(recent) == 60
    assert recent[0] == "Fact 5."


PARFAIT = CheckidayEvent(
    "National Parfait Day", "https://static.checkiday.com/img/1200/yogurt-924880.jpg"
)


def test_parse_checkiday_events_lists_the_days_with_large_photos() -> None:
    events = parse_checkiday_events(_page("checkiday_2026-11-25.html"))
    names = [e.name for e in events]
    assert len(events) == 10
    assert PARFAIT in events
    assert 'National "Eat with a Friend" Day' in names
    assert "Blasé Day" in names
    assert "On This Day in History" not in names


async def test_checkiday_events_are_read_once_per_day(hass: HomeAssistant) -> None:
    session = _session(_page("checkiday_2026-11-25.html"))
    with patch(
        f"{daily_sources.__name__}.async_get_clientsession", return_value=session
    ):
        first = await async_checkiday_events(hass, NOV_25)
        assert await async_checkiday_events(hass, NOV_25) == first
    assert session.get.call_count == 1
    assert session.get.call_args.args[0] == "https://www.checkiday.com/11/25/2026"


async def test_checkiday_events_are_empty_when_the_site_fails(
    hass: HomeAssistant,
) -> None:
    with patch(
        f"{daily_sources.__name__}.async_get_clientsession",
        return_value=_session("down", status=500),
    ):
        assert await async_checkiday_events(hass, NOV_25) == []


def test_national_day_context_lists_the_days() -> None:
    context = national_day_context([PARFAIT, CheckidayEvent("Tie One On Day", "u")])
    assert context == (
        "Today's national days (choose only from this list): "
        "National Parfait Day; Tie One On Day"
    )


@pytest.mark.parametrize(
    "picked",
    [
        "National Parfait Day",
        "national parfait day!",
        "Parfait Day",
        "National Parfait Day 2026",
    ],
)
def test_match_event_finds_the_picked_day(picked: str) -> None:
    events = parse_checkiday_events(_page("checkiday_2026-11-25.html"))
    assert match_event(picked, events) == PARFAIT


def test_match_event_handles_quotes_and_misses() -> None:
    events = parse_checkiday_events(_page("checkiday_2026-11-25.html"))
    eat = match_event("National Eat with a Friend Day", events)
    assert eat is not None
    assert eat.name == 'National "Eat with a Friend" Day'
    assert match_event("National Pizza Day", events) is None
    assert match_event("", events) is None


def _image_session(status: int, content_type: str, body: bytes) -> MagicMock:
    resp = AsyncMock()
    resp.status = status
    resp.content_type = content_type
    resp.content_length = len(body)
    resp.read = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.get.return_value = ctx
    return session


@pytest.mark.parametrize(
    ("status", "content_type", "saved"),
    [(200, "image/jpeg", True), (404, "text/html", False), (200, "text/html", False)],
)
async def test_download_image_saves_only_images(
    hass: HomeAssistant,
    tmp_path: Path,
    status: int,
    content_type: str,
    saved: bool,  # noqa: FBT001
) -> None:
    target = tmp_path / "bg.jpg"
    with patch(
        f"{daily_sources.__name__}.async_get_clientsession",
        return_value=_image_session(status, content_type, b"jpgdata"),
    ):
        assert await async_download_image(hass, PARFAIT.image_url, target) is saved
    assert target.exists() is saved
