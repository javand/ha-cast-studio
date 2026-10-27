<p align="center">
  <img src="custom_components/cast_studio/brand/logo.png" alt="CastStudio Logo" width="400">
</p>

<p align="center">
  <strong>Personal narrated briefings for every person in your home</strong>
</p>

<p align="center">
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg" alt="HACS"></a>
  <a href="https://github.com/javand/ha-cast-studio/releases"><img src="https://img.shields.io/github/v/release/javand/ha-cast-studio?include_prereleases" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2025.12.0%2B-blue" alt="Home Assistant Core 2025.12.0+">
</p>

---

**CastStudio** turns your Google Cast displays (Nest Hub, Chromecast, Android TV) and smart speakers into a personal morning studio.

Instead of a plain text-to-speech readout, each person in the house gets their own narrated briefing: 1024×600 video slides with multi-voice neural speech on a display, or audio alone on a speaker.

---

## 🎬 How It Works

```
   Settings → CastStudio                 Your automations
 ┌──────────────────────────┐   ┌───────────────────────────────────┐
 │ People: name, voice,     │   │ 04:30  cast_studio.prebuild       │
 │   device, calendars,     │   │ alarm  cast_studio.play           │
 │   commute sensors        │   │ button cast_studio.play           │
 │ Routines: ordered        │   │        (skip_played: true)        │
 │   segments per person    │   └────────────────┬──────────────────┘
 └────────────┬─────────────┘                    │
              ▼                                  ▼
     Segments built ahead of time ──────► Played instantly; the
     (AI text, voice, slides) and         commute is rebuilt live
     stored for the briefing day          so traffic is current
```

1. **Set up people and routines** in the integration: who gets a briefing, where it plays, and which segments it contains (greeting, weather, National Day, fun fact, agenda, commute, scripture, encouragement, closing).
2. **Pre-build** overnight with `cast_studio.prebuild`, so nothing waits on AI or video encoding in the morning.
3. **Play** with `cast_studio.play` from any trigger: an alarm, a button, motion, a time. Playback starts at once, and `skip_played` lets a second press play only what that routine has not played yet today.

---

## ✨ Key Features

- **Zero-Credential AI Auto-Detection:** Reuses the API key from your existing Home Assistant `google_generative_ai` (Gemini) integration, or falls back to a native Home Assistant `conversation.*` agent, so you never re-enter keys. If the AI account has a billing, key or quota problem, CastStudio tells you (see [AI account problems](#ai-account-problems)).
- **Atmospheric Visual Themes & Weather Gradients:** Dynamic palettes tailored to each slide (`morning`, `weather_sunny`, `weather_rainy`, `weather_snow`, `weather_cloudy`, `calendar`, `evening`, `scripture`) with weather-adaptive gradients and category badge pills (`GREETING`, `FORECAST`, `COMMUTE`, `SCHEDULE`, `TASKS`, `SCRIPTURE`).
- **Commute Comparison:** Compares each person's driving and transit sensors and says which is faster and by how much, or reports one mode for a `Driving` or `Transit` person. Optional evening sensors add the trip home and a round-trip total.
- **Live Google Maps Route Map:** When a Google Maps API key is configured (the `maps_api_key` option, or automatically reused from a `google_travel_time`/`google_maps` entry or `secrets.yaml`), the commute slide shows a dark-mode route map from `zone.home` (or the HA home location) to the person's work zone, cached per mode per day. The key needs the Directions and Static Maps APIs enabled. With no key, or if the request fails, a locally drawn dark map and the sensor-based times are used.
- **Weather Preparation Advisory:** The weather slide shows a one-line advisory chip and the narration mentions it: umbrella (rain chance of 30% or more, or a rain/storm/shower condition), warm coat (chilly), light jacket/sweater (cool), dress light and stay hydrated (hot), or limit outdoor exertion (outdoor AQI above 100, which takes priority).
- **Multiple Calendars:** The agenda merges any number of calendars (personal, Family, work) into one timeline and says which calendar an event is on, then moves on to the to-do list.
- **Daily Encouragement:** With a daily text it gives 2 practical takeaways in plain language; with none, a short general good-day remark. It is drawn locally as a frosted card on a dawn gradient.
- **Selective Dynamic AI Backgrounds:** AI images are generated only for `greeting`, `fun_fact` (illustrating the fact), `national_day` (the day's observance) and `closing` (themed to the weekday and season). Other slides use bundled artwork or local renderers, and a failed image falls back to bundled art.
- **Dual-Voice TTS & Style Conditioning:** Separate host and scripture voices (e.g. `Pulcherrima` and `Enceladus`) and delivery personalities (e.g. `news_anchor`, `life_coach`, `calm_reassuring`) per person.
- **Strict Identity Contract:** Slides show the real name ("Alex"); the phonetic spelling ("Al-ex") and each person's pronunciation list only change what is spoken.
- **Display Wake-Up & Audio Safety:** Turns the target display on, unmutes it and sets the volume before playback, so a briefing is never silent.
- **Dashboard Hand-Off or Release:** A routine can show a Lovelace dashboard on the display when it finishes; without one, the Cast display is turned off and returns to its home screen.
- **Aggressive Cast Cache-Busting:** Adds a timestamp query (`?v=<timestamp>`) to media URLs so Cast receivers never replay yesterday's video from memory.
- **Native Google Cast Network URLs:** Resolves `/config/www/...` media to network URLs (`get_url(hass)`) that Cast devices can stream over local Wi-Fi.
- **Tidy Storage:** Segments live in per-person, per-day folders under `/config/www/cast_studio/people/`; earlier days are removed on the next prebuild.

---

## 👥 People, Routines & Briefings

CastStudio can brief several people, each with their own routines. People and routines are config subentries of the CastStudio entry (**Settings** > **Devices & Services** > **CastStudio**). Home Assistant 2025.12.0 or newer is required (see `hacs.json`).

### Add person
Use **Add person** on the CastStudio entry. Fields:

| Field | Meaning |
| :--- | :--- |
| Name / Phonetic pronunciation | Name shown on screen, and how it is spoken (for example `Al-ex`). |
| Output device (optional) | The media player this person's briefings play on. |
| Work or commute destination zone | This person's own commute destination. |
| Travel mode | `Fastest`, `Driving` or `Transit`. |
| Daily reset time | When the person's briefing day starts (default 03:00). Anything built or played before it counts as the previous day. |
| Maximum content age (minutes) | Stored content older than this is rebuilt before it plays. `0` (default) turns the age check off. |

The **Advanced** section holds the entities and voices used to build segments: weather, calendars, to-do and daily text entities; the commute drive-time and transit-time sensors; host and scripture voices and personalities; conversation agent; greeting title, subtitle and image; and the person's **Pronunciations** list.

**Daily text** lists JW Library daily text sensors and text helpers; use a text helper to feed in any other source. **Fun fact** and **National Day** need no entity: the AI writes them from today's date.

**Calendars** takes any number of calendars, for example a personal calendar and a shared Family calendar. The agenda merges their events into one timeline, and the narration says which calendar an event is on. A person saved with a single calendar keeps it.

**Personas.** CastStudio ships a cast of speakers. Each one shapes both the voice and the wording of the parts it speaks, but never names itself. In **Advanced**, pick **Host personas** for the greeting, weather, agenda, commute, fun fact and closing, and **Reader personas** for the daily text and daily encouragement. Each picker lists only the personas suited to that role. Pick one to always hear it, or several to get one at random each briefing day; every part of that day's briefing keeps the same speaker. Leave a picker empty to keep the host or scripture voice and personality fields, exactly as before.

| Persona | Roles | Character |
| :--- | :--- | :--- |
| Default Host | Host | The original clear, friendly news-anchor host |
| Default Reader | Reader | The original calm, warm life-coach reader |
| Elena | Host, Reader | Warm, grounded young Saint Lucian woman |
| Malenfant | Reader | Mature, pastoral mentor |
| Della | Host, Reader | Joyful, soothing Southern auntie |
| Cole | Host | Cultured, composed Brooklyn gentleman |
| Arthur | Host | Crisp British butler with a dry wit |
| Theo | Host | Upbeat morning-radio host |
| Nana Rose | Host, Reader | Gentle, unhurried grandmother |
| Sage | Reader | Calm meditation guide |
| Ray | Host | No-nonsense news and traffic anchor |
| Pip | Host | Playful, kid-friendly guide |

A persona change takes effect on the next pre-build or briefing day.

**Commute.** Each person has their own work zone and their own commute sensors (minutes). Travel mode `Fastest` compares driving and transit; `Driving` or `Transit` gives a single-mode commute with no comparison. If a person has no work zone, or no sensor for their travel mode, the commute segment is skipped with a logged warning and the rest of the briefing still plays.

**Trip home and round trip.** Optionally pick an **Evening drive-home sensor** (and **Evening transit-home sensor**) in **Advanced**. Use a travel-time sensor that predicts the evening trip, such as a Google Maps Travel Time entry from work to home with **Time type** set to *Departure time* at your usual leave time. The commute segment then adds the trip home and a round-trip total, spoken and on the slide ("Home 48 min • Round trip 1h 23m"). CastStudio refreshes the person's commute sensors (`homeassistant.update_entity`) just before reading them, so sensors with polling turned off still give a current value. The commute is rebuilt every time a briefing plays, so it reflects traffic at that moment rather than at pre-build time; the pre-built commute is used if that rebuild fails. A to-work sensor with no departure time ("leave now") suits this best.

### Add routine
Use **Add routine**: pick the person, a routine name (unique for that person) and the segments. Segment types are `greeting`, `weather`, `national_day`, `fun_fact`, `agenda`, `commute`, `scripture`, `daily_encouragement` and `closing`. A `closing` segment says the person's name once, and the encouragement then leaves out its own sign-off. On a display it shows the sign-off over a full-screen photo with a "Happy Thursday!" style day tag: a fresh AI image themed to the weekday and season when Gemini is configured, otherwise a bundled sunrise illustration. The form stores segments in the order submitted, so check the order after saving.

Pick an **Output format** for the routine. **Visual** (the default) builds slides and video. It plays the video on a device with a screen (a Nest Hub, Chromecast or Google TV) and the audio on a speaker, so one routine can serve both, for example a bathroom routine that your automation sends to whichever room you walk into. **Audio** builds sound only, with no slides or video, for routines that only ever play on speakers, such as a night-time routine. A Cast group always gets audio. A player whose model isn't reported counts as a screen when its name contains "display".

Optionally pick a **Dashboard to show afterwards** and a **Dashboard display**: the dropdown lists every dashboard and its views (for example "Nest Display › Morning"), and the chosen view is shown on a Cast display (`cast.show_lovelace_view`) when the briefing finishes. You can also type a path such as `dashboard-home/morning`. The **Dashboard display** picker lists only Google Cast players, because only Cast can show a dashboard. It defaults to the person's output device, which must also be a Cast player (a Squeezebox or other non-Cast player cannot show a dashboard); otherwise the dashboard is skipped with a warning, so set a Cast display when the routine plays on another kind of player. Without a dashboard, a Cast player is turned off once it finishes the briefing, so a display leaves the cast screen and returns to its home screen; it is left alone if it is still playing something a minute later.

Each routine gets a status sensor named `sensor.cast_studio_<person>_<routine>` (states `idle`, `generating`, `ready`, `playing`, `error`). Routines added later work at once; their sensor may appear only after the first play or prebuild, or after the next integration reload.

### `cast_studio.prebuild`
Builds and stores segments without playing anything. For Visual routines the stitched routine video, and a stitched audio copy for speakers, are stored too.

| Field | Required | Meaning |
| :--- | :--- | :--- |
| `person` | no | At least one of `person` or `routine` is required. Alone: builds the union of segments of all of that person's routines. With `routine`: picks whose routine is meant. |
| `routine` | no | Routine to pre-build. At least one of `person` or `routine` is required. |

### `cast_studio.play`
Plays a routine now from fresh stored segments. Missing or stale segments are built live first.

| Field | Required | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `routine` | yes | | Routine to play. |
| `person` | no | | Needed only when more than one person has a routine with this name. |
| `target` | no | person's output device | Media player to play on instead. |
| `volume` | no | default level | Sets the player to this volume, from `0` to `1`, before playing (for example `0.5` for the bathroom). Leave it out for the default level. |
| `skip_played` | no | `false` | Plays only the segment types this routine has not yet played in the person's briefing day. Each routine keeps its own record, so hearing the weather in one routine does not skip it in another. When everything has been heard, a short "That's everything" line plays. |

### Briefing day, freshness and skip played
- A person's **briefing day** rolls over at their daily reset time. Stored segments from earlier days are not used, and are removed on the next prebuild.
- With a **maximum content age**, stored segments older than that are rebuilt before playing.
- Played segment types are recorded per routine when playback starts. `skip_played: true` relies on that routine's record, which resets with the briefing day.

### AI account problems
When Gemini or OpenAI rejects a request because of the account (key rejected, billing or credits, access denied, or quota used up), briefings still play with the fallback voice and text, and CastStudio tells you:
- Every CastStudio sensor gets an `ai_account_issue` attribute, for example `Gemini billing problem (text, voice, image): Your prepayment credits are depleted.` It is empty while the account is healthy, so an automation can notify you when it changes.
- A notice appears in **Settings → Repairs** with the provider's own message and a link to its account page.

Both clear on their own after the next successful request.

### When the AI voice is slow or unavailable
- If a Gemini or OpenAI voice request times out or fails with a network or server error, CastStudio tries it once more after a second. A request the provider rejects outright (for example a bad key or no credits) is not retried; see [AI account problems](#ai-account-problems).
- If the second try also fails, that segment is voiced by the fallback Home Assistant voice, and the warning `... falling back to Home Assistant TTS` is logged.
- A segment voiced by the fallback voice is marked in its manifest (`degraded`). A play still uses it as it is, so a provider outage never delays a briefing. The next `cast_studio.prebuild` rebuilds every segment and replaces it with the AI voice.
- The "That's everything" line is normally built once a day; while it is voiced by the fallback voice, any build replaces it.
- To fix a segment that was voiced by the fallback voice, run `cast_studio.prebuild` for that routine again.

### Pronunciations
- Every person's phonetic name applies **household-wide**, in everyone's briefings.
- Each person's own **Pronunciations** list (written as, spoken as) applies to that person's briefings and wins any conflict.
- Matching is whole-word and case-insensitive, longest first. A hyphen-joined word part is not matched: "Al" will not change "Al-Anon".
- Respelling changes speech only, never the text on screen.

### Example automations
The examples assume the Quick, Full and Home day routines already exist. Paste each one into the automation editor via **Edit in YAML**. The names (`event.bathroom_button`, `media_player.kitchen_speaker`, Alex, Sam, `zone.office`) are made up; replace them with your own. The Home Assistant `time` trigger has no weekday option, so the weekday examples pair a `time` trigger with a `time` condition.

**Bathroom button: single press for Quick, double press for Full**

```yaml
alias: Bathroom button briefing
description: Single press plays Quick, double press plays Full; each skips what that routine already played today.
mode: queued
triggers:
  - trigger: event.received
    target:
      entity_id: event.bathroom_button
    options:
      event_type:
        - single
    id: quick
  - trigger: event.received
    target:
      entity_id: event.bathroom_button
    options:
      event_type:
        - double
    id: full
actions:
  - choose:
      - conditions:
          - condition: trigger
            id: quick
        sequence:
          - action: cast_studio.play
            data:
              person: Alex
              routine: Quick
              skip_played: true
      - conditions:
          - condition: trigger
            id: full
        sequence:
          - action: cast_studio.play
            data:
              person: Alex
              routine: Full
              skip_played: true
```

Press types: the `event_type` values come from the button entity's `event_types` attribute (see **Developer Tools** > **States**), and the names vary by integration. A double press only works if the device reports one. If it doesn't, use a different button, or count two presses within a short window yourself.

**Weekday pre-build at 04:30**

```yaml
alias: Pre-build weekday briefings
description: Builds Alex's and Sam's briefings before they wake up, Monday to Friday.
mode: single
triggers:
  - trigger: time
    at: "04:30:00"
conditions:
  - condition: time
    weekday: [mon, tue, wed, thu, fri]
actions:
  - action: cast_studio.prebuild
    data:
      person: Alex
  - action: cast_studio.prebuild
    data:
      person: Sam
```

**Wake-up: play when the phone alarm goes off**

```yaml
alias: Wake-up briefing
description: Plays Alex's Full routine on the bedroom display when the phone alarm rings on a weekday.
mode: single
triggers:
  - trigger: time
    at: sensor.alex_phone_next_alarm
conditions:
  - condition: time
    weekday: [mon, tue, wed, thu, fri]
actions:
  - action: cast_studio.play
    data:
      person: Alex
      routine: Full
      target: media_player.bedroom_display
```

`sensor.alex_phone_next_alarm` is the **Next alarm** sensor from the Home Assistant Companion app (Android). A `time` trigger accepts any timestamp sensor, so a clock or alarm integration's next-alarm sensor works the same way. Without one, use a fixed time such as `at: "06:30:00"`.

**Tuesday and Friday: Home day**

```yaml
alias: Home day briefing
description: On Tuesdays and Fridays Sam plays the Home day routine on the kitchen speaker.
mode: single
triggers:
  - trigger: time
    at: "06:45:00"
conditions:
  - condition: time
    weekday: [tue, fri]
actions:
  - action: cast_studio.play
    data:
      person: Sam
      routine: Home day
      target: media_player.kitchen_speaker
      skip_played: true
```

For a work-day routine with a commute, set the person's work zone (for example `zone.office`) and commute sensors in **Add person** > **Advanced**.

---

## 📣 Announcements

Announcements are short household messages, not tied to a person: "Close the freezer door", "The front door is locked", or a welcome-home slide. You describe each one once; CastStudio builds the words, the voice and a slide, and your own automations play it whenever they need it.

### Add announcement
Use **Add announcement** on the CastStudio integration.

| Field | Meaning |
| :--- | :--- |
| Name | Used by the actions and in entity ids. Unique among announcements. |
| Description | What the announcement is for, what the slide should show and what should be said, for example "AI background of a cosy front door at dusk, Alex and Sam in two circles, 'Welcome home' across the top". |
| Wording | **Exactly as written** speaks the Message. **Written by the persona** lets the AI write the words from the description, in the persona's character. |
| Message | The words to speak with exactly-as-written wording. |
| Freshness | **Static** (the default) is built once and rebuilt only when you edit it or ask. **Daily** is rebuilt every day at the rebuild time, so it feels new each day. |
| Personas | Who speaks. Static: one persona. Daily: one or more; each day one is picked at random, never the same as the day before. |
| Rebuild time | Daily only: when the daily rebuild runs (default 03:00). |
| Pictures | Up to 4 pictures from your local media (upload them in the Media panel). Say in the description how each is used. |
| Output format | **Visual** builds a slide and plays video on screens. **Audio** builds speech only. |
| Play on | Optional. The speaker or display it plays on when an automation or the **Play** button names no other. |

The slide is painted by Gemini's image model from the description and your pictures; it may redraw the people in them. Without Gemini image access the slide is a plain card with the name (and your first picture), and the announcement still speaks. A restart reuses the last build, so creative words don't change on their own. If a rebuild fails, the previous build keeps playing.

Each announcement gets an image entity, `image.cast_studio_announcement_<name>`, showing the current slide with the spoken words, wording, freshness, persona, build time and `status` (`idle`, `building`, `ready`, `error`, `fallback`) as attributes, and a **Rebuild** button, `button.cast_studio_rebuild_<name>`.

To try an announcement, open its device page: **Play** (`button.cast_studio_play_<name>`) plays it on the player shown in **Play on** (`select.cast_studio_play_on_<name>`). Play on starts at the announcement's own Play on setting; pick another player there to test on it. That choice lasts until you change the setting or restart.

### `cast_studio.announce`
Plays an announcement now. Video on a device with a screen, audio otherwise. The action returns once the announcement is playing; announcements sent to the same player play one after another.

If a CastStudio briefing is playing on that player, it pauses: the announcement plays as audio only, then the briefing is cast again and picks up where it was (a second or two of gap is normal). If the player can't say where the briefing was, it restarts from the beginning.

| Field | Required | Meaning |
| :--- | :--- | :--- |
| `announcement` | yes | Name of the announcement. |
| `target` | no | Media player to play on. Defaults to the announcement's Play on setting. |

```yaml
actions:
  - action: cast_studio.announce
    data:
      announcement: Freezer Door
      target: media_player.kitchen_speaker
```

### `cast_studio.rebuild_announcement`
Builds an announcement again now: new words for creative wording, and a new slide. Fails if the build fails, leaving the previous build in use.

```yaml
actions:
  - action: cast_studio.rebuild_announcement
    data:
      announcement: Welcome Sam
```

---

## 🚀 Installation

### Via HACS (Recommended)
1. Open HACS in your Home Assistant dashboard.
2. Select **Integrations** > **Custom repositories**.
3. Add `https://github.com/javand/ha-cast-studio` with category **Integration**.
4. Click **Download**, then restart Home Assistant.

### Manual Installation
1. Download the `custom_components/cast_studio` folder from the latest release.
2. Copy it into your Home Assistant `<config_dir>/custom_components/` directory.
3. Restart Home Assistant.
4. Go to **Settings** > **Devices & Services** > **Add Integration**, search for **CastStudio**, then add a person and a routine.

---

## 📄 License & Credits

- **Author & Codeowner:** Javan Dicks ([@javand](https://github.com/javand))
- **License:** MIT License
