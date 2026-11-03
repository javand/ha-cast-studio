# One default voice per person, voice changes per routine

Date: 2026-11-03
Status: approved design, ready for a plan

## Why

The person form has two persona pickers, **Host personas** and **Reader
personas**. People don't know what a "reader" is, or why the daily text
should get a different voice from the rest of the briefing. The split
hard-codes one opinion (the daily text sounds reflective) into the model
for everyone.

The owner wants something simpler and more flexible:

- **One default voice per person**, used by every segment of every routine.
- **Per-routine voice changes**: inside a routine, under **Advanced**, pick
  the segments that should sound different and the persona(s) for each.

No segment type gets special voice treatment. If you want the daily text
read by Malenfant, you say so in that routine.

## Goals

- The person form has a single **Voice** picker (personas, one or several).
  Several means a different one each briefing day, as today.
- Each routine form has a **Voice changes** list in an Advanced section.
  Each row is one segment of that routine plus one or more personas
  (several rotate daily, like the default voice).
- Segments stay shared across routines when they sound the same: a segment
  is built once per persona per briefing day, whichever routines use it.
- Every persona is usable for every segment. The host/reader roles go away.

## Non-goals

- No "sensitivity" or category attribute on segment types that implies a
  different voice. The owner ruled this out explicitly as too complex.
- No support for installs other than this household. Only two person
  profiles exist (Javan, Jamillah); nobody else runs the integration yet.
- No per-routine changes to anything other than the voice (wording style,
  images and so on stay per person).
- No changes to announcements beyond what removing roles requires.
  Announcements already pick from all personas.

## Anti-goals (would make this a failure even if it works)

- A routine that doesn't use voice changes costs more to build than today,
  or two routines with the same voice for a segment build it twice.
- Two routines that use the same persona list for a segment disagree on
  which persona speaks it on the same day.
- A voice change for a segment the routine doesn't have silently lingers
  and confuses later edits.

## Current state (for the builder)

Branch `feat/person-form-cleanup` (3 unpushed commits on top of `main`)
already removed the typed voice/personality fields, made the greeting image
a media picker, gave each persona a one-line `summary` shown in pickers as
"Name: summary", pre-fills the persona pickers with the defaults, and fixed
the release version. This work builds on that branch and ships with it as
one PR.

Relevant code today:

- `personas.py`: `Persona` has `roles` (host/reader). `pick_persona(ids,
  role, person_id, day)` filters by role and seeds the daily pick with
  `person_id:role:day`. `default_host` / `default_reader` personas exist;
  `announcements.py` uses `default_host` as its default persona id.
- `segments.py`: `_role_voice()` picks host or reader voice;
  `build_segment_step()` gives `scripture` and `daily_encouragement` the
  reader voice and everything else the host voice. Segments are stored per
  person per briefing day in one folder with a `manifest.json` keyed by
  segment key (segment type, plus `_nosignoff` for the encouragement
  variant). `routine_segment_keys()` turns a routine into keys;
  prebuild builds the union of keys across a person's routines.
- `briefing.py`: stitched routine video/audio sidecars record which segment
  keys (and their `built_at`) they were stitched from. Refresh rebuilds
  dynamic keys (weather, commute) per routine.
- `play_history.py`: tracks played **segment types** per routine per day.
  Unaffected: a voice change doesn't change what "already heard" means.
- `config_flow.py`: person form has `host_personas` / `reader_personas`
  pickers; routine form has person, name, segments (ordered multi-select),
  output format, dashboard, dashboard display.

## Decisions made with the owner

1. **Profile voice.** One picker, label **Voice**, help text along the lines
   of: "Who speaks every segment of this person's routines. Pick several to
   get a different one each day. A routine can change the voice of
   individual segments under Advanced." Pre-filled with the default persona
   when empty (as the branch already does for host).
2. **Rotation.** Both the profile voice and each voice change accept one or
   several personas; several rotate daily.
3. **Routine form: option A.** A **Voice changes** list in an Advanced
   section of the routine form, in the same style as the person form's
   Pronunciations list: add a row, pick a segment, pick persona(s). Rows
   only for segments that should differ; unlisted segments use the
   person's voice.
4. **Which segments the row can name.**
   - Editing a routine: the segment dropdown lists only the segments the
     routine had when the form opened. (HA forms can't update live as
     segment chips change; a segment added in the same edit can get a voice
     change on the next edit. The owner accepted this.)
   - Adding a routine: the dropdown lists every segment type; on submit, a
     row for a segment not picked for the routine is rejected with a form
     error such as "Weather isn't in this routine".
   - Removing a segment from a routine drops its voice change on save.
5. **No special segments.** Everything defaults to the person's voice,
   including the daily text and daily encouragement.
6. **Carrying over existing setups.** Jamillah's `host_personas`
   (`["elena"]`) becomes her voice. Javan has none and gets the default.
   Neither has `reader_personas`; it is dropped. Reading the old
   `host_personas` key as a fallback when the new key is absent is enough
   (the same pattern used for `calendar_entity` / `todo_entity`); no
   migration step is needed.
7. **A voice change rewrites the segment in character.** A persona shapes
   both the words (via the writer prompt) and the sound, so each persona
   variant of a segment is a full build: its own writing, slide and image.
   Two routines can therefore say slightly different things for the same
   segment on the same day (a different fun fact, say). The owner accepted
   this; the alternative (write once, re-voice per persona) was rejected as
   not worth the extra build and storage work.
8. **Source text is never rewritten.** Segments whose words come from a
   source are read word for word by every persona; the persona changes only
   the delivery. Today that is the daily text (`scripture`: built verbatim
   from the sensor in `generator.py`, no writer call when the sensor has
   text) and the fixed "That's everything" line. This must keep holding
   for any persona chosen through a voice change. Every other segment is
   written in the persona's character. No per-segment labelling is needed
   in the UI; the Voice changes help text should say that the daily text is
   always read word for word and a persona only changes how it sounds.
9. **Ship together** with the existing `feat/person-form-cleanup` branch as
   one PR, with a version bump in `manifest.json` (0.7.0 is already set on
   the branch; keep it unless the builder sees a reason otherwise).

## Builder's calls already made (owner can override)

- **Persona catalogue:** remove `roles` from `Persona`. Keep the
  `default_host` and `default_reader` ids (announcements and stored data
  reference `default_host`) but rename their display names, e.g.
  "News Anchor" and "Life Coach", since "default" no longer means
  anything. Their summaries must not repeat the new name (an existing test
  checks this).
- **Default voice** for a person with nothing picked: the persona
  `default_host`.
- **Same day, same pick:** the daily pick for a list of personas depends
  only on the person, the briefing day and the set of personas in the
  list, not on the routine or segment. So any two places with the same
  list pick the same persona that day, and a list identical to the
  person's default voice always lands on the same build.
- **Rotation of the writer's character:** `voice_personality` and
  `voice_profile` come from the persona chosen for that segment (default
  voice or voice change), exactly as `_role_voice` does today for a role.
- **Storage identity:** a stored segment is identified by its segment key
  **and the persona that voiced it**. Two routines asking for the same
  segment voiced by the same persona on the same day get the same stored
  file; a different persona is a separate stored segment. How that is
  encoded (key suffix, nested manifest entry, folder) is left to the
  builder, as long as stitched-routine sidecars and freshness checks keep
  working and today's folders built before the change are simply rebuilt.
- **"That's everything" clip** (all-heard) stays per person and uses the
  person's default voice.

## Left to the builder

- Field and key names for the new settings (e.g. `voice_personas` on the
  person, `voice_changes` on the routine) and the shape of a voice-change
  row, within the decisions above.
- How the Voice changes rows label their segment (HA's object selector
  shows one field as the row label; it should read as the segment's
  display name, not its id, if HA allows).
- Exactly how prebuild, play, refresh and the stitched routine files
  resolve each routine segment to (key, persona), and how a refresh of a
  dynamic segment covers every persona variant routines use that day.
- Test strategy (the repo uses pytest with TDD; follow it), README, SPEC
  and translation updates (`strings.json` and `translations/en.json` must
  stay identical).
- Removing the now-dead host/reader plumbing (`_role_voice`, role
  constants, role-filtered pickers) and the related strings.
