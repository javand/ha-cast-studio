"""Tests for the announce action: media choice, queueing, briefing pause and resume."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.cast_studio import briefing
from custom_components.cast_studio.const import DOMAIN
from custom_components.cast_studio.orchestrator import async_play_and_sync_routine

from .conftest import announcement_subentry, write_fake_build
from .test_announcement_runtime import make_entry, setup

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from custom_components.cast_studio.announcement_build import (
        AnnouncementBuild,
    )
    from custom_components.cast_studio.announcements import Announcement

RUNTIME = "custom_components.cast_studio.announcement_runtime"
PLAY = "custom_components.cast_studio.announcement_play"
SPEAKER = "media_player.kitchen_speaker"
DISPLAY = "media_player.office_display"
BRIEFING_VIDEO = "/config/www/cast_studio/people/p/2026-10-20/play_quick.ab12cd34.mp4"
SPEECH_SECONDS = 0.1


class Player:
    """Fake Cast players: play_media starts playing the requested media."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.fail_on: str | None = None
        # Seconds a cast takes to load; until then the old media still shows.
        self.lag = 0.0
        # Loop time each media was asked for, and started showing as playing.
        self.called: dict[str, float] = {}
        self.shown: dict[str, float] = {}
        for service in ("play_media", "media_seek", "turn_off", "volume_set"):
            hass.services.async_register("media_player", service, self._record)
        hass.services.async_register("media_player", "volume_mute", self._record)
        hass.services.async_register("cast", "show_lovelace_view", self._record)
        registry = er.async_get(hass)
        for entity_id in (SPEAKER, DISPLAY):
            registry.async_get_or_create(
                "media_player",
                "cast",
                f"uid_{entity_id}",
                suggested_object_id=entity_id.split(".", 1)[1],
            )
            hass.states.async_set(entity_id, "off")

    async def _record(self, call: ServiceCall) -> None:
        self.events.append((call.service, dict(call.data)))
        if call.service == "play_media":
            entity_id = call.data["entity_id"]
            media = call.data["media_content_id"]
            if self.fail_on and self.fail_on in media:
                self.hass.states.async_set(entity_id, "off")  # the cast dropped
                return
            attributes = {
                "app_name": "Default Media Receiver",
                "media_content_id": media,
            }
            name = media.split("?")[0].rsplit("/", 1)[-1]
            self.called[name] = self.hass.loop.time()

            def _show() -> None:
                self.shown[name] = self.hass.loop.time()
                self.hass.states.async_set(entity_id, "playing", attributes)
                if "/announcements/" in media:
                    # A real player goes idle when the short announcement ends.
                    self.hass.loop.call_later(
                        SPEECH_SECONDS,
                        lambda: self.hass.states.async_set(
                            entity_id, "idle", attributes
                        ),
                    )

            if self.lag:
                self.hass.loop.call_later(self.lag, _show)
            else:
                _show()

    def played(self) -> list[str]:
        return [d["media_content_id"] for s, d in self.events if s == "play_media"]

    def names(self) -> list[str]:
        return [s for s, _ in self.events if s not in ("volume_set", "volume_mute")]


@pytest.fixture
async def ready(hass: HomeAssistant, tmp_path: Path) -> Player:
    """
    CastStudio set up with announcements already built; fake players.

    No frozen clock: playback here runs on real, short sleeps.
    """

    async def _build(
        _hass: Any,
        directory: Path,
        announcement: Announcement,
        _creds: Any,
        now: datetime,
        _previous: Any,
    ) -> AnnouncementBuild:
        return write_fake_build(
            directory, announcement, now, duration=SPEECH_SECONDS, visual_file="clip"
        )

    patchers = [
        patch(f"{RUNTIME}.async_build_announcement", new=_build),
        patch(
            f"{RUNTIME}.async_discover_ai_credentials", new=AsyncMock(return_value={})
        ),
    ]
    for p in patchers:
        p.start()
    player = Player(hass)
    await setup(
        hass,
        make_entry(
            tmp_path,
            announcement_subentry("a1", "Freezer Door"),
            announcement_subentry(
                "a2", "Front Door", message="The front door is locked", target=SPEAKER
            ),
            announcement_subentry("a3", "Quiet", output_format="audio"),
        ),
    )
    yield player
    for p in reversed(patchers):
        p.stop()


async def announce(hass: HomeAssistant, name: str, target: str | None) -> None:
    data = {"announcement": name}
    if target is not None:
        data["target"] = target
    await hass.services.async_call(DOMAIN, "announce", data, blocking=True)


def played_on(player: Player) -> list[str]:
    return [d["entity_id"] for s, d in player.events if s == "play_media"]


async def finish(hass: HomeAssistant) -> None:
    """Let background playback (real, short sleeps) run to its end."""
    await asyncio.sleep(SPEECH_SECONDS * 4)
    await hass.async_block_till_done()


async def start_briefing(
    hass: HomeAssistant, duration: float = 0.4
) -> briefing._CurrentPlay:
    """A briefing video playing on the display, as briefing.async_play leaves it."""
    metadata: dict[str, Any] = {
        "video_path": BRIEFING_VIDEO,
        "duration": duration,
        "prepare_player": False,
        "post_routine_actions": [
            {
                "action": "cast.show_lovelace_view",
                "data": {"entity_id": DISPLAY, "dashboard_path": "kitchen-dash"},
            }
        ],
    }
    current = briefing._CurrentPlay("r_sam_quick", metadata, player=DISPLAY)
    current.task = await async_play_and_sync_routine(
        hass, "sam_quick", DISPLAY, metadata, [], control=current.control
    )
    briefing._current_plays(hass)[SPEAKER] = current
    await asyncio.sleep(0)
    return current


async def test_announce_on_screen_plays_video(
    hass: HomeAssistant, ready: Player
) -> None:
    await announce(hass, "Freezer Door", DISPLAY)
    assert ready.played()[0].split("?")[0].endswith(".mp4")
    await finish(hass)


async def test_announce_on_speaker_plays_audio(
    hass: HomeAssistant, ready: Player
) -> None:
    hass.states.async_set(SPEAKER, "off")
    await announce(hass, "Freezer Door", SPEAKER)
    assert ready.played()[0].split("?")[0].endswith(".mp3")
    await finish(hass)


async def test_audio_announcement_on_screen_plays_audio(
    hass: HomeAssistant, ready: Player
) -> None:
    await announce(hass, "Quiet", DISPLAY)
    assert ready.played()[0].split("?")[0].endswith(".mp3")
    await finish(hass)


async def test_announce_builds_first_when_never_built(
    hass: HomeAssistant, ready: Player, tmp_path: Path
) -> None:
    from custom_components.cast_studio.announcement_runtime import get_manager

    manager = get_manager(hass)
    manager.state("a1").build = None
    with patch.object(
        manager, "async_ensure_built", wraps=manager.async_ensure_built
    ) as ensure:
        await announce(hass, "Freezer Door", DISPLAY)
    ensure.assert_awaited_once_with("a1")
    assert len(ready.played()) == 1
    await finish(hass)


async def test_announce_returns_once_playing_and_releases_woken_cast_player(
    hass: HomeAssistant, ready: Player
) -> None:
    with patch(f"{PLAY}.async_release_player", new=AsyncMock()) as release:
        await announce(hass, "Freezer Door", DISPLAY)
        assert hass.states.get(DISPLAY).state == "playing"
        release.assert_not_awaited()
        await finish(hass)
    release.assert_awaited_once_with(hass, DISPLAY)


async def test_announce_does_not_release_player_already_in_use(
    hass: HomeAssistant, ready: Player
) -> None:
    hass.states.async_set(DISPLAY, "playing", {"app_name": "Spotify"})
    with patch(f"{PLAY}.async_release_player", new=AsyncMock()) as release:
        await announce(hass, "Freezer Door", DISPLAY)
        await finish(hass)
    release.assert_not_awaited()


async def test_announce_pauses_and_resumes_briefing_at_position(
    hass: HomeAssistant, ready: Player
) -> None:
    current = await start_briefing(hass)
    hass.states.async_set(
        DISPLAY,
        "playing",
        {
            "app_name": "Default Media Receiver",
            "media_content_id": BRIEFING_VIDEO,
            "media_position": 30,
            "media_position_updated_at": dt_util.utcnow(),
        },
    )
    ready.events.clear()
    with patch(f"{PLAY}.async_release_player", new=AsyncMock()) as release:
        await announce(hass, "Freezer Door", DISPLAY)
        # The briefing's own time runs out during the announcement: it must wait.
        await asyncio.sleep(0.5)
        await current.task
        await hass.async_block_till_done()
    played = [url.split("?")[0] for url in ready.played()]
    assert played[0].endswith(".mp3")  # announcement audio only, over a briefing
    assert played[1].endswith("play_quick.ab12cd34.mp4")
    names = ready.names()
    assert names.index("media_seek") > names.index("play_media")
    seek = next(d for s, d in ready.events if s == "media_seek")
    assert seek["seek_position"] == pytest.approx(30, abs=1)
    assert names[-1] == "show_lovelace_view"
    release.assert_not_awaited()


async def test_unknown_position_resumes_from_start(
    hass: HomeAssistant, ready: Player, caplog: pytest.LogCaptureFixture
) -> None:
    current = await start_briefing(hass)
    ready.events.clear()
    with caplog.at_level(logging.WARNING):
        await announce(hass, "Freezer Door", DISPLAY)
        await asyncio.sleep(0.6)
        await current.task
        await hass.async_block_till_done()
    assert "media_seek" not in ready.names()
    assert ready.played()[1].split("?")[0].endswith("play_quick.ab12cd34.mp4")
    assert "from the start" in caplog.text


async def test_no_resume_when_briefing_superseded(
    hass: HomeAssistant, ready: Player
) -> None:
    current = await start_briefing(hass)
    ready.events.clear()
    await announce(hass, "Freezer Door", DISPLAY)
    # A newer play elsewhere supersedes the briefing during the announcement.
    briefing._current_plays(hass).pop(SPEAKER)
    current.task.cancel()
    await finish(hass)
    assert len(ready.played()) == 1


async def test_failed_resume_sets_briefing_error(
    hass: HomeAssistant, ready: Player
) -> None:
    current = await start_briefing(hass)
    ready.events.clear()
    ready.fail_on = "play_quick"
    with patch(
        "custom_components.cast_studio.orchestrator.DEFAULT_PLAYING_TIMEOUT", 0.2
    ):
        await announce(hass, "Freezer Door", DISPLAY)
        await finish(hass)
    assert "never started playing" in current.failed
    assert current.task.done()


async def test_two_announcements_on_one_target_play_in_order(
    hass: HomeAssistant, ready: Player
) -> None:
    order: list[str] = []

    async def _call(name: str) -> None:
        await announce(hass, name, DISPLAY)
        order.append(name)

    first = hass.async_create_task(_call("Freezer Door"))
    await asyncio.sleep(0)
    second = hass.async_create_task(_call("Front Door"))
    await first
    assert len(ready.played()) == 1
    await second
    assert order == ["Freezer Door", "Front Door"]
    assert len(ready.played()) == 2
    await finish(hass)


async def test_announce_unknown_announcement_raises(
    hass: HomeAssistant, ready: Player
) -> None:
    with pytest.raises(HomeAssistantError, match="not found"):
        await announce(hass, "Nope", DISPLAY)


async def test_announce_unavailable_target_raises(
    hass: HomeAssistant, ready: Player
) -> None:
    hass.states.async_set(SPEAKER, "unavailable")
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await announce(hass, "Freezer Door", SPEAKER)


async def test_second_announcement_over_one_briefing_extends_it_once(
    hass: HomeAssistant, ready: Player
) -> None:
    """Final review: each resume sets the end from now, not on top of the last."""
    current = await start_briefing(hass, duration=1.0)
    loop = asyncio.get_running_loop()
    briefing_name = BRIEFING_VIDEO.rsplit("/", 1)[-1]
    await asyncio.sleep(0.4)  # some of the briefing plays before the first pause
    for name in ("Freezer Door", "Front Door"):
        await announce(hass, name, DISPLAY)
        await asyncio.sleep(0.3)
    resumed_at = ready.called[briefing_name]  # the last resume, from the start
    await current.task
    assert loop.time() - resumed_at == pytest.approx(1.0, abs=0.15)


async def test_slow_cast_times_announcement_and_seek_from_real_start(
    hass: HomeAssistant, ready: Player
) -> None:
    """Final review: a Cast still showing the briefing is not the announcement."""
    current = await start_briefing(hass, duration=2.0)
    hass.states.async_set(
        DISPLAY,
        "playing",
        {
            "app_name": "Default Media Receiver",
            "media_content_id": BRIEFING_VIDEO,
            "media_position": 30,
            "media_position_updated_at": dt_util.utcnow(),
        },
    )
    ready.lag = 0.15
    ready.called.clear()
    ready.shown.clear()
    seeks: list[float] = []
    original = ready._record

    async def _record(call: ServiceCall) -> None:
        if call.service == "media_seek":
            seeks.append(hass.loop.time())
        await original(call)

    hass.services.async_register("media_player", "media_seek", _record)
    await announce(hass, "Freezer Door", DISPLAY)
    await asyncio.sleep(1.0)
    await hass.async_block_till_done()
    announcement = next(n for n in ready.shown if n.startswith("speech."))
    briefing_name = BRIEFING_VIDEO.rsplit("/", 1)[-1]
    # The briefing is cast again only after the whole announcement played.
    assert ready.called[briefing_name] - ready.shown[announcement] >= (
        SPEECH_SECONDS - 0.02
    )
    # The seek waits until the briefing media is really back.
    assert seeks
    assert seeks[0] >= ready.shown[briefing_name]
    current.task.cancel()


async def test_announce_without_target_plays_on_default(
    hass: HomeAssistant, ready: Player
) -> None:
    await announce(hass, "Front Door", None)
    assert played_on(ready) == [SPEAKER]
    await finish(hass)


async def test_announce_without_target_or_default_raises(
    hass: HomeAssistant, ready: Player
) -> None:
    with pytest.raises(HomeAssistantError, match="Play on"):
        await announce(hass, "Freezer Door", None)


PLAY_ON = "select.cast_studio_play_on_front_door"
PLAY_BUTTON = "button.cast_studio_play_front_door"


async def press(hass: HomeAssistant, button: str) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True
    )


async def test_play_on_starts_at_default_and_lists_players(
    hass: HomeAssistant, ready: Player
) -> None:
    state = hass.states.get(PLAY_ON)
    assert state.state == SPEAKER
    assert {SPEAKER, DISPLAY} <= set(state.attributes["options"])


async def test_play_button_plays_on_default(hass: HomeAssistant, ready: Player) -> None:
    await press(hass, PLAY_BUTTON)
    assert played_on(ready) == [SPEAKER]
    await finish(hass)


async def test_play_button_plays_on_chosen_player(
    hass: HomeAssistant, ready: Player
) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": PLAY_ON, "option": DISPLAY},
        blocking=True,
    )
    assert hass.states.get(PLAY_ON).state == DISPLAY
    await press(hass, PLAY_BUTTON)
    assert played_on(ready) == [DISPLAY]
    await finish(hass)


async def test_play_button_without_player_raises(
    hass: HomeAssistant, ready: Player
) -> None:
    assert hass.states.get("select.cast_studio_play_on_freezer_door").state in (
        "unknown",
        None,
    )
    with pytest.raises(HomeAssistantError, match="Play on"):
        await press(hass, "button.cast_studio_play_freezer_door")


async def test_new_default_replaces_chosen_player(
    hass: HomeAssistant, ready: Player
) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": PLAY_ON, "option": DISPLAY},
        blocking=True,
    )
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    subentry = entry.subentries["a2"]
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, "target": "media_player.office_tv"}
    )
    await hass.async_block_till_done()
    assert hass.states.get(PLAY_ON).state == "media_player.office_tv"
