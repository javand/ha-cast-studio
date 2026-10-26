# Output format per routine — design

Status: approved in chat 2026-10-26.

## Intent

Today the output format (Audio or Visual) is a setting on the **person**.
But where a briefing plays depends on the **routine**, not the person:

- A morning routine plays on a display, so it wants slides and video.
- A night-time routine only ever plays on a speaker, so building slides and
  video for it wastes build time and AI calls.
- A bathroom routine can land on either: the user's own automation picks the
  master bathroom display or the guest bathroom speaker (that choice is made
  outside CastStudio and passed as the play `target`).

So the format moves to the routine, and play picks video or audio from the
device it is sent to.

## Goals

- Each routine has an **Output format**: `audio` or `visual`.
- Prebuild builds only what the routine's format needs.
- Play sends video to a device with a screen and audio to everything else,
  with no new play field: automations keep passing `target` as today.
- Existing setups keep behaving exactly as before after upgrade.

## Non-goals

- No third "both" option. A Visual routine already has an audio file for
  every segment (a visual build writes the `.mp3` next to each `.mp4`), so
  Visual already covers "play on either".
- No format override on the `play` service. Can be added later if a case
  comes up.
- No change to how automations choose the device.

## Anti-goals

- A Visual routine sent to a speaker must never fail or wait on a build
  just because the device can't show video.
- An Audio routine must never trigger slide rendering or slide AI calls.
- Upgrading must not change what any existing routine plays on a screen.
  (One deliberate change: a Visual routine sent to a speaker now gets the
  audio file instead of the video file. Claude's call: a speaker can't show
  the video anyway.)

## The setting

- Routine subentry gains `output_format` (`audio` | `visual`), shown on the
  routine add and reconfigure forms with the same selector, label and help
  text the person form uses today.
- New routines default to `visual` (works on any device; the user switches
  night-time routines to Audio). Decided by Claude.
- The person form loses its Output format field. `Person.output_format` is
  removed; code that reads it reads the routine's format instead.
- **Upgrade:** a config entry migration copies each person's current
  `output_format` (default `visual`) onto each of that person's routines that
  has none, then drops the key from the person. Bump the entry's minor
  version for this (add a minor-version branch to `async_migrate_entry`;
  today it only checks `entry.version`). Routines created after upgrade get
  the form default. A routine whose person no longer exists gets `visual`.

## Building segments

Segments are stored per person and shared between that person's routines
(a greeting built for one routine is reused by another the same day). So a
segment's format comes from the routines that need it:

- A build of a set of keys builds a segment **visual** when any routine in
  that build that uses the key is Visual; otherwise audio-only.
- A visual segment satisfies an Audio routine (it has the `.mp3`).
- An audio-only segment does **not** satisfy a Visual routine when the play
  is going to a screen device, nor a Visual routine's prebuild: it counts as
  missing and is rebuilt with a clip, as `_usable_segment` does today for a
  visual person.
- The all-heard line is built visual when the person has any Visual routine.
- `segments.py` reads `person.output_format` today (the step's `visual` flag
  and `is_visual` in the single-segment build). The build functions take the
  set of keys to build visual instead. Any build with no routine context
  uses "visual if the person has any Visual routine". Claude's call.

## Prebuild

- **Audio routine:** builds its segments audio-only and stitches nothing new
  (today's audio-person behaviour).
- **Visual routine:** builds segments with clips and stores the stitched
  routine video, as today, **and** also stores a stitched routine audio file
  with its own sidecar, so a play to a speaker needs no stitching. The audio
  stitch is Claude's call.
- Prebuild of a person (no routine given) does the above per routine over
  the union of keys.

## Play

Play decides **video** or **audio** once, from the routine and the device:

- video when the routine is Visual **and** the target is a screen device;
- audio otherwise (Audio routine on any device; Visual routine on a speaker).

Then:

- video: today's visual path (pre-built routine video if fresh, else
  stitch clips; rebuild segments missing clips);
- audio: use `.mp3` files only (pre-built routine audio if fresh, else
  stitch); any fresh segment qualifies, with or without a clip.
- `skip_played`, the all-heard line, play history, supersede and the
  failed-play handling all work as today; only the media choice changes.
- The dashboard-after-playing setting is unchanged (it already checks for a
  Cast display on its own).

### Which devices have a screen

The check runs on the resolved player: the `target` when given, otherwise
the person's media player. Only Cast players can be screens: a non-Cast
player (for example a Squeezebox) is always audio, so the model match never
sees "Harmony Hub"-style devices. A target with no device-registry entry
uses the name fallback below, and is audio if that doesn't match.
Claude's calls.

From the target entity's device in the device registry:

- **screen:** model contains `Hub` (Nest Hub, Hub Max), or `Chromecast` but
  not `Chromecast Audio`, or `Google TV`.
- **no screen:** everything else, including every `Google Cast Group`
  (a group can't play video), Nest/Home Mini, Home Max, Home Speaker,
  Chromecast Audio, and other brands (Onkyo, Sony, JBL, ...).
- **fallback:** when the device has no model, a device or entity name
  containing `display` (case-insensitive) counts as a screen.

A Chromecast always gets video: it can't be known whether it feeds a TV or
a receiver, and video plays its sound either way (user's call).

Checked against the user's real devices on 2026-10-26: Bathroom/Kitchen
Display report `Google Nest Hub`; the Guest Bathroom speaker reports
`Google Nest Mini`; TVs report `Chromecast` or `Google TV Streamer`; groups
report `Google Cast Group`. The same device can appear twice (cast and
google_home integrations); the entity's own device is the one to read.

## Left to the builder

- Where the screen check lives (orchestrator next to `_is_cast_player`, or
  briefing) and its exact matching code.
- File names and sidecar shape for the stored routine audio.
- How the visual-or-audio decision is passed through the play helpers.
- Translation strings beyond moving the existing ones.
- README updates (the person settings table and the routine settings).
