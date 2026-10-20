<p align="center">
  <img src="custom_components/cast_studio/brand/logo.png" alt="CastStudio Logo" width="400">
</p>

<p align="center">
  <strong>Autonomous Multimedia Routines & Visual Alerts for Home Assistant</strong>
</p>

<p align="center">
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg" alt="HACS"></a>
  <a href="https://github.com/javand/ha-cast-studio/releases"><img src="https://img.shields.io/github/v/release/javand/ha-cast-studio?include_prereleases" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2024.6.0%2B-blue" alt="Home Assistant Core 2024.6.0+">
</p>

---

**CastStudio** transforms your Google Cast smart displays (Google Nest Hub, Chromecasts, Android TVs) and smart speakers into an executive multimedia production studio.

Instead of plain text-to-speech audio chimes, CastStudio compiles synchronized, television-grade 1024×600 MP4 video briefings and instant visual alerts paired with personality-conditioned multi-voice neural TTS and concurrent smart home actions.

---

## 🎬 The 3 Media Production Tiers

CastStudio operates across three execution tiers designed for real-world smart home needs:

```
                     ┌────────────────────────────────────────────────────────┐
                     │          Home Assistant Blueprint / UI                 │
                     └──────────────────────────┬─────────────────────────────┘
                                                │
                 ┌──────────────────────────────┼──────────────────────────────┐
                 ▼                              ▼                              ▼
      [Static Reusable Clips]        [Pre-Generated Daily Shows]     [On-Demand Experiences]
   e.g. Freezer Door, Alarms       e.g. Morning Briefing, Bedtime   e.g. Welcome Home [Name]
   • Custom/static graphic icon    • Real-time weather, cal, tasks  • Person avatar/camera snapshot
   • Compiled once, cached         • Silent overnight pre-render    • Generated fresh on trigger
   • 0s instantaneous playback     • 0s instant wake-up playback    • 10-15s contextual render
```

### 1. ⚡ Static Reusable Clips (0s Latency)
Compiled once and permanently cached in `/config/www/cast_studio/static/`. When an urgent sensor trips (e.g. freezer door left open, garage door open, security trip), CastStudio plays the pre-compiled video immediately with zero rendering delay.

### 2. ☀️ Pre-Generated Daily Shows (0s Wake-Up Latency)
Pre-renders silently in the background overnight (e.g. at 04:30 AM) while the household is asleep. When your morning alarm or motion sensor fires at 07:00 AM, the video briefing starts playing **instantly** without waiting 15–30 seconds for AI or video encoding.

### 3. 🏡 On-Demand Dynamic Greetings
Generated on the fly when unexpected contextual events occur, such as a family member arriving home (`welcome_home.yaml`), embedding their person profile picture and delivering a personalized arrival summary.

---

## ✨ Key Features

- **Zero-Credential AI Auto-Detection:** Automatically extracts active API keys from your existing Home Assistant `google_generative_ai` (Gemini 2.5 Flash) integration, or gracefully falls back to native Home Assistant `conversation.*` agents without forcing you to re-enter API keys.
- **Atmospheric Visual Themes & Weather Gradients:** Dynamic palettes tailored to each slide (`morning`, `weather_sunny`, `weather_rainy`, `weather_snow`, `weather_cloudy`, `calendar`, `evening`, `scripture`) with weather-adaptive atmospheric gradients and category badge pills (`GREETING`, `FORECAST`, `COMMUTE`, `SCHEDULE`, `TASKS`, `SCRIPTURE`).
- **Multi-Sensor Commute Comparison:** Automatically compares travel times across multiple transit sensors (e.g. Drive vs. Metro/Transit) and calculates which route is faster and by how many minutes. By default (`fastest`) the faster of driving and transit is chosen; set a Default Transportation Mode (`fastest`, `driving` or `transit`) in the integration options, or override it per service call or routine with `transportation_mode`. A `run_routine` call that passes `transportation_mode` always compiles fresh media instead of replaying the pregenerated video.
- **Live Google Maps Route Map:** When a Google Maps API key is configured (the `maps_api_key` option, or automatically reused from a `google_travel_time`/`google_maps` entry or `secrets.yaml`), the commute slide shows a dark-mode route map (home to the configured destination, with the route polyline and markers), cached per mode per day. The origin is `zone.home` (or the HA home location). The destination comes from configuration only: the step's `destination_zone`, the **Commute Destination Zone** option (pick any existing zone), the drive sensor's `destination` attribute (a zone id or `"lat,lon"`), or a generic `zone.work`/`zone.office`. With no destination, no map is fetched. The key needs the Directions and Static Maps APIs enabled. Pregenerated briefings show the commute as a trip length ("35 min trip") because they play hours after compiling; live runs show a clock arrival time. With no key, or if the Directions/Static Maps request fails or times out, it silently falls back to a locally drawn dark map and the sensor-based times.
- **Weather Preparation Advisory:** The weather slide shows a one-line advisory chip and the narration tells you about it: umbrella (rain chance of 30% or more, or a rain/storm/shower condition), warm coat (chilly), light jacket/sweater (cool), dress light and stay hydrated (hot), or limit outdoor exertion (outdoor AQI above 100, which takes priority). Air quality is read from the weather attributes or an outdoor AQI sensor.
- **Daily Encouragement Slide:** A `daily_encouragement` step follows scripture in the default morning routine. With a daily text it gives 2 practical takeaways in plain language without religious jargon; with none it gives a short general good-day remark. The narration always ends "Have a great day, <Name>!". It is drawn locally as a reusable frosted card on a procedural dawn gradient, with no AI image.
- **Selective Dynamic AI Backgrounds:** Imagen backgrounds are generated only for `greeting` (fresh every run), `fun_fact` (illustrating the specific fact) and `national_day` (relevant to the day's holiday). All other slides use bundled artwork or local renderers unless a step sets `dynamic_image: true`.
- **Modern HA 2024.6+ Forecast Extraction:** Uses async `weather.get_forecasts` service queries to provide accurate daily high/low temperatures and conditions directly to the narration engine.
- **Dual-Voice TTS & Style Conditioning:** Assign independent speaker voices (e.g. `Pulcherrima` and `Enceladus`) and delivery personalities (e.g. `morning_host`, `news_anchor`, `life_coach`, `calm_reassuring`, `soothing`, `meditative`) to different steps in the same routine, with support for custom personality registration.
- **Strict Identity Contract:** Visual cards strictly display your real name (`name`, e.g. "Javan"), while phonetic spellings (`phonetic_name`, e.g. "Jay-ven") are isolated exclusively to TTS audio prompts to guarantee accurate pronunciation without awkward on-screen text.
- **Display Wake-Up & Audio Safety:** Automatically turns on the target display, unmutes audio, and initializes volume (default: 60%) before video starts, preventing missed or silent briefings.
- **Aggressive Cast Cache-Busting:** Appends dynamic timestamp query parameters (`?v=<timestamp>`) to media URLs, forcing Google Cast receivers to download fresh video instead of replaying yesterday's cached media from RAM.
- **Deep Entity & Attribute Ingestion:** Extracts complex sensor attributes (e.g. `scripture` and `text` from `sensor.jw_library_daily_text_today`), handles empty calendar schedules cleanly ("No scheduled meetings today"), and prompts AI for an uplifting closing reflection.
- **Post-Routine Dashboard Hand-Off:** Configurable `post_routine_actions` hook executes smart home actions (such as `cast.show_lovelace_view` for `nest-display/morning`) the moment video playback finishes.
- **Partitioned Action Execution:** Non-visual smart home actions (turning on kitchen lights, starting the espresso machine) fire concurrently via `asyncio.gather`, while visual-linked actions execute at the precise moment their slide appears during playback.
- **Native Google Cast Network URLs:** Automatically resolves local `/config/www/...` media storage to accessible network HTTP URLs (`get_url(hass)`) so physical Cast devices can stream smoothly over your local Wi-Fi.
- **Smart Storage Hygiene:** Compiles fixed targets with overwrite (`-y`), purges temporary files in `/tmp/cast_studio/` inside guaranteed `finally:` blocks, and automatically prunes stale routine media older than 24 hours while permanently preserving static clips.

---

## 📦 Blueprints Library

CastStudio ships with official Home Assistant blueprints located in `blueprints/automation/cast_studio/`:

| Blueprint | Category | Description |
| :--- | :--- | :--- |
| **`morning_briefing.yaml`** | ☀️ Daily Show | Complete 7-step visual morning show (`greeting` → `weather` → `national_day` → `fun_fact` → `agenda` → `commute` → `scripture`; the integration's built-in default morning routine adds `daily_encouragement` after `scripture`) with overnight 04:30 AM pre-generation, dual voices, audio safety preparation, and Lovelace dashboard cast hand-off upon completion. |
| **`bedtime_routine.yaml`** | 🌙 Daily Show | Evening wind-down briefing with relaxing voice styling, bedside display dimming, and concurrent shutdown actions (locking doors, turning off lights). |
| **`event_alert.yaml`** | 🚨 Static Alert | Reusable instant alert when a sensor trips (e.g. freezer door left open for 60s, garage open) with custom graphic icons (`dripping_ice.png`, `door_alert.png`). |
| **`welcome_home.yaml`** | 🚪 Arrival Greeting | Contextual greeting when a family member arrives home, displaying their avatar or camera snapshot and playing a custom welcome message with dynamic caching. |

---

## 🛠️ Service Reference

### `cast_studio.run_routine`
Executes a multimedia routine. Plays pre-compiled static clips or pre-generated daily shows instantly, or compiles on demand if media is missing.

```yaml
action: cast_studio.run_routine
data:
  routine_name: morning_briefing
  target_player: media_player.bathroom_display
  force_regenerate: false
  post_routine_actions:
    - action: cast.show_lovelace_view
      target:
        entity_id: media_player.bathroom_display
      data:
        dashboard_path: nest-display
        view_path: morning
```

### `cast_studio.pregenerate_routine`
Silently compiles media and companion metadata ahead of time without waking the house or triggering smart devices.

```yaml
action: cast_studio.pregenerate_routine
data:
  routine_name: morning_briefing
  routine_config:
    visuals: true
    steps:
      - name: weather
        visual: true
        voice: Pulcherrima
        personality: news_anchor
        entity_id: weather.home
```

### `cast_studio.compile_clip`
Compiles a static reusable alert video clip directly into `/config/www/cast_studio/static/<clip_name>.mp4`.

```yaml
action: cast_studio.compile_clip
data:
  clip_name: freezer_door_alert
  step_config:
    title: "Freezer Door Open!"
    message: "Attention: The kitchen freezer door has been left open."
    image_path: "/config/custom_components/cast_studio/assets/dripping_ice.png"
    voice: Pulcherrima
    duration: 6.0
```

### `cast_studio.reload`
Reloads integration configuration, blueprints, and resets routine status sensors (`sensor.cast_studio_<routine>`).

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
3. Copy `blueprints/automation/cast_studio/*.yaml` into `<config_dir>/blueprints/automation/cast_studio/`.
4. Restart Home Assistant.
5. (Optional) Go to **Settings** > **Devices & Services** > **Add Integration** and search for **CastStudio** to configure default profile settings or API key overrides.

---

## 📄 License & Credits

- **Author & Codeowner:** Javan Dicks ([@javand](https://github.com/javand))
- **License:** MIT License
