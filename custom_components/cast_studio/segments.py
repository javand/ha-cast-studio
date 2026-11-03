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
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import LOGGER
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
    SEGMENT_TYPES,
    briefing_day,
    is_content_fresh,
    person_calendars,
    person_todos,
    person_voice,
)
from .personas import (
    DEFAULT_VOICE_PERSONA,
    PERSONAS,
    Persona,
    get_persona,
    pick_persona,
)
from .slide_renderer import CANVAS_HEIGHT, CANVAS_WIDTH
from .tts_client import track_voice_fallback

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Iterator

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


# A key voiced by someone other than the person's voice: ``<key>--<persona id>``.
PERSONA_SEPARATOR = "--"


def voiced_key(key: str, persona_id: str) -> str:
    """Return the storage key for ``key`` voiced by ``persona_id``."""
    return f"{key}{PERSONA_SEPARATOR}{persona_id}"


def persona_of(key: str) -> str | None:
    """Return the persona a storage key is voiced by, or None for the person's."""
    _, sep, persona_id = key.partition(PERSONA_SEPARATOR)
    return persona_id if sep else None


def unvoiced_key(key: str) -> str:
    """Return a storage key without its persona."""
    return key.partition(PERSONA_SEPARATOR)[0]


def segment_type_of(key: str) -> str:
    """Return the segment type a storage key was built from."""
    return unvoiced_key(key).removesuffix(NOSIGNOFF_SUFFIX)


# Segment types that change through the day; ``cast_studio.refresh`` rebuilds them.
DYNAMIC_SEGMENT_TYPES = frozenset({"weather", "commute"})


def dynamic_segment_types(routine: Routine) -> list[str]:
    """Return the routine's dynamic segment types, in routine order."""
    return [t for t in routine.segments if t in DYNAMIC_SEGMENT_TYPES]


def routine_segment_keys(person: Person, routine: Routine, day: date) -> list[str]:
    """
    Return the routine's segment keys in order for the briefing day ``day``.

    With a ``closing`` segment, the encouragement is built without its own
    sign-off so the name is never said twice (spec §5.2). A segment the
    routine voices with someone other than the person's voice that day gets a
    voiced key, so it is built and stored apart; any other routine asking for
    the same segment and persona shares it.
    """
    include_signoff = "closing" not in routine.segments
    default = person_default_persona(person, day)
    keys: list[str] = []
    for segment_type in routine.segments:
        key = segment_key(segment_type, include_signoff=include_signoff)
        persona = segment_persona(person, routine, segment_type, day)
        keys.append(key if persona == default else voiced_key(key, persona.persona_id))
    return keys


_DEFAULT_PERSONA = next(p for p in PERSONAS if p.persona_id == DEFAULT_VOICE_PERSONA)


def persona_voice(persona: Persona) -> dict[str, Any]:
    """Return the step keys that make ``persona`` write and speak a segment."""
    return {
        "voice": persona.gemini_voice,
        "voice_personality": persona.character,
        "voice_profile": persona.delivery,
        "openai_voice": persona.openai_voice,
    }


def person_default_persona(person: Person, day: date) -> Persona:
    """Return who speaks the person's segments on ``day`` without a voice change."""
    picked = pick_persona(person_voice(person.settings), person.person_id, day)
    return picked or _DEFAULT_PERSONA


def segment_persona(
    person: Person, routine: Routine | None, segment_type: str, day: date
) -> Persona:
    """Return who voices a routine's segment on ``day``."""
    changes = dict(routine.voice_changes) if routine else {}
    picked = pick_persona(changes.get(segment_type), person.person_id, day)
    return picked or person_default_persona(person, day)


def _key_persona(person: Person, key: str, day: date) -> Persona:
    """Return who voices a stored key: its own persona, else the person's."""
    persona_id = persona_of(key)
    return (persona_id and get_persona(persona_id)) or person_default_persona(
        person, day
    )


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
    the person's personas speaks when they picked several. A voiced key names
    its persona instead; an unknown one falls back to the person's voice.
    ``visual`` builds a slide and clip as well as the audio.
    """
    segment_type = segment_type_of(segment_key)
    if segment_type not in SEGMENT_TYPES:
        msg = f"Unknown CastStudio segment '{segment_key}'"
        raise ValueError(msg)

    settings = person.settings
    if day is None:
        day = briefing_day(person, dt_util.now())
    scripture_entity = settings.get("scripture_entity") or ""

    step: dict[str, Any] = {
        "name": segment_type,
        "type": segment_type,
        "visual": visual,
        "standalone_segment": True,
        **persona_voice(_key_persona(person, segment_key, day)),
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
        image = settings.get("greeting_image")
        if isinstance(image, dict) and image.get("media_content_id"):
            # Resolved to a file at build time; see the generator.
            step["image_media_id"] = image["media_content_id"]
        elif isinstance(image, str) and image:
            # Typed as a path before the image picker existed.
            step["image_path"] = image
    elif segment_type == "weather":
        step["entity_id"] = settings.get("weather_entity") or ""
    elif segment_type == "agenda":
        calendars = person_calendars(settings)
        todos = person_todos(settings)
        step.update(
            calendar_entities=calendars,
            todo_entities=todos,
            entity_ids=[*calendars, *todos],
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
            include_signoff=not unvoiced_key(segment_key).endswith(NOSIGNOFF_SUFFIX),
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
    and the speech length for audio-only people. ``degraded`` marks speech
    voiced by the fallback voice after the configured AI voice failed. A play
    still uses it; the next prebuild replaces it.
    """

    key: str
    audio_path: str
    clip_path: str | None
    duration: float
    built_at: datetime
    built_day: date
    degraded: bool = False


def _segment_to_json(segment: StoredSegment) -> dict[str, Any]:
    """Serialise a segment; file names are relative to its manifest folder."""
    return {
        "audio": Path(segment.audio_path).name,
        "clip": Path(segment.clip_path).name if segment.clip_path else None,
        "duration": segment.duration,
        "built_at": segment.built_at.isoformat(),
        "built_day": segment.built_day.isoformat(),
        "degraded": segment.degraded,
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
        degraded=bool(data.get("degraded", False)),
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


def _json_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _json_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _json_strings(item)


def _remove_superseded_day_files(directory: Path, now: datetime) -> list[str]:
    """
    Delete files in a day folder that no manifest or routine file points to.

    Every rebuild writes new versioned files and leaves the old ones for any
    play still streaming them. Only once nothing was superseded within
    ``ALL_HEARD_KEEP_SUPERSEDED`` (the newest ``*.json`` is that old) can no
    play still be running from an old file; files written within that time are
    kept too.
    """
    cutoff = (now - ALL_HEARD_KEEP_SUPERSEDED).timestamp()
    records = list(directory.glob("*.json"))
    if any(record.stat().st_mtime > cutoff for record in records):
        return []
    keep: set[str] = set()
    for record in records:
        try:
            payload = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Unreadable: can't tell what it points to, so delete nothing.
            return []
        keep.update(Path(value).name for value in _json_strings(payload))
    removed: list[str] = []
    for child in directory.iterdir():
        if (
            child.is_file()
            and child.suffix != ".json"
            and child.name not in keep
            and child.stat().st_mtime <= cutoff
        ):
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
        day_dir = self.day_dir(person, today)
        if await async_run_in_executor(self._hass, day_dir.is_dir):
            removed += await async_run_in_executor(
                self._hass, _remove_superseded_day_files, day_dir, now
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
    with track_voice_fallback() as voice:
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
        degraded=voice.used,
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
    source: str = "prebuild",
    sensor_refresh_timeout: float | None = None,
    build_all_heard: bool = True,
) -> dict[str, StoredSegment | Exception]:
    """
    Build and store the person's segments for the briefing day of ``now``.

    Each key is built independently: a failure is logged and returned as the
    key's value, and the remaining keys are still built. The all-heard clip
    is also built when it is missing for the day, unless ``build_all_heard``
    is False. Keys in ``visual_keys``
    (and the all-heard line when ``all_heard_visual``) get a slide and clip.
    Every build logs how long it took, labelled with ``source`` (what asked
    for it). ``sensor_refresh_timeout`` caps the commute's sensor refresh.
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
    if build_all_heard and (
        all_heard is None or all_heard.built_day != today or all_heard.degraded
    ):
        jobs.append((ALL_HEARD_KEY, store.person_dir(person) / ALL_HEARD_DIR))

    scratch = Path(
        await async_run_in_executor(hass, tempfile.mkdtemp, prefix="cast_studio_seg_")
    )
    results: dict[str, StoredSegment | Exception] = {}
    try:
        for key, target_dir in jobs:
            started = time.monotonic()
            try:
                step = (
                    _all_heard_step(person, today, visual=all_heard_visual)
                    if key == ALL_HEARD_KEY
                    else build_segment_step(
                        person, key, today, visual=key in visual_keys
                    )
                )
                if (
                    sensor_refresh_timeout is not None
                    and segment_type_of(key) == "commute"
                ):
                    step["sensor_refresh_timeout"] = sensor_refresh_timeout
                segment, staged = await _async_build_one(
                    hass, person, key, step, creds, scratch, target_dir, now
                )
                segment = await store.async_store(person, target_dir, segment, staged)
            except Exception as err:  # noqa: BLE001 - one segment must not stop the rest
                LOGGER.warning(
                    "CastStudio could not build segment '%s' for %s after %.1fs "
                    "(%s): %s",
                    key,
                    person.name,
                    time.monotonic() - started,
                    source,
                    err,
                    exc_info=err,
                )
                if key != ALL_HEARD_KEY:
                    results[key] = err
            else:
                LOGGER.info(
                    "Built segment '%s' for %s in %.1fs (%s)",
                    key,
                    person.name,
                    time.monotonic() - started,
                    source,
                )
                if key != ALL_HEARD_KEY:
                    results[key] = segment
    finally:
        await async_run_in_executor(hass, shutil.rmtree, scratch, ignore_errors=True)
    return results
