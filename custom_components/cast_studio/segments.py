"""
Per-person segments for the new briefing engine (spec §5.2, §5.4, §5.6).

A segment is one stored piece of a briefing (greeting, weather, closing, ...).
Segments are built one by one per person and briefing day, and stored as an
audio file plus, for visual routines, a video clip. Every segment is encoded
with the same parameters, so routines can be stitched by stream copy.

On-disk layout, all inside ``output_dir``::

    people/<person_id>/<YYYY-MM-DD>/manifest.json   segments of one briefing day
    people/<person_id>/<YYYY-MM-DD>/<key>.<version>.mp3|.mp4
    people/<person_id>/all_heard/manifest.json       the "That's everything" clip

Every build writes new, versioned file names and then switches the manifest
entry, so a rebuild never overwrites a file a running play may be streaming.
Superseded files are left in place: a briefing day's folder goes with the
daily prune, and old all-heard files are deleted once safely out of use.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import DEFAULT_HOST_VOICE, DEFAULT_SCRIPTURE_VOICE, LOGGER
from .ffmpeg_runner import async_run_ffmpeg, build_slide_clip_command
from .generator import (
    DEFAULT_SLIDE_PAD_SECONDS,
    FINAL_SLIDE_PAD_SECONDS,
    GeneratorError,
    _async_compile_single_step,
    _is_unconfigured_optional_step,
    async_extract_entity_context,
)
from .helpers import async_run_in_executor, write_json_atomic
from .people import (
    PERSON_COMMUTE_DRIVE_RETURN_SENSOR,
    PERSON_COMMUTE_DRIVE_SENSOR,
    PERSON_COMMUTE_TRANSIT_RETURN_SENSOR,
    PERSON_COMMUTE_TRANSIT_SENSOR,
    PERSON_HOST_PERSONAS,
    PERSON_READER_PERSONAS,
    SEGMENT_TYPES,
    briefing_day,
    is_content_fresh,
    person_calendars,
)
from .personas import ROLE_HOST, ROLE_READER, pick_persona
from .slide_renderer import CANVAS_HEIGHT, CANVAS_WIDTH

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

    from homeassistant.core import HomeAssistant

    from .people import Person, Routine

NOSIGNOFF_SUFFIX = "_nosignoff"
ALL_HEARD_KEY = "all_heard"
ALL_HEARD_TEXT = "That's everything for this morning."

PEOPLE_DIR = "people"
ALL_HEARD_DIR = "all_heard"
MANIFEST_FILE = "manifest.json"

# An all-heard clip superseded this long ago can no longer be playing.
ALL_HEARD_KEEP_SUPERSEDED = timedelta(hours=1)

DEFAULT_HOST_PERSONALITY = "news_anchor"
DEFAULT_SCRIPTURE_PERSONALITY = "life_coach"

# One fixed encode for every segment so the concat demuxer can ``-c copy``.
# Audio is MP3; clips reuse the slide clip
# parameters from ffmpeg_runner, pinned to the slide canvas size, one frame
# rate, mono audio and one timescale.
SEGMENT_AUDIO_ARGS: tuple[str, ...] = (
    "-vn",
    "-c:a",
    "libmp3lame",
    "-b:a",
    "192k",
    "-ar",
    "44100",
    "-ac",
    "1",
)
SEGMENT_CLIP_UNIFORM_ARGS: tuple[str, ...] = (
    "-vf",
    f"scale={CANVAS_WIDTH}:{CANVAS_HEIGHT}:force_original_aspect_ratio=decrease,"
    f"pad={CANVAS_WIDTH}:{CANVAS_HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1",
    "-r",
    "25",
    "-ac",
    "1",
    "-video_track_timescale",
    "12800",
)


async def async_encode_segment_audio(
    hass: HomeAssistant, source: str, output: str
) -> bool:
    """Encode ``source`` to the uniform segment MP3; False if FFmpeg fails."""
    return await async_run_ffmpeg(
        hass, ["ffmpeg", "-y", "-i", source, *SEGMENT_AUDIO_ARGS, output]
    )


async def async_encode_segment_clip(  # noqa: PLR0913
    hass: HomeAssistant,
    slide: str,
    audio: str,
    output: str,
    *,
    duration: float,
    pad_seconds: float,
) -> bool:
    """Mux ``slide`` and ``audio`` into a uniform segment clip; False on failure."""
    cmd = build_slide_clip_command(
        slide, audio, output, duration=duration, pad_seconds=pad_seconds
    )
    # The uniform args go before the output path, which comes last.
    return await async_run_ffmpeg(
        hass, [*cmd[:-1], *SEGMENT_CLIP_UNIFORM_ARGS, cmd[-1]]
    )


def is_safe_subpath(target_path: Path, base_path: Path) -> bool:
    """Return True if target_path is strictly a child of base_path."""
    resolved_base = base_path.resolve()
    resolved_target = target_path.resolve()
    return resolved_target != resolved_base and resolved_target.is_relative_to(
        resolved_base
    )


def segment_key(segment_type: str, *, include_signoff: bool = True) -> str:
    """Return the storage key for a segment type."""
    if segment_type == "daily_encouragement" and not include_signoff:
        return f"{segment_type}{NOSIGNOFF_SUFFIX}"
    return segment_type


def segment_type_of(key: str) -> str:
    """Return the segment type a storage key was built from."""
    return key.removesuffix(NOSIGNOFF_SUFFIX)


def routine_segment_keys(routine: Routine) -> list[str]:
    """
    Return the routine's segment keys in order.

    With a ``closing`` segment, the encouragement is built without its own
    sign-off so the name is never said twice (spec §5.2).
    """
    include_signoff = "closing" not in routine.segments
    return [
        segment_key(segment_type, include_signoff=include_signoff)
        for segment_type in routine.segments
    ]


# Each role's settings: persona picks, typed voice and style, and defaults.
_ROLE_SETTINGS: dict[str, tuple[str, str, str, str, str]] = {
    ROLE_HOST: (
        PERSON_HOST_PERSONAS,
        "host_voice",
        "host_personality",
        DEFAULT_HOST_VOICE,
        DEFAULT_HOST_PERSONALITY,
    ),
    ROLE_READER: (
        PERSON_READER_PERSONAS,
        "scripture_voice",
        "scripture_personality",
        DEFAULT_SCRIPTURE_VOICE,
        DEFAULT_SCRIPTURE_PERSONALITY,
    ),
}


def _role_voice(person: Person, role: str, day: date) -> dict[str, Any]:
    """
    Return the step keys for whoever speaks a role on ``day``.

    A picked persona wins; with none, the typed voice and style (or the
    defaults) are used exactly as before personas existed.
    """
    personas_key, voice_key, personality_key, voice, personality = _ROLE_SETTINGS[role]
    settings = person.settings
    if persona := pick_persona(settings.get(personas_key), role, person.person_id, day):
        return {
            "voice": persona.gemini_voice,
            "voice_personality": persona.character,
            "voice_profile": persona.delivery,
            "openai_voice": persona.openai_voice,
        }
    return {
        "voice": settings.get(voice_key) or voice,
        "voice_personality": settings.get(personality_key) or personality,
    }


def build_segment_step(
    person: Person,
    segment_key: str,
    day: date | None = None,
    *,
    visual: bool = True,
) -> dict[str, Any]:
    """
    Return the generator step for one of the person's segments.

    ``day`` is the briefing day the segment is built for; it decides which of
    the person's personas speaks when they picked several. ``visual`` builds
    a slide and clip as well as the audio.
    """
    segment_type = segment_type_of(segment_key)
    if segment_type not in SEGMENT_TYPES:
        msg = f"Unknown CastStudio segment '{segment_key}'"
        raise ValueError(msg)

    settings = person.settings
    if day is None:
        day = briefing_day(person, dt_util.now())
    host = _role_voice(person, ROLE_HOST, day)
    reader = _role_voice(person, ROLE_READER, day)
    scripture_entity = settings.get("scripture_entity") or ""

    step: dict[str, Any] = {
        "name": segment_type,
        "type": segment_type,
        "visual": visual,
        "standalone_segment": True,
        **(reader if segment_type in ("scripture", "daily_encouragement") else host),
    }
    if settings.get("conversation_agent"):
        step["conversation_agent"] = settings["conversation_agent"]

    if segment_type == "greeting":
        step.update(
            is_first_step=True,
            title=settings.get("greeting_title") or person.name,
            subtitle=settings.get("greeting_subtitle") or "Good Morning",
            context=f"A cheerful, energizing morning greeting for {person.name}.",
        )
        if settings.get("greeting_image"):
            step["image_path"] = settings["greeting_image"]
    elif segment_type == "weather":
        step["entity_id"] = settings.get("weather_entity") or ""
    elif segment_type == "agenda":
        calendars = person_calendars(settings)
        todo = settings.get("todo_entity") or ""
        step.update(
            calendar_entities=calendars,
            todo_entity=todo,
            entity_ids=[*calendars, *([todo] if todo else [])],
        )
    elif segment_type == "commute":
        drive = settings.get(PERSON_COMMUTE_DRIVE_SENSOR) or None
        transit = settings.get(PERSON_COMMUTE_TRANSIT_SENSOR) or None
        # A driving or transit person only uses that mode's sensor (spec §5.6).
        relevant = {
            "driving": [drive],
            "transit": [transit],
        }.get(person.transportation_mode, [drive, transit])
        # Stored segments can play hours after they are built: no clock ETA.
        step.update(
            destination_zone=person.commute_destination_zone,
            transportation_mode=person.transportation_mode,
            commute_drive_sensor=drive,
            commute_transit_sensor=transit,
            commute_drive_return_sensor=(
                settings.get(PERSON_COMMUTE_DRIVE_RETURN_SENSOR) or None
            ),
            commute_transit_return_sensor=(
                settings.get(PERSON_COMMUTE_TRANSIT_RETURN_SENSOR) or None
            ),
            commute_sensors=[eid for eid in relevant if eid],
            relative_eta=True,
        )
    elif segment_type == "scripture":
        step["entity_id"] = scripture_entity
    elif segment_type == "daily_encouragement":
        step.update(
            title="Today's Takeaway",
            subtitle="Daily Encouragement",
            include_signoff=not segment_key.endswith(NOSIGNOFF_SUFFIX),
        )
        if scripture_entity:
            step["entity_id"] = scripture_entity
    return step


def _all_heard_step(
    person: Person, day: date | None = None, *, visual: bool = True
) -> dict[str, Any]:
    """Return the step for the all-heard line: fixed narration, no LLM."""
    step = build_segment_step(person, "closing", day, visual=visual)
    step.update(
        name=ALL_HEARD_KEY,
        type=ALL_HEARD_KEY,
        narration=ALL_HEARD_TEXT,
        title=ALL_HEARD_TEXT,
        subtitle="",
    )
    return step


@dataclass
class StoredSegment:
    """
    One built segment on disk.

    ``duration`` is the clip length for visual segments (speech plus slide pad)
    and the speech length for audio-only people.
    """

    key: str
    audio_path: str
    clip_path: str | None
    duration: float
    built_at: datetime
    built_day: date


def _segment_to_json(segment: StoredSegment) -> dict[str, Any]:
    """Serialise a segment; file names are relative to its manifest folder."""
    return {
        "audio": Path(segment.audio_path).name,
        "clip": Path(segment.clip_path).name if segment.clip_path else None,
        "duration": segment.duration,
        "built_at": segment.built_at.isoformat(),
        "built_day": segment.built_day.isoformat(),
    }


def _segment_from_json(key: str, data: dict[str, Any], base: Path) -> StoredSegment:
    clip = data.get("clip")
    return StoredSegment(
        key=key,
        audio_path=str(base / data["audio"]),
        clip_path=str(base / clip) if clip else None,
        duration=float(data["duration"]),
        built_at=datetime.fromisoformat(data["built_at"]),
        built_day=date.fromisoformat(data["built_day"]),
    )


def _read_manifest(directory: Path) -> dict[str, StoredSegment]:
    """Read a manifest, dropping entries whose files are gone."""
    path = directory / MANIFEST_FILE
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = raw.get("segments") or {}
        segments = {
            key: _segment_from_json(key, data, directory)
            for key, data in entries.items()
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as err:
        LOGGER.warning("Ignoring unreadable segment manifest %s: %s", path, err)
        return {}
    return {
        key: seg
        for key, seg in segments.items()
        if Path(seg.audio_path).is_file()
        and (seg.clip_path is None or Path(seg.clip_path).is_file())
    }


def versioned_path(path: Path) -> Path:
    """Return ``path`` with a fresh version tag: ``<stem>.<version><suffix>``."""
    return path.with_name(f"{path.stem}.{secrets.token_hex(4)}{path.suffix}")


def _commit_segment(
    directory: Path, segment: StoredSegment, staged: dict[str, str]
) -> dict[str, StoredSegment]:
    """
    Move a built segment's files into place and record it in the manifest.

    Each staged file gets a new, versioned name, so the files of the entry it
    replaces stay untouched for any play still streaming them. Returns the
    new manifest.
    """
    directory.mkdir(parents=True, exist_ok=True)
    moved: dict[str, str] = {}
    for source, target in staged.items():
        final = versioned_path(Path(target))
        # shutil.move: scratch (/tmp) and the output dir may be different filesystems.
        shutil.move(source, final)
        moved[target] = str(final)
    segment = dataclasses.replace(
        segment,
        audio_path=moved.get(segment.audio_path, segment.audio_path),
        clip_path=(
            moved.get(segment.clip_path, segment.clip_path)
            if segment.clip_path
            else None
        ),
    )
    segments = _read_manifest(directory)
    segments[segment.key] = segment
    payload = {
        "segments": {key: _segment_to_json(seg) for key, seg in segments.items()}
    }
    write_json_atomic(directory / MANIFEST_FILE, payload)
    return segments


def _remove_earlier_days(person_dir: Path, today: date) -> list[str]:
    """Delete briefing-day folders older than ``today``."""
    removed: list[str] = []
    if not person_dir.is_dir():
        return removed
    for child in person_dir.iterdir():
        try:
            day = date.fromisoformat(child.name)
        except ValueError:
            continue
        if child.is_dir() and day < today:
            shutil.rmtree(child, ignore_errors=True)
            removed.append(str(child))
    return removed


def _remove_superseded_all_heard(directory: Path, now: datetime) -> list[str]:
    """
    Delete all-heard files the manifest no longer points to.

    Only once the current clip was built long enough ago that no play of the
    one it replaced can still be running.
    """
    current = _read_manifest(directory).get(ALL_HEARD_KEY)
    if current is None or now - current.built_at < ALL_HEARD_KEEP_SUPERSEDED:
        return []
    keep = {MANIFEST_FILE, Path(current.audio_path).name}
    if current.clip_path:
        keep.add(Path(current.clip_path).name)
    removed: list[str] = []
    for child in directory.iterdir():
        if child.is_file() and child.name not in keep:
            child.unlink(missing_ok=True)
            removed.append(str(child))
    return removed


class SegmentStore:
    """Stored segments for every person, by briefing day."""

    def __init__(self, hass: HomeAssistant, output_dir: str) -> None:
        """Initialise the store under ``output_dir``."""
        self._hass = hass
        self._output_dir = Path(output_dir)
        # person_id -> (briefing day, key -> segment)
        self._days: dict[str, tuple[date, dict[str, StoredSegment]]] = {}
        self._all_heard: dict[str, StoredSegment] = {}
        self._loaded: set[tuple[str, date]] = set()
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, person: Person) -> asyncio.Lock:
        """Return the lock serialising one person's manifest writes."""
        return self._locks.setdefault(person.person_id, asyncio.Lock())

    def person_dir(self, person: Person) -> Path:
        """Return the person's folder, which must lie inside ``output_dir``."""
        path = self._output_dir / PEOPLE_DIR / person.person_id
        if not is_safe_subpath(path, self._output_dir):
            msg = f"Segment folder for '{person.person_id}' is outside the output dir"
            raise ValueError(msg)
        return path

    def day_dir(self, person: Person, day: date) -> Path:
        """Return the folder holding the person's segments for one briefing day."""
        return self.person_dir(person) / day.isoformat()

    def get_fresh(
        self, person: Person, key: str, now: datetime
    ) -> StoredSegment | None:
        """Return the stored segment if it is fresh at ``now`` (spec §5.4)."""
        cached = self._days.get(person.person_id)
        if cached is None or cached[0] != briefing_day(person, now):
            return None
        segment = cached[1].get(key)
        if segment is None or not is_content_fresh(
            person, segment.built_at, segment.built_day, now
        ):
            return None
        return segment

    def all_heard_clip(self, person: Person) -> StoredSegment | None:
        """Return the person's "That's everything" clip, if one was built."""
        return self._all_heard.get(person.person_id)

    async def async_load(self, person: Person, now: datetime) -> None:
        """Read the person's manifests for the current briefing day from disk."""
        day = briefing_day(person, now)
        if (person.person_id, day) in self._loaded:
            return
        segments = await async_run_in_executor(
            self._hass, _read_manifest, self.day_dir(person, day)
        )
        self._days[person.person_id] = (day, segments)
        all_heard = await async_run_in_executor(
            self._hass, _read_manifest, self.person_dir(person) / ALL_HEARD_DIR
        )
        if ALL_HEARD_KEY in all_heard:
            self._all_heard[person.person_id] = all_heard[ALL_HEARD_KEY]
        self._loaded.add((person.person_id, day))

    async def async_prune(self, person: Person, now: datetime) -> None:
        """Delete the person's folders from earlier briefing days."""
        today = briefing_day(person, now)
        removed = await async_run_in_executor(
            self._hass, _remove_earlier_days, self.person_dir(person), today
        )
        all_heard_dir = self.person_dir(person) / ALL_HEARD_DIR
        if await async_run_in_executor(self._hass, all_heard_dir.is_dir):
            removed += await async_run_in_executor(
                self._hass, _remove_superseded_all_heard, all_heard_dir, now
            )
        if removed:
            LOGGER.debug("Pruned stale segment folders: %s", removed)
        cached = self._days.get(person.person_id)
        if cached is not None and cached[0] < today:
            del self._days[person.person_id]
        self._loaded = {
            (pid, day)
            for pid, day in self._loaded
            if pid != person.person_id or day >= today
        }

    async def async_store(
        self,
        person: Person,
        directory: Path,
        segment: StoredSegment,
        staged: dict[str, str],
    ) -> StoredSegment:
        """Move a built segment's staged files into place and record it."""
        async with self._lock(person):
            segments = await async_run_in_executor(
                self._hass, _commit_segment, directory, segment, staged
            )
        if segment.key == ALL_HEARD_KEY:
            self._all_heard[person.person_id] = segments[ALL_HEARD_KEY]
        else:
            self._days[person.person_id] = (segment.built_day, segments)
        return segments[segment.key]


async def _async_build_one(  # noqa: PLR0913
    hass: HomeAssistant,
    person: Person,
    key: str,
    step: dict[str, Any],
    ai_creds: dict[str, Any],
    scratch: Path,
    target_dir: Path,
    now: datetime,
) -> tuple[StoredSegment, dict[str, str]]:
    """Build one segment into ``scratch``; return it and the files to move."""
    if segment_type_of(key) == "commute":
        # The commute needs the person's own zone and sensor (spec §5.6).
        if not step.get("destination_zone"):
            msg = f"No work zone configured for {person.name}"
            raise HomeAssistantError(msg)
        if not step.get("commute_sensors"):
            msg = f"No commute sensor configured for {person.name}"
            raise HomeAssistantError(msg)
    if _is_unconfigured_optional_step(hass, step):
        msg = f"Segment '{key}' has no configured or available entity"
        raise GeneratorError(msg)
    if context := await async_extract_entity_context(hass, step):
        step["context"] = context

    is_visual = bool(step.get("visual"))
    is_last = segment_type_of(key) in ("closing", ALL_HEARD_KEY)
    tts_path = scratch / f"{key}.wav"
    slide_path = scratch / f"{key}.jpg"
    # No clip target: the generator renders the slide, and the clip is
    # encoded below with the uniform segment parameters.
    audio_path, duration, pad_seconds, _ = await _async_compile_single_step(
        hass,
        step,
        str(step["name"]),
        {"name": person.name, "phonetic_name": person.phonetic_name},
        ai_creds,
        is_visual=is_visual,
        default_pad_seconds=(
            FINAL_SLIDE_PAD_SECONDS if is_last else DEFAULT_SLIDE_PAD_SECONDS
        ),
        audio_target_path=str(tts_path),
        slide_target_path=str(slide_path) if is_visual else None,
    )

    staged: dict[str, str] = {}
    audio_out = scratch / f"{key}.mp3"
    if not await async_encode_segment_audio(hass, audio_path, str(audio_out)):
        msg = f"FFmpeg audio encode failed for segment '{key}'"
        raise GeneratorError(msg)
    staged[str(audio_out)] = str(target_dir / audio_out.name)

    clip_target: str | None = None
    if is_visual:
        clip_out = scratch / f"{key}.mp4"
        if not await async_encode_segment_clip(
            hass,
            str(slide_path),
            audio_path,
            str(clip_out),
            duration=duration,
            pad_seconds=pad_seconds,
        ):
            msg = f"FFmpeg clip encode failed for segment '{key}'"
            raise GeneratorError(msg)
        clip_target = str(target_dir / clip_out.name)
        staged[str(clip_out)] = clip_target

    segment = StoredSegment(
        key=key,
        audio_path=str(target_dir / audio_out.name),
        clip_path=clip_target,
        duration=duration,
        built_at=now,
        built_day=briefing_day(person, now),
    )
    return segment, staged


async def async_build_segments(  # noqa: PLR0913
    hass: HomeAssistant,
    store: SegmentStore,
    person: Person,
    keys: Iterable[str],
    ai_creds: dict[str, Any],
    *,
    now: datetime,
    visual_keys: Collection[str] = (),
    all_heard_visual: bool = False,
) -> dict[str, StoredSegment | Exception]:
    """
    Build and store the person's segments for the briefing day of ``now``.

    Each key is built independently: a failure is logged and returned as the
    key's value, and the remaining keys are still built. The all-heard clip
    is also built when it is missing for the day. Keys in ``visual_keys``
    (and the all-heard line when ``all_heard_visual``) get a slide and clip.
    """
    await store.async_load(person, now)
    creds = dict(ai_creds or {})
    if agent := person.settings.get("conversation_agent"):
        creds["agent_id"] = agent

    today = briefing_day(person, now)
    jobs: list[tuple[str, Path]] = [
        (key, store.day_dir(person, today)) for key in dict.fromkeys(keys)
    ]
    all_heard = store.all_heard_clip(person)
    if all_heard is None or all_heard.built_day != today:
        jobs.append((ALL_HEARD_KEY, store.person_dir(person) / ALL_HEARD_DIR))

    scratch = Path(
        await async_run_in_executor(hass, tempfile.mkdtemp, prefix="cast_studio_seg_")
    )
    results: dict[str, StoredSegment | Exception] = {}
    try:
        for key, target_dir in jobs:
            try:
                step = (
                    _all_heard_step(person, today, visual=all_heard_visual)
                    if key == ALL_HEARD_KEY
                    else build_segment_step(
                        person, key, today, visual=key in visual_keys
                    )
                )
                segment, staged = await _async_build_one(
                    hass, person, key, step, creds, scratch, target_dir, now
                )
                segment = await store.async_store(person, target_dir, segment, staged)
            except Exception as err:  # noqa: BLE001 - one segment must not stop the rest
                LOGGER.warning(
                    "CastStudio could not build segment '%s' for %s: %s",
                    key,
                    person.name,
                    err,
                    exc_info=err,
                )
                if key != ALL_HEARD_KEY:
                    results[key] = err
            else:
                if key != ALL_HEARD_KEY:
                    results[key] = segment
    finally:
        await async_run_in_executor(hass, shutil.rmtree, scratch, ignore_errors=True)
    return results
