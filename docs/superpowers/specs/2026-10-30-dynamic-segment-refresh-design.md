# Dynamic segment refresh — design

Status: approved in chat 2026-10-30.

## Intent

Routines are prebuilt ahead of time (the user's automation runs
`cast_studio.prebuild` at 06:00) so playback starts quickly. Some segments
describe things that change through the morning: **weather** and **traffic**
(the `commute` segment). A weather segment built at 06:00 and played at 08:30
is stale. It has the 06:00 temperature, it misses rain that moved in since,
and before sunrise it repeats the weather entity's `clear-night` condition, so
the morning briefing talks about "night".

Today `commute` is rebuilt on every play (`LIVE_SEGMENT_TYPES` in
`briefing.py`), so traffic is current, but the user waits 10–15 seconds for the
cast to start. Weather is never refreshed after the prebuild.

The user wants a general feature that **refreshes the dynamic segments close
to when the routine will play**. They trigger it from their own Home Assistant
automation at a moment that predicts the play. For them that is getting out of
bed during their Morning Rush window: the bathroom trigger usually fires 20–35
seconds later (8 seconds at the fastest in the last 10 days). The refresh then
finishes during that walk, and play uses fresh copies without rebuilding
anything.

## Goals

- A defined set of **dynamic segment types**: `weather` and `commute` for now.
  It is one place in code, so more types can be added later.
- A new action that rebuilds **only the dynamic segments** for a person (or a
  routine), in every routine that includes them, and stores them the way
  prebuild does. Weather and traffic are always handled together: both are
  refreshed by this action, or both come from the last prebuild. If only one
  of them rebuilds, the fresh one is kept and the other keeps its prebuild
  copy.
- Visual routines whose dynamic segments were refreshed get their pre-stitched
  routine video and audio rebuilt, so a play does not have to stitch.
- `cast_studio.play` **stops rebuilding `commute` at play time.** It plays
  whatever copy is stored: refreshed if a refresh ran, otherwise the prebuild
  copy.
- If a refresh for the person is **still running** when play is called, play
  waits for it for up to **10 seconds** (a single named constant). If the
  refresh finishes in time, play uses the fresh copies. If not, play goes ahead
  with what is stored (the prebuild copies) and does not wait further.
- **Build timing in the logs**: every segment build logs how long it took, with
  the person, the segment key, and the source (prebuild, refresh or play). The
  whole refresh logs its total time, and a play logs how long it waited for a
  refresh. INFO level, so real numbers show up without debug logging.
- Works for any person and any routine that includes weather or commute. It is
  not tied to the morning routine or to bed sensors.

## Non-goals

- Deciding **when** to refresh. That stays in the user's automations (for this
  user: `binary_sensor.javan_in_bed` turning off while `sensor.morning_rush`
  is `True`). CastStudio knows nothing about bed sensors or time windows.
- Changing how weather is worded. In particular, **no fix for the "night"
  wording** (`clear-night` reaching the AI) and no forecast-temperature
  substitution. A refreshed build fixes this as a side effect when the weather
  entity is no longer in a night state.
- Any heads-up, warning line or second voice when the stored copy is stale.
- Comparing old and new data to decide whether a rebuild is "significant". A
  refresh always rebuilds.
- Rebuilding anything at play time. The play-time live rebuild of `commute` is
  removed, not extended to weather.
- Setting up the user's Home Assistant automation for Jamillah (her automation
  can be added later; the action is per person).
- Splitting playback so the greeting starts before later segments are ready.
  Play stitches one file and casts it, and that stays.

## Anti-goals

- Play must never wait more than about 10 seconds because of a refresh. The
  user's limit is about 10–20 seconds from trigger to cast.
- A failed or slow refresh must never stop a routine from playing. The stored
  prebuild copy is always the fallback.
- A refresh must never break a play that is already streaming. Versioned file
  names already protect this; keep it that way.
- A refresh must never rebuild non-dynamic segments (greeting, agenda, fun
  fact, and so on). The point is to stay fast.

## Current code to build on

- `briefing.py:132` `LIVE_SEGMENT_TYPES = {"commute"}`, used by
  `_async_segments_to_play` (`briefing.py:608`) to rebuild commute on every
  play. That behavior is what's being removed.
- `async_prebuild` (`briefing.py:492`) shows the full flow: per-person lock,
  sensors set to `generating`, `async_build_segments`, then
  `_async_store_visual_routines` for the pre-stitched files.
- `get_lock` (`briefing.py:98`): a per-person `asyncio.Lock` taken at the top of
  prebuild and play. Play already waits on it while a prebuild runs, with no
  time limit.
- `async_build_segments` (`segments.py:653`) builds keys one after another,
  with per-key failure isolation, and also builds the all-heard clip when it is
  missing.
- `_async_fresh_routine_media` (`briefing.py:433`) reuses the pre-stitched file
  only if its segment signature matches. A refresh changes the signature, so
  those files must be re-stored.
- Actions are registered in `__init__.py` (`_register_briefing_actions`) with
  voluptuous schemas, and described in `services.yaml` and `strings.json`.
- Commute sensors are already asked to refresh (`homeassistant.update_entity`,
  15 s cap) before they are read. The user's own automation also refreshes the
  route sensors every 10 minutes during Morning Rush.
- `SPEC.md` §§ on segments and pre-build/play describe the commute-at-play
  behavior and must be updated to match.

## Decided

- Dynamic types: `weather`, `commute`.
- Trigger: a new CastStudio action called from the user's automation. Its
  targeting is the same as `prebuild`: `person` or `routine` (with optional
  `person`).
- **Routine scope is required, not optional to support.** The user will add
  other routines with dynamic segments (for example a night routine), each
  refreshed by its own automation at its own moment. With `routine`, the
  refresh rebuilds only the dynamic segments that routine contains, and
  re-stores only that routine's pre-stitched files. With `person` alone, it
  covers every routine of that person that has a dynamic segment.
- Stored segments are per person and per briefing day, not per routine (one
  `weather` key per person per day, `segments.py:150`). So a routine-scoped
  refresh also gives the person's other routines that fresh copy. That is
  intended: fresher is better. Those other routines' pre-stitched files no
  longer match their segments, so their next play stitches at play time, as
  it already does when the files don't match.
- **Freshness across routines stays the user's job.** Nothing rebuilds on its
  own schedule: the newest stored copy for the briefing day wins, and it is
  only replaced when an automation calls `prebuild` or `refresh`. A night
  routine that wants evening weather needs its own evening `refresh` (or a
  routine-scoped `prebuild`). The existing per-person `max_age_minutes`
  setting (default off) remains the safety net: a segment older than that
  counts as missing and is built live at play time. This design does not
  change that setting or its behavior.
- Weather and traffic share one path: one action refreshes both, and with no
  refresh both come from the prebuild copy.
- **Partial refresh:** each dynamic segment stands on its own. If weather
  rebuilds but commute fails (or the reverse), the fresh one is kept and the
  failed one keeps its prebuild copy. A person with no commute configured still
  gets weather refreshed. The existing per-key failure isolation and commit in
  `async_build_segments` already behave this way.
- Commute is no longer rebuilt at play time. On a morning with no refresh, the
  traffic heard is from the 06:00 prebuild. The user accepted this.
- Play waits up to 10 seconds for a running refresh, then plays the stored
  copies.
- No heads-up, no wording fix, no significance check.
- Timing logs for every segment build.

## Calls made by the designer (check these)

- **Action name:** `cast_studio.refresh`, shown as "Refresh dynamic
  segments". The feature is "dynamic segment refresh".
- **Play gave up waiting and a dynamic segment has no stored copy** (no
  prebuild yet, or the prebuild failed for it): play does **not** build it
  itself. It skips that segment with the existing warning and plays the rest.
  This keeps the 10–20 second limit and avoids play and refresh building the
  same segment at the same time. Non-dynamic missing segments are still built
  live as today.
- **Commute sensor update during a refresh:** the refresh still asks the
  route sensors to update before reading them, but with a shorter cap of
  **5 seconds** (play no longer builds commute, so the 15-second cap only
  applies to prebuild). If the update doesn't finish, the refresh uses the
  sensors' last value, as today. The user's own automation already updates
  them every 10 minutes during Morning Rush, so the last value is at most
  about 10 minutes old.

## Left to the builder

- The action's wording in `services.yaml` / `strings.json`.
- How the 10-second wait is implemented: for example a timed lock acquire, or
  a refresh-in-progress event per person that play waits on. Whatever is
  chosen, a play that gives up waiting must still produce correct output from
  the stored segments, and the refresh must finish and store its results for
  the next play. Keep in mind that the lock serializes manifest writes today.
- Whether a refresh holds the person lock for its whole duration, or only while
  it commits. This interacts with the point above.
- Whether weather and commute build at the same time inside a refresh (the
  build loop is sequential today). Parallel building would shorten the refresh,
  but it is optional.
- What a refresh does when the person has no routine with a dynamic segment:
  most likely log and return without error.
- What a refresh does when there is no prebuild yet for the briefing day: build
  only the dynamic segments, so play then builds the rest live as it does
  today.
- What the briefing sensor shows during a refresh (for example `generating`, as
  prebuild does, without overriding `playing`).
- Whether `LIVE_SEGMENT_TYPES` is renamed or replaced by the new dynamic-types
  constant.
- Tests, following the existing test layout.
