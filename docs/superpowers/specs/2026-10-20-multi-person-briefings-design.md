# Multi-Person Briefings: People, Playlists & Pre-Built Segments — Design

> **Status (2026-10-24):** Implemented. This is the current architecture spec; code comments cite its sections (`spec §5.x`). Everything here about the **old pipeline** no longer applies: the blueprints, `run_routine`, `pregenerate_routine`, `compile_clip`, inline `routine_config`, YAML routines and the per-routine-name sensors were removed before the first release (commit `01f7cf1`), so "two engines side by side" (§5.3) is gone and only `prebuild` and `play` exist. Later additions are described in `SPEC.md` and the README: several calendars per person, evening trip-home sensors, the closing slide, live commute rebuild on play, and AI account-problem reporting.

**Date:** 2026-10-20
**Status:** Draft for review
**Component:** `custom_components/cast_studio`

## 1. Intent

CastStudio today serves one person. A household needs several people, each with their own briefing. The motivating case: the owner has a fully automated, display-based morning. Their spouse wants a short, audio-only briefing on a speaker, started with a button, with control over what plays. Trying to add the second person through **Add Service** fails with "CastStudio is already configured". The config flow allows only one entry (`async_set_unique_id(DOMAIN)`), and the recipient is a single name in that entry.

CastStudio will become a public (HACS) project. Everything here must be general, with nothing specific to this household.

**The core idea:** each person has a pool of **segments**: greeting, weather, drive time, daily text, fun fact, closing and so on. Segments are built ahead of time and stored individually. A **routine** is a named playlist over one person's segments. Playing a routine stitches that person's stored segments together, so playback is instant. It can also skip segments the person already heard today.

## 2. Goals

1. More than one person per CastStudio install, each with their own name, voice settings, output device, output format (audio-only or visual), commute destination and travel mode.
2. Multiple named routines (playlists) per person, for example "Office day" / "Home day", or "Quick" / "Full". Each is an ordered, user-chosen list of segments, configured in the UI.
3. A **pre-build** action that builds a routine's content and stores it without playing it. It is triggered entirely from outside CastStudio.
4. A **play** action that plays stored content immediately if it is fresh, and otherwise builds it live and then plays. It never fails silently.
5. **Freshness by day.** Pre-built content is only valid for the "briefing day" it was built in, so yesterday's content never plays. Within the day it can be replayed on demand.
6. **No-repeat, as a per-call option.** When it is on, play only the segments of the routine that this person hasn't already heard today.
7. **Audio-only people skip all visual work:** no slide rendering, maps or Imagen calls.
8. **Single-mode commute per person.** A person whose travel mode is `driving` hears only drive time, with no driving-vs-transit comparison.
9. Existing installs keep working after upgrading, with no reconfiguration.

## 3. Non-goals

- **Deciding when anything happens.** CastStudio does not know about schedules, work-from-home days, buttons, tap counts or motion. Home Assistant automations decide when to pre-build and which routine to play. A work-from-home day is simply a different routine, such as "Home day" without the commute segment, chosen by the user's own automation.
- **Detecting single, double or triple taps.** That is the button integration's job. Most button integrations expose an `event.*` entity with event types the automation maps to routines.
- **Per-person AI provider or API keys.** These stay shared, install-wide settings.
- **Keeping a history of past briefings.** Pre-building again replaces earlier content for that day.

## 4. Anti-goals (a failure even if it "works")

- Yesterday's (or any previous day's) pre-built briefing plays as if it were today's.
- Pressing play gives silence or an error because no pre-built content existed. In that case it must build live.
- Upgrading breaks an existing single-person setup or renames its existing entities.
- Household-specific data (names, zones, coordinates, sensor ids) ends up in code, tests, docs or examples. Docs and tests use obviously fake values.
- A segment that only makes sense after another one ("…and after that weather, here's your daily text"). Every segment must stand on its own in any order and any subset.

## 5. Decisions already made with the user

### 5.1 Setup shape

- **One CastStudio entry per install** holds the shared settings: AI provider and API key, Maps API key, output directory and the default transportation mode.
- **People are added inside that entry** with an "Add person" button. This uses Home Assistant **config subentries**: subentry type `person`.
- **Routines are also subentries**, of type `routine`. Each one names exactly one person, a routine name, and an ordered list of segments picked from a selector rather than free text. *(Builder's note: putting routines in subentries is my call, see §7. The user asked that "what plays should be configurable", and picking from a menu in the setup screen was agreed.)*
- **Per-person fields:**
  - display name
  - phonetic name
  - output device (`media_player`)
  - output format (`audio` / `visual`)
  - commute destination zone and travel mode (`fastest` / `driving` / `transit`)
  - optional entities: calendar, scripture/daily-text sensor, weather entity
  - daily reset time (default `03:00`)
  - optional maximum content age (off by default)
- **Fixing the reported error:** adding a second person must go through "Add person", not a second entry. The single-entry rule stays, but its abort message should point users to "Add person" and not just say "already configured".

### 5.2 Segments

- **Segment types:** every existing step type works as a segment: greeting, weather, national_day, fun_fact, agenda/calendar, commute, scripture/daily text and daily_encouragement. One new segment type, **closing**, is added: a short sign-off, "Have a great day, {name}!".
- **Built and stored one by one:** each segment is built and stored on its own: an audio clip, plus a video clip for visual people. Routines for the same person share segments. If "Quick" and "Full" both contain greeting and weather, those are built once and reused.
- **Self-contained narration:** each segment must make sense on its own. The person's name is spoken in greeting and closing only. Segment narration must not refer to previous or next segments. In the new engine this rule replaces `_enforce_recipient_separation`, which keeps the name in the first step only. The old pipeline keeps the old rule (§5.3).
- **Encouragement sign-off:** if a routine contains a `closing` segment, its `daily_encouragement` segment is built without the "Have a great day, {name}!" sign-off, so the name is never said twice. Without `closing`, `daily_encouragement` keeps its sign-off as today. A segment shared between routines with and without `closing` is two different segments (see segment identity, §8).
- **Where settings live:**
  - **Per person:** everything that shapes a segment. That covers the entities (weather, calendar, to-do, scripture, fun-fact, National Day), voices and personalities (host voice and scripture voice), conversation agent, and greeting image/title/subtitle.
  - **Per routine:** only the name and the ordered segment list.
  - **Why:** this keeps segments shareable across a person's routines.
  - **UI layout:** person fields beyond those in §5.1 may go in an "advanced" section of the person form.
- **Live-data segments (weather, commute):** these can go stale within a morning. Keeping them fresh is handled by the user re-running pre-build on their own schedule, for example every 15 minutes during a window. CastStudio adds no internal refresh timer.

### 5.3 Actions

- **`cast_studio.prebuild`**
  - **Targets:** a routine, or a person, which builds the union of all of that person's routines' segments.
  - **Behaviour:** builds and stores the content. Plays nothing.
  - **Visual routines:** for routines of visual people, pre-build also stores the fully stitched routine video, so a plain play needs no stitching.
- **`cast_studio.play`**
  - **Parameters:** a routine, plus an optional `target` media player override and an optional `skip_played` (default `false`).
  - **Behaviour:** plays fresh stored content immediately. If there is none, it builds live, then plays.
- **Existing actions:** `run_routine` and `pregenerate_routine` keep working with their existing fields, including the inline `routine_config`. Existing automations and the blueprint must not break.
  - **Inline config stays on the old pipeline.** A call that passes an inline `routine_config` behaves exactly as it does today. It keeps the whole-routine video, the 24h freshness check, `<output_dir>/<routine>.mp4`, the first-step-only name rule (`_enforce_recipient_separation`) and `sensor.cast_studio_<routine>`. It doesn't count toward "already played today".
  - **Old call naming a routine subentry.** A call with no inline config whose `routine_name` matches a routine subentry is routed to the new engine, as if it were `play`/`prebuild` for that routine. Without inline config and with no matching subentry, today's default-routine behaviour applies.
  - **Two engines on purpose.** The two engines live side by side so existing automations hear exactly what they hear today.

### 5.3a Post-briefing dashboard (the only side action)

- **Optional field:** a routine can set a dashboard to show when the briefing finishes. It names a dashboard and view, and optionally a display; the display defaults to the person's output device. It is shown with `cast.show_lovelace_view`, the same way the existing `post_briefing_actions` work.
- **When it triggers:** after playback of the routine ends. That includes a `skip_played` playback that only played some segments, and the "That's everything" line.
- **When it's skipped:** if the target isn't a Cast display, or no display is set for an audio-only person, CastStudio skips the dashboard and logs a warning.
- **Nothing else:** background actions and slide-timed actions aren't supported on new routines. Users' own automations handle anything else. Inline-config calls keep all their existing action support (§5.3).

### 5.4 Freshness

- **Briefing day:** stored content belongs to the person's **briefing day**. That runs from their daily reset time to the next one, in Home Assistant's local timezone.
- **Stale rules:** content from an earlier briefing day is stale. When the optional maximum age is set, older content is also stale.
- **Stale content is never played.** Play builds live instead.
- **Replay:** within the briefing day, `play` with `skip_played: false` replays the routine as many times as asked.
- **Replacement:** pre-building again within the same day replaces the stored segments.

### 5.5 No-repeat

- **Per-person record:** CastStudio records, per person and briefing day, which segments have been played. The record resets at the person's daily reset time.
- **`skip_played: true`:** plays only the routine's segments not yet played that day, in routine order. For example, "Quick" (greeting, weather) and then "Full" (greeting, weather, commute, daily text, closing) plays commute, daily text and closing.
- **When everything has been heard:** a short spoken line plays, "That's everything for this morning." It is pre-built per person.

### 5.6 Commute and output format

- **Destination and travel mode:** these come from the person. With travel mode `driving` or `transit`, the commute segment covers that single mode only.
- **Audio-only people:** for them CastStudio never renders slides, never fetches Static Maps and never calls Imagen. The fun fact, greeting and National Day image rules apply only to visual people.

### 5.7 Migration

- **Entry upgrade:** the existing entry is upgraded (config entry `VERSION` 1 → 2, via `async_migrate_entry`).
- **First person:** the entry's current recipient name, phonetic name, commute destination and travel mode become the first `person` subentry. Output format defaults to `visual` and the output device is left empty.
- **No routine is migrated.** Today's morning routine isn't stored anywhere: it's either rebuilt from guessed entities or passed inline by the blueprint. Existing setups keep running unchanged on the old pipeline (§5.3), and users create routines in the UI when they're ready.
- **Existing entities:** existing entity ids, including `sensor.cast_studio_<routine>`, stay as they are.
- **Routine names:** unique per person, not install-wide. New-engine sensors are keyed by person and routine (`sensor.cast_studio_<person>_<routine>`), so they never collide with old `sensor.cast_studio_<routine>` sensors. The routine form rejects a duplicate name for the same person.

### 5.8 Pronunciations (added 2026-10-20, user-approved)

- **The problem:** Home Assistant `person` entities have no pronunciation field. Today only the listener's own name is respelled for TTS.
- **No new button:** this is supported inside **Add person** / reconfigure person.
- **Household names apply everywhere.** Every person's `phonetic_name` is used in every briefing, for any listener. A spouse's name in the other person's calendar is spoken correctly without being entered twice.
- **Per-person list:** each person also has a **Pronunciations** list in the advanced section of their form. It holds rows of *written as → spoken as* that the user can add and remove. These rows apply only to that person's own briefings. For a name defined in both places, the listener's own row wins.
- **Applied at TTS time only:** the swap runs on the narration text immediately before the speech engine. It is never applied to the AI prompt or to visual cards, which always show the real spelling. It covers every segment, including `closing` and the all-heard line, in both the new engine and the old inline-config pipeline.
- **Matching rules:** whole words only ("Al" never changes "Alarm"), case-insensitive, and longest written form first ("Mary Anne" before "Mary"). It is a single pass, so a replacement is never re-replaced. Possessives and punctuation are kept ("Siobhan's" becomes "Shiv-awn's").

## 6. Constraints

- **Home Assistant version:** config subentries need a newer Home Assistant than the current `hacs.json` minimum (`2024.6.0`). Raise the minimum to the first release with stable config subentries. The builder verifies the exact release against HA docs. Dev requirements already pin `2026.6.4`.
- **Translations:** all new UI strings go in `strings.json` / `translations/en.json`.
- **Quality gates:** `pytest` green, and `ruff check .` and `ruff format --check .` clean. All existing tests keep passing unless a behaviour change listed here requires updating them. Each such update must name the section of this document it follows.
- **Instant playback:** playing an audio routine from stored segments must start within about 2 seconds of the action call. Stitching audio by stream copy is fine. For a visual routine with `skip_played: false`, the pre-stitched video plays directly. Stitching at play time is only acceptable for `skip_played: true` on visual routines, and must use stream copy: segment clips are encoded with identical parameters so the FFmpeg concat demuxer can `-c copy`.
- **No personal data:** none in code, tests or docs (see §4).

## 7. Calls I made (the user can override)

- **Routines are config subentries** rather than being defined only through service-call YAML, so they are UI-editable.
- **A new `closing` segment type** carries the sign-off, separate from `daily_encouragement` (which keeps its own sign-off).
- **Segments are marked as played when playback of them starts**, not when it completes. Completion isn't reliably observable on every speaker. The cost is that an interrupted briefing counts as heard.
- **Default daily reset time is `03:00`**, configurable per person.
- **The all-heard line** is "That's everything for this morning."
- **Pre-build by person** builds the union of that person's routines.
- **Two engines side by side:** inline-config calls stay on the old pipeline, and calls that name a routine subentry use the new engine (§5.3).
- **Migration creates the person only, not a routine** (§5.7). This differs from what I first described to the user, because the current routine isn't stored anywhere that can be migrated.
- **Segment-shaping settings live on the person,** and a routine is only a name plus a segment list (§5.2).
- **Encouragement sign-off:** when a `closing` segment is present, the encouragement sign-off is dropped (§5.2).
- **Routine names are unique per person,** and new sensors are keyed by person and routine (§5.7).

## 8. Left to the builder

- **Storage layout:** the on-disk layout for segments and manifests, for example `<output_dir>/<person>/<briefing_date>/…`. It must stay inside `output_dir`, and stale days must be pruned.
- **Segment identity:** how a segment is identified, so identical segment configs are shared across routines.
- **Readiness sensors:** sensor and attribute design for per-routine and per-person readiness, keeping existing sensor ids.
- **Field names:** exact field names in the new actions' `services.yaml`, following existing naming.
- **Selector layout:** how segment ordering is presented in the routine subentry form.
