# Announcements — design

Status: approved in chat 2026-10-26.

## Intent

Briefings are per person and rebuilt every briefing day. The user also
wants **household announcements**: built ahead of time and played by their
own automations whenever they need it. Each announcement is one of two
kinds:

- **Static**: one persona, built once, never goes stale. Examples: "Close
  the freezer door", "The front door is locked", "The patio door has been
  left open".
- **Daily**: rebuilt every day so it feels new. Example: a welcome-home
  announcement where a different persona welcomes you back each day, in
  their own AI-written words.

Welcome-home example: "Welcome Alex", "Welcome Sam", and "Welcome Alex and
Sam" as three separate announcements, each with an AI-painted scene of the
people being welcomed home. Which one plays is decided by the user's
automation, outside CastStudio.

The feature is a general tool: the user **describes the slide and what it
says**, and CastStudio builds it. It is not a welcome-home template.

## Goals

- A new subentry type, **Announcement**, owned by the household (no person).
- The user writes a description; the slide image is **painted by the AI
  image model** from that description plus any pictures they supply.
- The spoken words are either **exactly as written** or **written by the AI
  in the persona's character** from the description.
- Static announcements are built once and rebuilt only on save/edit or on
  request. Daily announcements are also rebuilt once a day, with that
  day's persona.
- The user can see the current slide and words in HA and rebuild until
  happy.
- Played by automations with a new service; video on a screen, audio on a
  speaker (reuse `has_screen`).
- If a briefing is playing on the target, it pauses, the announcement plays
  as audio, and the briefing resumes where it was.

## Non-goals

- Deciding who is arriving or when to announce: that stays in the user's
  automations.
- Variants inside one announcement (one announcement = one slide + words).
- Scheduling builds beyond the daily rebuild.
- Linking an announcement to a person: names, pictures and wording come from
  the announcement itself.

## Anti-goals

- An announcement must never fail to play because image generation failed:
  it falls back to a plain slide and still speaks.
- An announcement must never leave an interrupted briefing stopped.
- Building an announcement must never touch any person's briefing files or
  heard records.

## The announcement settings (subentry form)

- **Name** (unique among announcements; used by the service and entity ids).
- **Description**: what the announcement is for, what the slide should show
  and what should be said. The user gives clear instructions here, e.g.
  "AI background of a cosy front door at dusk, Alex and Sam in two circles,
  'Welcome home' across the top" or "use the uploaded image as is, with
  Sam's photo in the bottom-right corner like a profile badge".
- **Wording**: `verbatim` (speak the **Message** field exactly) or
  `creative` (the AI writes the words from the description, in the
  persona's character, following the same spoken-output rules personas
  already use: no stage directions, no emojis, numbers said naturally).
- **Message** (used when wording is `verbatim`; required then).
- **Freshness**: `static` (default) or `daily`.
- **Persona**: from the existing catalog (`personas.py`); supplies voice,
  delivery prompt and, for `creative`, the character. Default:
  `default_host`.
  - Static: exactly one persona (the form rejects more).
  - Daily: one or more personas (Claude's call: a multi-select). Each day's
    rebuild picks one at random from the list, never the same as
    yesterday's when the list has two or more. Selecting one persona gives
    the same voice every day with new words.
- **Rebuild time** (Daily only; ignored for Static): when the daily rebuild
  runs. Default 03:00, matching the briefing reset (Claude's call).
- **Pictures**: zero or more images picked with HA's media selector (so the
  user can upload through the Media panel). Their role (background,
  reference for a person, use as is) is stated in the description.
- **Output format**: `audio` or `visual` (default `visual`), same meaning as
  for routines. An Audio announcement builds no image.

Household pronunciations (every person's phonetic name and pronunciation
lists) apply to the spoken words, as in briefings.

## Building

- On create, on any edit, and on the rebuild action, CastStudio builds:
  1. the words (verbatim, or one AI text call for creative);
  2. the speech with the persona's voice and delivery (existing TTS path,
     same fallbacks);
  3. for Visual: the slide image from the Gemini image model
     (`gemini-2.5-flash-image`, already used for the closing image), sent
     the description and the pictures as reference images, asked for a
     1024×600 slide. If the description says to use a picture as is,
     still sent to the model with that instruction (the model decides
     placement); the user accepted that the AI may redraw people;
  4. the clip (slide + speech) with the same uniform encode as segments.
- **Fallback:** with no Gemini image access (no key, account problem, or an
  error), the slide is a plain card rendered by the existing slide renderer:
  the first picture as background if any, otherwise the default background,
  with the announcement name as title. It still speaks. The failure is
  logged and surfaced like other AI account issues.
- Files live under the output dir in an `announcements/<announcement id>/`
  folder with versioned names (a play still streaming the old file is never
  overwritten). Old versions are removed on the next build once not in use.
- Builds of one announcement are serialized; a build does not hold any
  person's lock.
- **What triggers a build** (Claude's call): a change to the
  announcement's own settings, a rebuild request, no successful build on
  disk, or, for Daily only, the rebuild time passing. A restart or
  integration reload reuses the last build from its sidecar: store a
  fingerprint of the settings (and, for Daily, the build day) with the
  build and compare. Creative words must not change just because HA
  restarted. A Daily announcement whose rebuild time passed while HA was
  down rebuilds once at startup.
- **Daily rebuild** (Claude's call): rebuilds everything (words, speech,
  slide) with the day's persona, in the background. Plays never wait for
  it: until it finishes, the previous day's build plays. If it fails,
  yesterday's build stays in use and the status shows `error`; it is
  retried at the next rebuild time or on a rebuild request.
- **Text call fails** (creative wording; Claude's call): a previous good
  build stays in use and the status shows `error` with the reason. With no
  previous build, the Message field is spoken if filled, otherwise the
  announcement name; the status shows `fallback`.
- **Pictures** (Claude's call): local media only (Media panel uploads and
  other local media sources); the form rejects other sources. At most 4
  pictures. An unreadable picture is skipped with a warning and the build
  goes on; the fallback slide then uses the default background.

## Seeing and retrying

- Each announcement gets an **image entity** showing its current slide
  (Audio announcements: no image, or a placeholder) with attributes: the
  spoken words, wording mode, freshness, the persona used for the current
  build, built_at, and a `status`
  (`building`, `ready`, `error`, `fallback`).
- A **rebuild** action: service `cast_studio.rebuild_announcement` with
  `announcement`, plus a button entity on the announcement's device. Each
  rebuild is a new attempt; the entity updates when it finishes.

## Playing

- Service `cast_studio.announce` with `announcement` (required) and `target`
  (required media player; there is no person default).
- Video (slide clip) when the announcement is Visual and `has_screen(target)`;
  otherwise the audio file.
- If the announcement has never been built successfully, it is built first.
- During a rebuild, a play uses the previous build. The service returns once
  playback has started, like `cast_studio.play`; announcements for the same
  target queue and play in order (Claude's call).
- CastStudio allows a single config entry, so announcement names are unique
  within it and the service resolves by name.
- **Interrupting a briefing:** if a CastStudio briefing is playing on the
  target, CastStudio records the media position, plays the announcement as
  **audio only** on that target, then re-casts the briefing's media and
  seeks to the recorded position, with slide sync resuming from there. A
  second or two of gap is expected (Cast has no true pause-for-another-
  sound). The briefing's sensor stays `playing`; its post-play dashboard or
  release still happens once, at the real end. A failed resume leaves the
  briefing sensor at `error` (as the failed-cast fix does), never stuck.
- Two announcements on the same target play one after another.
- If the briefing's position is unknown or stale, it resumes from the start
  and logs a warning (Claude's call).
- On a target that isn't playing a briefing, nothing is paused; after the
  announcement, a Cast player CastStudio woke is released as for briefings
  (turned off when it finishes), unless it was already in use.

## Left to the builder

- Exact image prompt wording and how pictures are attached as inline data.
- Entity naming, the device for announcements, and the button.
- Where the interrupt/resume logic lives (orchestrator vs briefing) and how
  the playing briefing's position is read (`media_position` plus
  `media_position_updated_at`).
- Storage of built-file metadata (sidecar json next to the files, like
  routine media).
- Translation strings and README section.
