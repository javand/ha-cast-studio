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
  <img src="https://img.shields.io/badge/Home%20Assistant-2025.8.0%2B-blue" alt="Home Assistant Core 2025.8.0+">
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
3. **Play** with `cast_studio.play` from any trigger: an alarm, a button, motion, a time. Playback starts at once, and `skip_played` lets a second press play only what that person has not heard yet today.

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

CastStudio can brief several people, each with their own routines. People and routines are config subentries of the CastStudio entry (**Settings** > **Devices & Services** > **CastStudio**). Home Assistant 2025.8.0 or newer is required (see `hacs.json`).

### Add person
Use **Add person** on the CastStudio entry. Fields:

| Field | Meaning |
| :--- | :--- |
| Name / Phonetic pronunciation | Name shown on screen, and how it is spoken (for example `Al-ex`). |
| Output device (optional) | The media player this person's briefings play on. |
| Output format | `Audio` (sound only) or `Visual` (slides and video). The default is Visual. |
| Work or commute destination zone | This person's own commute destination. |
| Travel mode | `Fastest`, `Driving` or `Transit`. |
| Daily reset time | When the person's briefing day starts (default 03:00). Anything built or played before it counts as the previous day. |
| Maximum content age (minutes) | Stored content older than this is rebuilt before it plays. `0` (default) turns the age check off. |

The **Advanced** section holds the entities and voices used to build segments: weather, calendars, to-do and daily text entities; the commute drive-time and transit-time sensors; host and scripture voices and personalities; conversation agent; greeting title, subtitle and image; and the person's **Pronunciations** list.

**Daily text** lists JW Library daily text sensors and text helpers; use a text helper to feed in any other source. **Fun fact** and **National Day** need no entity: the AI writes them from today's date.

**Calendars** takes any number of calendars, for example a personal calendar and a shared Family calendar. The agenda merges their events into one timeline, and the narration says which calendar an event is on. A person saved with a single calendar keeps it.

**Audio-only people** get sound only: no slides are rendered and no video is stitched.

**Commute.** Each person has their own work zone and their own commute sensors (minutes). Travel mode `Fastest` compares driving and transit; `Driving` or `Transit` gives a single-mode commute with no comparison. If a person has no work zone, or no sensor for their travel mode, the commute segment is skipped with a logged warning and the rest of the briefing still plays.

**Trip home and round trip.** Optionally pick an **Evening drive-home sensor** (and **Evening transit-home sensor**) in **Advanced**. Use a travel-time sensor that predicts the evening trip, such as a Google Maps Travel Time entry from work to home with **Time type** set to *Departure time* at your usual leave time. The commute segment then adds the trip home and a round-trip total, spoken and on the slide ("Home 48 min • Round trip 1h 23m"). CastStudio refreshes the person's commute sensors (`homeassistant.update_entity`) just before reading them, so sensors with polling turned off still give a current value. The commute is rebuilt every time a briefing plays, so it reflects traffic at that moment rather than at pre-build time; the pre-built commute is used if that rebuild fails. A to-work sensor with no departure time ("leave now") suits this best.

### Add routine
Use **Add routine**: pick the person, a routine name (unique for that person) and the segments. Segment types are `greeting`, `weather`, `national_day`, `fun_fact`, `agenda`, `commute`, `scripture`, `daily_encouragement` and `closing`. A `closing` segment says the person's name once, and the encouragement then leaves out its own sign-off. On a display it shows the sign-off over a full-screen photo with a "Happy Thursday!" style day tag: a fresh AI image themed to the weekday and season when Gemini is configured, otherwise a bundled sunrise illustration. The form stores segments in the order submitted, so check the order after saving.

Optionally pick a **Dashboard to show afterwards** and a **Dashboard display**: the dropdown lists every dashboard and its views (for example "Nest Display › Morning"), and the chosen view is shown on a Cast display (`cast.show_lovelace_view`) when the briefing finishes. You can also type a path such as `dashboard-home/morning`. The display defaults to the person's output device and must be a Cast player, otherwise the dashboard is skipped with a warning. Without a dashboard, a Cast player is turned off once it finishes the briefing, so a display leaves the cast screen and returns to its home screen; it is left alone if it is still playing something a minute later.

Each routine gets a status sensor named `sensor.cast_studio_<person>_<routine>` (states `idle`, `generating`, `ready`, `playing`, `error`). Routines added later work at once; their sensor may appear only after the first play or prebuild, or after the next integration reload.

### `cast_studio.prebuild`
Builds and stores segments without playing anything. For Visual people the stitched routine video is stored too.

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
| `skip_played` | no | `false` | Plays only the segment types the person has not yet heard in their briefing day. When everything has been heard, a short "That's everything" line plays. |

### Briefing day, freshness and skip played
- A person's **briefing day** rolls over at their daily reset time. Stored segments from earlier days are not used, and are removed on the next prebuild.
- With a **maximum content age**, stored segments older than that are rebuilt before playing.
- Played segment types are recorded when playback starts. `skip_played: true` relies on that record, which resets with the briefing day.

### AI account problems
When Gemini or OpenAI rejects a request because of the account (key rejected, billing or credits, access denied, or quota used up), briefings still play with the fallback voice and text, and CastStudio tells you:
- Every CastStudio sensor gets an `ai_account_issue` attribute, for example `Gemini billing problem (text, voice, image): Your prepayment credits are depleted.` It is empty while the account is healthy, so an automation can notify you when it changes.
- A notice appears in **Settings → Repairs** with the provider's own message and a link to its account page.

Both clear on their own after the next successful request.

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
description: Single press plays Quick, double press plays Full, skipping what Alex already heard today.
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
