"""
Build one announcement: its words, speech, slide and clip, plus a sidecar.

On-disk layout, inside ``output_dir``::

    announcements/<announcement_id>/build.json            what the build is
    announcements/<announcement_id>/speech.<version>.mp3   the spoken words
    announcements/<announcement_id>/slide.<version>.png|jpg
    announcements/<announcement_id>/clip.<version>.mp4     slide plus speech

Every build writes new, versioned file names and then switches the sidecar,
so a play still streaming the previous build is never overwritten. The
previous build's files are kept; older ones go after each successful build.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import mimetypes
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from homeassistant.exceptions import HomeAssistantError

from .ai_client import async_generate_ai_image, async_generate_announcement_words
from .announcements import (
    MAX_PICTURES,
    announcement_day,
    pick_build_persona,
    resolve_local_picture,
    settings_fingerprint,
)
from .const import LOGGER
from .generator import FINAL_SLIDE_PAD_SECONDS, get_audio_duration
from .helpers import async_run_in_executor, write_json_atomic
from .people import get_loaded_entry, get_people
from .pronunciation import apply_pronunciations, build_household_lexicon
from .segments import (
    async_encode_segment_audio,
    async_encode_segment_clip,
    is_safe_subpath,
    versioned_path,
)
from .slide_renderer import render_slide
from .tts_client import async_synthesize_speech

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .announcements import Announcement

ANNOUNCEMENTS_DIR = "announcements"
SIDECAR = "build.json"

NOTE_NO_WORDS = "AI words unavailable"
NOTE_NO_SLIDE = "AI slide unavailable"


class WordsFailedError(HomeAssistantError):
    """The AI could not write new words; the previous build stays in use."""


@dataclass(frozen=True)
class AnnouncementBuild:
    """One built announcement on disk."""

    fingerprint: str
    build_day: date | None
    built_at: datetime
    words: str
    persona_id: str
    audio_path: str
    audio_duration: float
    clip_path: str | None
    clip_duration: float | None
    slide_path: str | None
    # Why parts of the build are stand-ins, if any are.
    note: str | None = None

    @property
    def status(self) -> Literal["ready", "fallback"]:
        """Return "fallback" when some part is a stand-in."""
        return "fallback" if self.note else "ready"

    def files(self) -> set[str]:
        """Return the names of this build's files inside its folder."""
        return {
            Path(path).name
            for path in (self.audio_path, self.clip_path, self.slide_path)
            if path
        }


def announcement_dir(output_dir: str, announcement_id: str) -> Path:
    """Return an announcement's folder, which must lie inside ``output_dir``."""
    base = Path(output_dir)
    path = base / ANNOUNCEMENTS_DIR / announcement_id
    if not is_safe_subpath(path, base):
        msg = f"Announcement folder for '{announcement_id}' is outside the output dir"
        raise ValueError(msg)
    return path


def _build_to_json(build: AnnouncementBuild) -> dict[str, Any]:
    return {
        "fingerprint": build.fingerprint,
        "build_day": build.build_day.isoformat() if build.build_day else None,
        "built_at": build.built_at.isoformat(),
        "words": build.words,
        "persona_id": build.persona_id,
        "audio": Path(build.audio_path).name,
        "audio_duration": build.audio_duration,
        "clip": Path(build.clip_path).name if build.clip_path else None,
        "clip_duration": build.clip_duration,
        "slide": Path(build.slide_path).name if build.slide_path else None,
        "note": build.note,
    }


def read_build(directory: Path) -> AnnouncementBuild | None:
    """Read the sidecar; None if it is missing, unreadable or its audio is gone."""
    try:
        raw = json.loads((directory / SIDECAR).read_text(encoding="utf-8"))

        def _file(name: Any) -> str | None:
            if not isinstance(name, str) or Path(name).name != name:
                return None
            return str(directory / name)

        build = AnnouncementBuild(
            fingerprint=str(raw["fingerprint"]),
            build_day=(
                date.fromisoformat(raw["build_day"]) if raw.get("build_day") else None
            ),
            built_at=datetime.fromisoformat(raw["built_at"]),
            words=str(raw["words"]),
            persona_id=str(raw["persona_id"]),
            audio_path=_file(raw["audio"]) or "",
            audio_duration=float(raw["audio_duration"]),
            clip_path=_file(raw.get("clip")),
            clip_duration=(
                float(raw["clip_duration"])
                if raw.get("clip_duration") is not None
                else None
            ),
            slide_path=_file(raw.get("slide")),
            note=raw.get("note"),
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if not build.audio_path or not Path(build.audio_path).is_file():
        return None
    if build.clip_path and not Path(build.clip_path).is_file():
        build = dataclasses.replace(build, clip_path=None, clip_duration=None)
    return build


def write_build(directory: Path, build: AnnouncementBuild) -> None:
    """Write the sidecar atomically."""
    write_json_atomic(directory / SIDECAR, _build_to_json(build))


def _commit(
    directory: Path,
    staged: dict[str, Path],
    build: AnnouncementBuild,
    previous: AnnouncementBuild | None,
) -> AnnouncementBuild:
    """Move staged files in with versioned names, write the sidecar, prune."""
    directory.mkdir(parents=True, exist_ok=True)
    moved: dict[str, str] = {}
    for role, source in staged.items():
        final = versioned_path(directory / f"{role}{source.suffix}")
        shutil.move(source, final)
        moved[role] = str(final)
    build = dataclasses.replace(
        build,
        audio_path=moved["speech"],
        clip_path=moved.get("clip"),
        slide_path=moved.get("slide"),
    )
    write_build(directory, build)
    keep = {SIDECAR, *build.files(), *(previous.files() if previous else ())}
    for child in directory.iterdir():
        if child.is_file() and child.name not in keep:
            child.unlink(missing_ok=True)
    return build


def _read_pictures(
    hass: HomeAssistant, announcement: Announcement
) -> list[tuple[Path, str, bytes]]:
    """Read up to MAX_PICTURES pictures; unreadable ones are skipped."""
    pictures: list[tuple[Path, str, bytes]] = []
    for media_id in announcement.pictures[:MAX_PICTURES]:
        path = resolve_local_picture(hass, media_id)
        data: bytes | None = None
        if path is not None:
            with contextlib.suppress(OSError):
                data = path.read_bytes()
        if path is None or data is None:
            LOGGER.warning(
                "Skipping picture %s of announcement '%s': not readable",
                media_id,
                announcement.name,
            )
            continue
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        pictures.append((path, mime, data))
    return pictures


def _slide_prompt(announcement: Announcement, words: str) -> str:
    return (
        "Paint one 16:9 landscape slide for a smart display (shown at 1024x600) "
        "for a household announcement.\n"
        f"What the slide should show: {announcement.description}\n"
        f'The announcement says: "{words}"\n'
        "Any text on the slide must be short, large and spelled exactly as "
        "given. Reference pictures, if any, are attached in the order the "
        "description mentions them."
    )


async def _async_words(
    hass: HomeAssistant,
    announcement: Announcement,
    persona: Any,
    ai_creds: dict[str, Any],
    previous: AnnouncementBuild | None,
) -> tuple[str, str | None]:
    """Return the words and, when they are a stand-in, the fallback note."""
    if announcement.wording == "verbatim":
        return announcement.message, None
    try:
        words = await async_generate_announcement_words(
            hass, announcement.description, persona, ai_creds
        )
    except Exception as err:
        if previous is not None:
            raise WordsFailedError(str(err)) from err
        LOGGER.warning(
            "Could not write words for announcement '%s': %s", announcement.name, err
        )
        return announcement.message or announcement.name, NOTE_NO_WORDS
    return words, None


async def async_build_announcement(  # noqa: PLR0913
    hass: HomeAssistant,
    directory: Path,
    announcement: Announcement,
    ai_creds: dict[str, Any],
    now: datetime,
    previous: AnnouncementBuild | None,
) -> AnnouncementBuild:
    """
    Build an announcement into ``directory`` and return the new build.

    Raises WordsFailedError when creative words fail and a previous build
    exists, and HomeAssistantError when the speech or clip cannot be made.
    """
    persona = pick_build_persona(
        announcement, previous.persona_id if previous else None
    )
    words, note = await _async_words(hass, announcement, persona, ai_creds, previous)
    notes = [note] if note else []

    entry = get_loaded_entry(hass)
    lexicon = build_household_lexicon(get_people(entry)) if entry else []
    spoken = apply_pronunciations(words, lexicon)

    scratch = Path(
        await async_run_in_executor(hass, tempfile.mkdtemp, prefix="cast_studio_ann_")
    )
    try:
        wav = scratch / "speech.wav"
        wav = Path(
            await async_synthesize_speech(
                hass,
                spoken,
                persona.gemini_voice,
                persona.character,
                str(wav),
                ai_creds,
                voice_profile=persona.delivery,
                openai_voice=persona.openai_voice,
            )
            or wav
        )
        mp3 = scratch / "speech.mp3"
        if not await async_encode_segment_audio(hass, str(wav), str(mp3)):
            msg = f"FFmpeg audio encode failed for announcement '{announcement.name}'"
            raise HomeAssistantError(msg)
        audio_duration = await async_run_in_executor(
            hass, get_audio_duration, str(wav), fallback_text=words
        )
        staged: dict[str, Path] = {"speech": mp3}

        clip_duration: float | None = None
        if announcement.output_format == "visual":
            pictures = await async_run_in_executor(
                hass, _read_pictures, hass, announcement
            )
            slide = await async_generate_ai_image(
                hass,
                _slide_prompt(announcement, words),
                str(scratch / "slide.png"),
                ai_creds,
                reference_images=[(mime, data) for _, mime, data in pictures],
                aspect_ratio="16:9",
            )
            if slide is None:
                card: dict[str, Any] = {"title": announcement.name, "subtitle": ""}
                if pictures:
                    card.update(image_path=str(pictures[0][0]), layout="photo")
                slide = await async_run_in_executor(
                    hass, render_slide, card, str(scratch / "slide.jpg")
                )
                notes.append(NOTE_NO_SLIDE)
            slide_path = Path(slide)
            clip = scratch / "clip.mp4"
            clip_duration = round(audio_duration + FINAL_SLIDE_PAD_SECONDS, 2)
            if not await async_encode_segment_clip(
                hass,
                str(slide_path),
                str(wav),
                str(clip),
                duration=clip_duration,
                pad_seconds=FINAL_SLIDE_PAD_SECONDS,
            ):
                msg = (
                    f"FFmpeg clip encode failed for announcement '{announcement.name}'"
                )
                raise HomeAssistantError(msg)
            staged.update(slide=slide_path, clip=clip)

        build = AnnouncementBuild(
            fingerprint=settings_fingerprint(announcement),
            build_day=(
                announcement_day(announcement, now)
                if announcement.freshness == "daily"
                else None
            ),
            built_at=now,
            words=words,
            persona_id=persona.persona_id,
            audio_path="",
            audio_duration=audio_duration,
            clip_path=None,
            clip_duration=clip_duration,
            slide_path=None,
            note="; ".join(notes) or None,
        )
        return await async_run_in_executor(
            hass, _commit, directory, staged, build, previous
        )
    finally:
        await async_run_in_executor(hass, shutil.rmtree, scratch, ignore_errors=True)
