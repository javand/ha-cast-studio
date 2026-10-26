"""
Briefing engine: pre-build and play routine subentries (spec §5.3 to §5.5).

``async_prebuild`` builds and stores a routine's (or a person's) segments, and
for visual routines also a fully stitched routine video and audio.
``async_play`` plays fresh stored segments, building whatever is missing or
stale live first, and can skip segment types the routine already played during
the briefing day. It plays video only for a visual routine on a screen.

Per-routine files sit next to the segments of the day they were built for::

    people/<id>/<YYYY-MM-DD>/routine_<slug>.<version>.mp4   pre-stitched video
    people/<id>/<YYYY-MM-DD>/routine_<slug>.json   its file, what it was built from
    people/<id>/<YYYY-MM-DD>/routine_<slug>-audio.<version>.mp3   the same as audio
    people/<id>/<YYYY-MM-DD>/routine_<slug>-audio.json   its sidecar
    people/<id>/<YYYY-MM-DD>/play_<slug>.<version>.mp3|.mp4 stitched at play time

Stitched files get a new versioned name each time, like segments, so a
pre-build during playback never overwrites the file being streamed. Older
versions go with the day's folder at the daily prune.

A newer play for a person supersedes the one before it: the earlier play's
lifecycle is cancelled and runs neither its post-briefing dashboard nor a
sensor reset over the newer play.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, EventStateChangedData, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .ai_client import async_discover_ai_credentials
from .const import CONF_OUTPUT_DIR, DEFAULT_OUTPUT_DIR, DOMAIN, LOGGER
from .ffmpeg_runner import async_run_ffmpeg, build_concat_command, write_concat_file
from .helpers import async_run_in_executor, create_task
from .orchestrator import (
    SUPERSEDED_FLAG,
    PlaybackNotStartedError,
    _is_cast_player,
    async_play_and_sync_routine,
    has_screen,
)
from .people import (
    briefing_day,
    get_people,
    get_routines,
    is_content_fresh,
    slugify_name,
)
from .play_history import PlayHistory
from .segments import (
    SegmentStore,
    StoredSegment,
    async_build_segments,
    routine_segment_keys,
    segment_type_of,
    versioned_path,
)
from .sensor import (
    BRIEFING_SENSORS,
    CastStudioBriefingSensor,
    async_get_or_create_briefing_sensor,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .people import Person, Routine

DATA_STORE = "segment_store"
DATA_HISTORY = "play_history"
DATA_LOCKS = "briefing_locks"
DATA_PLAYS = "briefing_plays"

ROUTINE_VIDEO_PREFIX = "routine_"
ROUTINE_AUDIO_SUFFIX = "-audio"
PLAY_PREFIX = "play_"

UNAVAILABLE_STATES = ("unavailable", "unknown")


# --- runtime objects ----------------------------------------------------------


def get_lock(hass: HomeAssistant, person_id: str) -> asyncio.Lock:
    """
    Return the engine lock for one person.

    A play waits on it while a pre-build for the same person is running. It
    is only ever taken at the top of ``async_prebuild`` and ``async_play``,
    never nested; the segment store's own manifest lock is taken inside it.
    """
    locks = hass.data.setdefault(DOMAIN, {}).setdefault(DATA_LOCKS, {})
    return locks.setdefault(person_id, asyncio.Lock())


@dataclass
class _CurrentPlay:
    """The latest play of one person, until a newer play supersedes it."""

    routine_id: str
    metadata: dict[str, Any]
    task: asyncio.Task[Any] | None = None
    superseded_by: str | None = field(default=None)
    # Segment types this play newly marked heard, undone if it never plays.
    newly_heard: set[str] = field(default_factory=set)
    marked_at: datetime | None = None


# How long a Cast display may keep reporting "playing" after the briefing's
# playback task ends (a stall makes it run late) before it is left alone.
RELEASE_WAIT_SECONDS = 60.0
# Segment types rebuilt at play time so they reflect that moment (traffic).
LIVE_SEGMENT_TYPES = frozenset({"commute"})
_STILL_PLAYING = ("playing", "buffering")


def _current_plays(hass: HomeAssistant) -> dict[str, _CurrentPlay]:
    return hass.data.setdefault(DOMAIN, {}).setdefault(DATA_PLAYS, {})


def _supersede_play(hass: HomeAssistant, person: Person, routine: Routine) -> None:
    """Stop the person's earlier play from acting after its playback ends."""
    previous = _current_plays(hass).pop(person.person_id, None)
    if previous is None:
        return
    previous.superseded_by = routine.routine_id
    previous.metadata[SUPERSEDED_FLAG] = True
    if previous.task is not None and not previous.task.done():
        previous.task.cancel()


def _output_dir(entry: ConfigEntry) -> str:
    return entry.options.get(
        CONF_OUTPUT_DIR, entry.data.get(CONF_OUTPUT_DIR, DEFAULT_OUTPUT_DIR)
    )


def _store(hass: HomeAssistant, entry: ConfigEntry) -> SegmentStore:
    domain_data = hass.data.setdefault(DOMAIN, {})
    store = domain_data.get(DATA_STORE)
    if store is None:
        store = domain_data[DATA_STORE] = SegmentStore(hass, _output_dir(entry))
    return store


async def async_get_play_history(hass: HomeAssistant) -> PlayHistory:
    """Return the shared play history, loading it on first use."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    history = domain_data.get(DATA_HISTORY)
    if history is None:
        history = PlayHistory(hass)
        await history.async_load()
        domain_data[DATA_HISTORY] = history
    return history


async def async_setup_runtime(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """
    Create the entry's segment store and load play history and manifests.

    The play history object is kept across reloads, so a mark saved with a
    delay just before a reload is never lost.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data[DATA_STORE] = store = SegmentStore(hass, _output_dir(entry))
    await async_get_play_history(hass)
    now = dt_util.now()
    for person in get_people(entry):
        await store.async_load(person, now)


def async_unload_runtime(hass: HomeAssistant) -> None:
    """Drop the entry's store and sensors so a reload starts clean."""
    domain_data = hass.data.get(DOMAIN, {})
    for key in (DATA_STORE, BRIEFING_SENSORS):
        domain_data.pop(key, None)


async def async_refresh_sensors(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Show each routine's readiness for the current briefing day."""
    store = _store(hass, entry)
    now = dt_util.now()
    people = {person.person_id: person for person in get_people(entry)}
    for routine in get_routines(entry):
        person = people[routine.person_id]
        sensor = async_get_or_create_briefing_sensor(hass, person, routine)
        ready = _ready_keys(store, person, routine, now)
        _set_sensor(sensor, "ready" if ready else "idle", store, person, routine, now)


def _usable_segment(
    store: SegmentStore, person: Person, key: str, now: datetime, *, visual: bool
) -> StoredSegment | None:
    """
    Return a fresh stored segment, with a clip when ``visual``.

    A segment built audio-only has no clip; for a visual use it counts as
    missing, so it is rebuilt with a clip.
    """
    segment = store.get_fresh(person, key, now)
    if segment is None:
        return None
    if visual and not segment.clip_path:
        return None
    return segment


def _is_visual(routine: Routine) -> bool:
    return routine.output_format == "visual"


def _person_has_visual_routine(entry: ConfigEntry, person: Person) -> bool:
    return any(_is_visual(r) for r in get_routines(entry, person.person_id))


def _ready_keys(
    store: SegmentStore, person: Person, routine: Routine, now: datetime
) -> list[str]:
    return [
        key
        for key in routine_segment_keys(routine)
        if _usable_segment(store, person, key, now, visual=_is_visual(routine))
        is not None
    ]


def _set_sensor(  # noqa: PLR0913
    sensor: CastStudioBriefingSensor,
    state: str,
    store: SegmentStore,
    person: Person,
    routine: Routine,
    now: datetime,
) -> None:
    sensor.set_briefing_state(
        state,
        briefing_day=briefing_day(person, now).isoformat(),
        ready_segments=_ready_keys(store, person, routine, now),
    )


def _person_of(entry: ConfigEntry, routine: Routine) -> Person:
    for person in get_people(entry):
        if person.person_id == routine.person_id:
            return person
    msg = f"CastStudio routine '{routine.name}' has no person: it was removed"
    raise HomeAssistantError(msg)


# --- stitching ----------------------------------------------------------------


@dataclass
class _Media:
    """What a play hands to the orchestrator."""

    path: str
    is_video: bool
    duration: float


def _segment_media(segment: StoredSegment, *, visual: bool) -> str:
    if visual and segment.clip_path:
        return segment.clip_path
    return segment.audio_path


async def _async_concat(hass: HomeAssistant, inputs: list[str], output: Path) -> None:
    """Join same-encoded files by stream copy into ``output``."""
    list_file = output.with_name(f"{output.name}.txt")
    partial = output.with_name(f"{output.stem}.part{output.suffix}")
    await async_run_in_executor(hass, write_concat_file, inputs, str(list_file))
    if output.suffix == ".mp4":
        command = build_concat_command(str(list_file), str(partial))
    else:
        command = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(partial),
        ]
    try:
        if not await async_run_ffmpeg(hass, command):
            msg = f"FFmpeg could not stitch {output.name}"
            raise HomeAssistantError(msg)
    finally:
        await async_run_in_executor(hass, list_file.unlink, missing_ok=True)
    await async_run_in_executor(hass, partial.replace, output)


def _routine_media_base(
    store: SegmentStore, person: Person, routine: Routine, day: date, *, video: bool
) -> Path:
    # "-audio" can't come out of slugify, so it never names another routine.
    suffix = "" if video else ROUTINE_AUDIO_SUFFIX
    return store.day_dir(person, day) / (
        f"{ROUTINE_VIDEO_PREFIX}{slugify_name(routine.name)}{suffix}"
    )


def _routine_media_file(base: Path, meta: dict[str, Any], *, video: bool) -> Path:
    """Return the file a routine sidecar points to, inside the day folder."""
    name = meta.get("video" if video else "audio")
    extension = ".mp4" if video else ".mp3"
    if (
        not isinstance(name, str)
        or Path(name).name != name
        or not name.startswith(base.name)
        or not name.endswith(extension)
    ):
        # Sidecars written before versioned names: the fixed file name.
        return base.with_suffix(extension)
    return base.with_name(name)


def _segments_signature(segments: Iterable[StoredSegment]) -> dict[str, str]:
    return {segment.key: segment.built_at.isoformat() for segment in segments}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


async def _async_store_routine_media(  # noqa: PLR0913
    hass: HomeAssistant,
    store: SegmentStore,
    person: Person,
    routine: Routine,
    now: datetime,
    *,
    video: bool,
) -> None:
    """Stitch a visual routine's fresh clips or audio into its pre-built file."""
    segments = [
        segment
        for key in routine_segment_keys(routine)
        if (segment := _usable_segment(store, person, key, now, visual=True))
        is not None
    ]
    if not segments:
        return
    base = _routine_media_base(
        store, person, routine, segments[0].built_day, video=video
    )
    output = versioned_path(base.with_suffix(".mp4" if video else ".mp3"))
    inputs = [_segment_media(s, visual=video) for s in segments]
    await _async_concat(hass, inputs, output)
    payload = {
        "video" if video else "audio": output.name,
        "built_at": now.isoformat(),
        "built_day": briefing_day(person, now).isoformat(),
        "duration": sum(segment.duration for segment in segments),
        "segments": _segments_signature(segments),
    }
    await async_run_in_executor(hass, _write_json, base.with_suffix(".json"), payload)


async def _async_store_visual_routines(
    hass: HomeAssistant,
    store: SegmentStore,
    person: Person,
    routines: list[Routine],
    now: datetime,
) -> None:
    """Store each visual routine's video, and its audio for speakers."""
    for routine in routines:
        if _is_visual(routine):
            for video in (True, False):
                await _async_store_routine_media(
                    hass, store, person, routine, now, video=video
                )


async def _async_fresh_routine_media(  # noqa: PLR0913
    hass: HomeAssistant,
    store: SegmentStore,
    person: Person,
    routine: Routine,
    segments: list[StoredSegment],
    now: datetime,
    *,
    video: bool,
) -> _Media | None:
    """Return the pre-built file if it is fresh and made of exactly ``segments``."""
    base = _routine_media_base(
        store, person, routine, briefing_day(person, now), video=video
    )
    meta = await async_run_in_executor(hass, _read_json, base.with_suffix(".json"))
    if not isinstance(meta, dict):
        return None
    media = _routine_media_file(base, meta, video=video)
    if not await async_run_in_executor(hass, media.is_file):
        return None
    try:
        built_at = datetime.fromisoformat(meta["built_at"])
        built_day = date.fromisoformat(meta["built_day"])
        duration = float(meta["duration"])
    except (KeyError, TypeError, ValueError):
        return None
    if not is_content_fresh(person, built_at, built_day, now):
        return None
    if meta.get("segments") != _segments_signature(segments):
        return None
    return _Media(str(media), is_video=video, duration=duration)


async def _async_stitch_for_play(  # noqa: PLR0913
    hass: HomeAssistant,
    store: SegmentStore,
    person: Person,
    routine: Routine,
    segments: list[StoredSegment],
    now: datetime,
    *,
    visual: bool,
) -> _Media:
    inputs = [_segment_media(segment, visual=visual) for segment in segments]
    duration = sum(segment.duration for segment in segments)
    if len(inputs) == 1:
        return _Media(inputs[0], is_video=inputs[0].endswith(".mp4"), duration=duration)
    suffix = ".mp4" if visual else ".mp3"
    output = versioned_path(
        store.day_dir(person, briefing_day(person, now))
        / f"{PLAY_PREFIX}{slugify_name(routine.name)}{suffix}"
    )
    await _async_concat(hass, inputs, output)
    return _Media(str(output), is_video=visual, duration=duration)


# --- prebuild -----------------------------------------------------------------


async def async_prebuild(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    routine: Routine | None = None,
    person: Person | None = None,
) -> None:
    """
    Build and store segments for one routine, or for all of a person's routines.

    Plays nothing. For visual routines the stitched video and audio are stored
    too, so a plain play needs no stitching (spec §5.3).
    """
    if (routine is None) == (person is None):
        msg = "async_prebuild needs exactly one of routine or person"
        raise ValueError(msg)
    if routine is not None:
        person = _person_of(entry, routine)
        routines = [routine]
    else:
        assert person is not None  # noqa: S101 - narrowed by the check above
        routines = get_routines(entry, person.person_id)
    keys = list(dict.fromkeys(key for r in routines for key in routine_segment_keys(r)))
    sensors = {
        r.routine_id: async_get_or_create_briefing_sensor(hass, person, r)
        for r in routines
    }
    store = _store(hass, entry)

    def _not_playing() -> list[CastStudioBriefingSensor]:
        # A routine that is playing keeps showing "playing" (its playback
        # monitor sets it back to ready when the media ends).
        return [s for s in sensors.values() if s.native_value != "playing"]

    try:
        async with get_lock(hass, person.person_id):
            for sensor in _not_playing():
                sensor.set_briefing_state("generating")
            now = dt_util.now()
            await store.async_prune(person, now)
            creds = await async_discover_ai_credentials(hass)
            # Any of the person's visual routines, not just these: a shared
            # segment must keep its clip when an audio routine is rebuilt.
            visual_keys = {
                key
                for r in get_routines(entry, person.person_id)
                if _is_visual(r)
                for key in routine_segment_keys(r)
            } & set(keys)
            results = await async_build_segments(
                hass,
                store,
                person,
                keys,
                creds,
                now=now,
                visual_keys=visual_keys,
                all_heard_visual=_person_has_visual_routine(entry, person),
            )
            await _async_store_visual_routines(hass, store, person, routines, now)
    except Exception:
        for sensor in _not_playing():
            sensor.set_briefing_state("error")
        raise

    for r in routines:
        sensor = sensors[r.routine_id]
        if sensor.native_value == "playing":
            state = "playing"
        else:
            state = "ready" if _ready_keys(store, person, r, now) else "error"
        _set_sensor(sensor, state, store, person, r, now)
    if keys and all(isinstance(value, Exception) for value in results.values()):
        msg = f"CastStudio could not build any segment for {person.name}"
        raise HomeAssistantError(msg)


# --- play ---------------------------------------------------------------------


def _resolve_target(hass: HomeAssistant, person: Person, target: str | None) -> str:
    player = target or person.media_player
    if not player:
        msg = f"No media player set for {person.name} and no target given"
        raise HomeAssistantError(msg)
    state = hass.states.get(player)
    if state is None or state.state in UNAVAILABLE_STATES:
        msg = f"Media player {player} is unavailable"
        raise HomeAssistantError(msg)
    return player


def _dashboard_actions(
    hass: HomeAssistant, person: Person, routine: Routine
) -> list[dict[str, Any]]:
    """Return the post-briefing dashboard action, if one applies (spec §5.3a)."""
    if not routine.dashboard_path:
        return []
    display = routine.dashboard_display or person.media_player
    if not display or not _is_cast_player(hass, display):
        LOGGER.warning(
            "Skipping post-briefing dashboard for %s's '%s': %s is not a Cast display",
            person.name,
            routine.name,
            display or "no display",
        )
        return []
    data: dict[str, Any] = {
        "entity_id": display,
        "dashboard_path": routine.dashboard_path,
    }
    if routine.dashboard_view:
        data["view_path"] = routine.dashboard_view
    return [{"action": "cast.show_lovelace_view", "data": data}]


async def _async_segments_to_play(  # noqa: PLR0913
    hass: HomeAssistant,
    store: SegmentStore,
    person: Person,
    routine: Routine,
    keys: list[str],
    now: datetime,
    *,
    visual: bool,
) -> list[StoredSegment]:
    """
    Return fresh segments for ``keys``, building missing ones live.

    ``LIVE_SEGMENT_TYPES`` (the commute) are always rebuilt, falling back to
    the stored copy if that fails. Keys that still have no fresh segment are
    skipped with a warning; raises when none is left to play.
    """
    # Live segments are rebuilt on every play; a stored copy is the fallback.
    missing = [
        key
        for key in keys
        if segment_type_of(key) in LIVE_SEGMENT_TYPES
        or _usable_segment(store, person, key, now, visual=visual) is None
    ]
    if missing:
        creds = await async_discover_ai_credentials(hass)
        await async_build_segments(
            hass,
            store,
            person,
            missing,
            creds,
            now=now,
            visual_keys=missing if visual else (),
            all_heard_visual=visual,
        )
    segments: list[StoredSegment] = []
    for key in keys:
        segment = _usable_segment(store, person, key, now, visual=visual)
        if segment is None:
            LOGGER.warning(
                "Skipping segment '%s' of %s's '%s': it could not be built",
                key,
                person.name,
                routine.name,
            )
            continue
        segments.append(segment)
    if not segments:
        msg = f"CastStudio could not build any segment of '{routine.name}'"
        raise HomeAssistantError(msg)
    return segments


async def _async_all_heard(
    hass: HomeAssistant,
    store: SegmentStore,
    person: Person,
    now: datetime,
    *,
    visual: bool,
) -> _Media:
    clip = store.all_heard_clip(person)
    if clip is None or clip.built_day != briefing_day(person, now):
        creds = await async_discover_ai_credentials(hass)
        await async_build_segments(
            hass,
            store,
            person,
            [],
            creds,
            now=now,
            all_heard_visual=visual,
        )
        clip = store.all_heard_clip(person)
    if clip is None:
        msg = f"CastStudio could not build the all-heard line for {person.name}"
        raise HomeAssistantError(msg)
    path = _segment_media(clip, visual=visual)
    return _Media(path, is_video=path.endswith(".mp4"), duration=clip.duration)


async def _async_dispatch_play(  # noqa: PLR0913
    hass: HomeAssistant,
    person: Person,
    routine: Routine,
    sensor: CastStudioBriefingSensor,
    player: str,
    metadata: dict[str, Any],
) -> _CurrentPlay:
    """Supersede the person's earlier play, then start this one."""
    _supersede_play(hass, person, routine)
    current = _CurrentPlay(routine.routine_id, metadata)
    plays = _current_plays(hass)
    plays[person.person_id] = current
    try:
        current.task = await async_play_and_sync_routine(
            hass, sensor.routine_name, player, metadata, []
        )
    except Exception:
        if plays.get(person.person_id) is current:
            del plays[person.person_id]
        raise
    return current


async def _async_release_player(hass: HomeAssistant, player: str) -> None:
    """Turn ``player`` off once it stops playing; leave it if it keeps playing."""
    state = hass.states.get(player)
    if state is not None and state.state in _STILL_PLAYING:
        stopped = asyncio.Event()

        @callback
        def _changed(event: Event[EventStateChangedData]) -> None:
            new = event.data["new_state"]
            if new is None or new.state not in _STILL_PLAYING:
                stopped.set()

        unsub = async_track_state_change_event(hass, [player], _changed)
        try:
            async with asyncio.timeout(RELEASE_WAIT_SECONDS):
                await stopped.wait()
        except TimeoutError:
            return  # still playing: something else is using the player
        finally:
            unsub()
    await hass.services.async_call(
        "media_player", "turn_off", {"entity_id": player}, blocking=True
    )


async def _async_back_to_ready(  # noqa: PLR0913
    hass: HomeAssistant,
    current: _CurrentPlay,
    sensor: CastStudioBriefingSensor,
    store: SegmentStore,
    person: Person,
    routine: Routine,
    release_player: str | None = None,
) -> None:
    """
    Show the routine ready again when its playback ends or is superseded.

    With ``release_player`` set, a Cast player that finished the briefing is
    turned off so it leaves the cast screen. A player that never started
    playing shows the routine as error instead.
    """
    play_task = current.task
    state = "ready"
    try:
        if play_task is not None:
            await play_task
        if release_player and current.superseded_by is None:
            await _async_release_player(hass, release_player)
    except asyncio.CancelledError:
        if play_task is None or not play_task.cancelled():
            raise  # this task itself was cancelled
    except PlaybackNotStartedError as err:
        LOGGER.warning("%s's '%s' did not play: %s", person.name, routine.name, err)
        state = "error"
        if current.newly_heard and current.marked_at is not None:
            history = await async_get_play_history(hass)
            await history.async_unmark_played(
                person, routine, current.newly_heard, current.marked_at
            )
    except Exception as err:  # noqa: BLE001
        LOGGER.debug("Briefing playback ended with an error: %s", err)
    finally:
        plays = _current_plays(hass)
        if plays.get(person.person_id) is current:
            del plays[person.person_id]
        # A newer play of the same routine owns the sensor now.
        if current.superseded_by != routine.routine_id:
            _set_sensor(sensor, state, store, person, routine, dt_util.now())


async def async_play(
    hass: HomeAssistant,
    entry: ConfigEntry,
    routine: Routine,
    *,
    target: str | None = None,
    skip_played: bool = False,
) -> None:
    """
    Play a routine now: stored fresh segments, or built live when missing.

    With ``skip_played`` only segment types this routine has not yet played in
    the person's briefing day play; when nothing is left the all-heard line
    plays.
    """
    person = _person_of(entry, routine)
    sensor = async_get_or_create_briefing_sensor(hass, person, routine)
    store = _store(hass, entry)
    history = await async_get_play_history(hass)
    try:
        async with get_lock(hass, person.person_id):
            player = _resolve_target(hass, person, target)
            now = dt_util.now()
            # A play-only setup must not keep a folder for every day (spec §8).
            await store.async_prune(person, now)
            await store.async_load(person, now)
            # Video only for a visual routine on a device with a screen.
            video = _is_visual(routine) and has_screen(hass, player)
            all_keys = routine_segment_keys(routine)
            keys = all_keys
            if skip_played:
                heard = history.played(person, routine, now)
                keys = [key for key in all_keys if segment_type_of(key) not in heard]

            segments: list[StoredSegment] = []
            if keys:
                sensor.set_briefing_state("generating")
                segments = await _async_segments_to_play(
                    hass, store, person, routine, keys, now, visual=video
                )
                media = None
                if _is_visual(routine) and keys == all_keys:
                    media = await _async_fresh_routine_media(
                        hass, store, person, routine, segments, now, video=video
                    )
                if media is None:
                    media = await _async_stitch_for_play(
                        hass, store, person, routine, segments, now, visual=video
                    )
            else:
                media = await _async_all_heard(hass, store, person, now, visual=video)

            metadata: dict[str, Any] = {
                ("video_path" if media.is_video else "audio_path"): media.path,
                "duration": media.duration,
            }
            release_player: str | None = None
            if actions := _dashboard_actions(hass, person, routine):
                metadata["post_routine_actions"] = actions
            elif _is_cast_player(hass, player):
                # No dashboard to show: leave the cast screen when done.
                release_player = player

            _set_sensor(sensor, "playing", store, person, routine, now)
            current = await _async_dispatch_play(
                hass, person, routine, sensor, player, metadata
            )
            # Marked when playback starts, not when it ends (spec §7). The
            # media is already playing, so a failure here must not show error.
            types = {segment_type_of(s.key) for s in segments}
            current.newly_heard = types - history.played(person, routine, now)
            current.marked_at = now
            try:
                await history.async_mark_played(person, routine, types, now)
            except Exception:  # noqa: BLE001 - playback already started
                LOGGER.exception(
                    "Could not record played segments for %s's '%s'",
                    person.name,
                    routine.name,
                )
    except Exception:
        sensor.set_briefing_state("error")
        raise

    create_task(
        hass,
        _async_back_to_ready(
            hass, current, sensor, store, person, routine, release_player
        ),
        name=f"briefing_{sensor.routine_name}",
    )
