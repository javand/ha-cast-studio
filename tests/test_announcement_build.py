"""Tests for building one announcement: words, speech, slide and clip."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState

from custom_components.cast_studio.ai_client import AIClientError
from custom_components.cast_studio.announcement_build import (
    AnnouncementBuild,
    WordsFailedError,
    async_build_announcement,
    read_build,
)
from custom_components.cast_studio.announcements import get_announcements

from .conftest import SAM_ID, announcement_subentry, at, make_entry, person_subentry

if TYPE_CHECKING:
    from pathlib import Path

    from homeassistant.core import HomeAssistant

    from custom_components.cast_studio.announcements import Announcement

BUILD = "custom_components.cast_studio.announcement_build"
CREDS = {"platform": "gemini", "api_key": "fake"}


def _ffmpeg() -> AsyncMock:
    async def _run(_hass: Any, cmd: list[str], **_kw: Any) -> bool:
        from pathlib import Path

        Path(cmd[-1]).write_bytes(b"media")
        return True

    return AsyncMock(side_effect=_run)


def _tts() -> AsyncMock:
    async def _speak(
        _h: Any, _text: str, _v: str, _p: str, path: str, *_a: Any, **_k: Any
    ) -> str:
        from pathlib import Path

        Path(path).write_bytes(b"wav")
        return path

    return AsyncMock(side_effect=_speak)


def _image() -> AsyncMock:
    async def _paint(_h: Any, _prompt: str, path: str, *_a: Any, **_k: Any) -> str:
        from pathlib import Path

        Path(path).write_bytes(b"png")
        return path

    return AsyncMock(side_effect=_paint)


def _render() -> MagicMock:
    def _draw(_card: dict[str, Any], path: str, *_a: Any) -> str:
        from pathlib import Path

        Path(path).write_bytes(b"jpg")
        return path

    return MagicMock(side_effect=_draw)


@pytest.fixture
def mocks() -> dict[str, Any]:
    """Patch the builder's boundaries: AI, TTS, slides and FFmpeg."""
    found: dict[str, Any] = {
        "words": AsyncMock(return_value="Welcome home, Sam."),
        "tts": _tts(),
        "image": _image(),
        "render": _render(),
        "ffmpeg": _ffmpeg(),
    }
    patchers = [
        patch(f"{BUILD}.async_generate_announcement_words", new=found["words"]),
        patch(f"{BUILD}.async_synthesize_speech", new=found["tts"]),
        patch(f"{BUILD}.async_generate_ai_image", new=found["image"]),
        patch(f"{BUILD}.render_slide", new=found["render"]),
        patch(
            "custom_components.cast_studio.segments.async_run_ffmpeg",
            new=found["ffmpeg"],
        ),
        patch(f"{BUILD}.get_audio_duration", return_value=3.0),
    ]
    for p in patchers:
        p.start()
    yield found
    for p in reversed(patchers):
        p.stop()


def _announcement(hass: HomeAssistant, **data: Any) -> Announcement:
    entry = make_entry(
        [
            person_subentry(SAM_ID, "Sam", phonetic_name="Sahm"),
            announcement_subentry("a1", "Freezer Door", **data),
        ]
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return get_announcements(entry)[0]


async def _build(
    hass: HomeAssistant,
    directory: Path,
    announcement: Announcement,
    previous: AnnouncementBuild | None = None,
    now: datetime | None = None,
) -> AnnouncementBuild:
    return await async_build_announcement(
        hass, directory, announcement, CREDS, now or at(9), previous
    )


async def test_verbatim_visual_build_writes_files_and_sidecar(
    hass: HomeAssistant, tmp_path: Path, mocks: dict[str, Any]
) -> None:
    ann = _announcement(hass)
    build = await _build(hass, tmp_path, ann)
    assert build.status == "ready"
    assert build.words == "Close the freezer door"
    assert build.persona_id == "default_host"
    assert build.build_day is None
    assert build.audio_duration == 3.0
    assert build.clip_duration == pytest.approx(3.0 + 2.5)
    for path in (build.audio_path, build.clip_path, build.slide_path):
        assert path is not None
        assert (tmp_path / path.rsplit("/", 1)[-1]).is_file()
    assert read_build(tmp_path) == build
    mocks["words"].assert_not_called()


async def test_audio_announcement_builds_no_slide(
    hass: HomeAssistant, tmp_path: Path, mocks: dict[str, Any]
) -> None:
    build = await _build(hass, tmp_path, _announcement(hass, output_format="audio"))
    mocks["image"].assert_not_called()
    mocks["render"].assert_not_called()
    assert build.clip_path is None
    assert build.slide_path is None


async def test_creative_words_come_from_ai_with_persona(
    hass: HomeAssistant, tmp_path: Path, mocks: dict[str, Any]
) -> None:
    ann = _announcement(hass, wording="creative", message="", personas=["elena"])
    build = await _build(hass, tmp_path, ann)
    assert build.words == "Welcome home, Sam."
    assert build.persona_id == "elena"
    assert mocks["words"].call_args.args[2].persona_id == "elena"
    assert mocks["tts"].call_args.kwargs["voice_profile"]


async def test_creative_failure_with_previous_raises_words_failed(
    hass: HomeAssistant, tmp_path: Path, mocks: dict[str, Any]
) -> None:
    ann = _announcement(hass, wording="creative")
    previous = await _build(hass, tmp_path, ann)
    mocks["words"].side_effect = AIClientError("quota")
    with pytest.raises(WordsFailedError, match="quota"):
        await _build(hass, tmp_path, ann, previous)
    assert read_build(tmp_path) == previous


async def test_creative_failure_without_previous_speaks_message_then_name(
    hass: HomeAssistant, tmp_path: Path, mocks: dict[str, Any]
) -> None:
    mocks["words"].side_effect = AIClientError("quota")
    ann = _announcement(hass, wording="creative")
    build = await _build(hass, tmp_path, ann)
    assert build.words == "Close the freezer door"
    assert build.status == "fallback"
    assert build.note == "AI words unavailable"

    build = await _build(hass, tmp_path / "two", replace(ann, message=""))
    assert build.words == "Freezer Door"


async def test_no_ai_image_falls_back_to_plain_slide(
    hass: HomeAssistant, tmp_path: Path, mocks: dict[str, Any]
) -> None:
    mocks["image"].side_effect = None
    mocks["image"].return_value = None
    build = await _build(hass, tmp_path, _announcement(hass))
    card = mocks["render"].call_args.args[0]
    assert card["title"] == "Freezer Door"
    assert build.status == "fallback"
    assert build.note == "AI slide unavailable"
    assert build.words == "Close the freezer door"
    assert build.slide_path is not None
    assert build.clip_path is not None


async def test_unreadable_picture_is_skipped(
    hass: HomeAssistant,
    tmp_path: Path,
    mocks: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    (media / "door.jpg").write_bytes(b"door")
    hass.config.media_dirs = {"local": str(media)}
    ann = _announcement(
        hass,
        pictures=[
            "media-source://media_source/local/door.jpg",
            "media-source://media_source/local/gone.jpg",
        ],
    )
    with caplog.at_level(logging.WARNING):
        await _build(hass, tmp_path / "out", ann)
    assert mocks["image"].call_args.kwargs["reference_images"] == [
        ("image/jpeg", b"door")
    ]
    assert "gone.jpg" in caplog.text


async def test_fallback_slide_uses_first_readable_picture(
    hass: HomeAssistant, tmp_path: Path, mocks: dict[str, Any]
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    (media / "door.png").write_bytes(b"door")
    hass.config.media_dirs = {"local": str(media)}
    mocks["image"].side_effect = None
    mocks["image"].return_value = None
    ann = _announcement(hass, pictures=["media-source://media_source/local/door.png"])
    await _build(hass, tmp_path / "out", ann)
    card = mocks["render"].call_args.args[0]
    assert card["image_path"] == str(media / "door.png")
    assert card["layout"] == "photo"


async def test_pictures_capped_at_four(
    hass: HomeAssistant, tmp_path: Path, mocks: dict[str, Any]
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    for n in range(5):
        (media / f"{n}.jpg").write_bytes(b"x")
    hass.config.media_dirs = {"local": str(media)}
    ann = _announcement(
        hass, pictures=[f"media-source://media_source/local/{n}.jpg" for n in range(5)]
    )
    await _build(hass, tmp_path / "out", ann)
    assert len(mocks["image"].call_args.kwargs["reference_images"]) == 4


async def test_speech_uses_household_pronunciations(
    hass: HomeAssistant, tmp_path: Path, mocks: dict[str, Any]
) -> None:
    build = await _build(
        hass, tmp_path, _announcement(hass, message="Welcome home, Sam")
    )
    assert mocks["tts"].call_args.args[1] == "Welcome home, Sahm"
    assert build.words == "Welcome home, Sam"


async def test_old_versions_removed_but_previous_kept(
    hass: HomeAssistant, tmp_path: Path, mocks: dict[str, Any]
) -> None:
    ann = _announcement(hass)
    first = await _build(hass, tmp_path, ann)
    second = await _build(hass, tmp_path, ann, first)
    third = await _build(hass, tmp_path, ann, second)
    names = {p.name for p in tmp_path.iterdir()}

    def files(build: AnnouncementBuild) -> set[str]:
        return {
            p.rsplit("/", 1)[-1]
            for p in (build.audio_path, build.clip_path, build.slide_path)
            if p
        }

    assert files(first).isdisjoint(names)
    assert files(second) <= names
    assert files(third) <= names
    assert "build.json" in names


async def test_daily_build_records_build_day(
    hass: HomeAssistant, tmp_path: Path, mocks: dict[str, Any]
) -> None:
    ann = _announcement(hass, freshness="daily")
    build = await _build(hass, tmp_path, ann, now=at(2))
    assert build.build_day.isoformat() == "2026-10-19"
