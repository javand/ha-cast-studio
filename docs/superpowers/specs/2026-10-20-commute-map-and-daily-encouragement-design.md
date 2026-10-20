# Design Specification: Geographically Accurate Commute Map & Daily Encouragement Slide

**Document Date:** October 20, 2026  
**Status:** In Review  
**Target Component:** `custom_components/cast_studio`  

---

## 1. Intent & The Why

CastStudio produces an AI-powered visual and spoken morning routine displayed on Google Nest Hubs and Google Cast smart displays.

This design addresses two high-impact enhancements requested by the user:
1. **Live Geographically Accurate Commute Route**:
   The current commute slide uses a static pre-rendered image. The user wants this replaced with a real, geographically accurate Google Maps view of the route between home and work (or the transit subway route). It must emulate the Google Nest Hub display experience when asking "Hey Google, what's traffic to work?", comparing live driving and subway options, selecting the fastest mode by default, and supporting both global and daily mode overrides.
2. **Post-Scripture Daily Encouragement Slide**:
   Following the Daily Text / Scripture slide, add a new final slide that offers short, warm, and practical encouraging remarks for having a great day. This slide draws practical, real-world application from the day's reflection, avoids religious jargon (explicitly omitting words like "blessed"), and pairs with a dynamic, daily-generated AI background image (via Gemini Imagen) to deliver an inspiring, grounded conclusion to the briefing.

---

## 2. Goals, Non-Goals & Anti-Goals

### Goals
- **Real-World Route Map**: Render an authentic dark-mode Google Maps view with the exact turn-by-turn route polyline from `zone.home` to the configured work zone using Google Maps Directions & Static Maps API with the user's existing `google_maps_api_key`.
- **Multi-Modal Commute Comparison**: Ingest both `sensor.javan_commute_drive_to_work` (driving) and `sensor.javan_commute_metro` (transit), computing travel times, difference in minutes, and highlighting the fastest mode.
- **Configurable Mode Preference**: Allow setting a default mode in CastStudio options (`"fastest"`, `"driving"`, `"transit"`), with the ability to override per-day or per-service-call (`transportation_mode: "driving" | "transit" | "fastest"`).
- **Google Nest Hub Commute Layout**: A sleek 1024×600 visual HUD with a prominent ETA hero card, arrival time, distance, and comparison chips for both Driving and Subway.
- **New Routine Step (`daily_encouragement`)**: Added immediately after `scripture` in the default morning routine.
- **Dynamic Imagen Allowlist**: Explicitly constrain dynamic Gemini Imagen generation to `fun_fact` and `daily_encouragement`. All other slides (`greeting`, `weather`, `agenda`, `commute`, `scripture`) utilize pre-bundled AI artwork, Google Maps static imagery, or local procedural vector layouts to eliminate unnecessary generation latency and API cost.
- **Weather Actionable Advice**: In the weather slide's spoken narration and visual layout, provide specific practical preparation guidance based on the day's conditions (e.g. pack an umbrella for rain/precipitation, grab a jacket if chilly, dress light if hot, or remain indoors/limit outdoor exertion if air quality or pollution is elevated).
- **Clean & Punchy Encouragement Card**: Frosted glass card overlay with a category badge (`☀️ TODAY'S TAKEAWAY`), a bold focal insight, 2 short practical applications, and a clean sign-off (`"Have a great day, {recipient_name}!"`).
- **Spoken Audio Synthesis**: Gemini TTS voice synthesis speaks the commute comparison and encouragement seamlessly within the single composite briefing video.

### Non-Goals
- Turn-by-turn active navigation in video (this is a glanceable briefing overview, not an interactive GPS navigation app).
- Modifying underlying Home Assistant zones or external entity state sensors.
- Re-generating map images every minute (cached daily and per-routine run to respect API quotas and maintain fast rendering).

### Anti-Goals
- **No Hallucinated Map Lines**: Never draw synthetic random lines across the map; route polylines must come from Google Directions API or exact geographic coordinates.
- **No Religious or Preachy Jargon**: Strictly avoid words like "blessed" or sermonizing tone; maintain a grounded, thoughtful, and human coaching tone.
- **No Text Overflow**: Design coordinates and font sizes must guarantee cards fit comfortably within the 1024×600 boundary on Google Nest Hub displays.

---

## 3. Architecture & Data Contracts

### 3.1 Commute Map Service & Ingestion Pipeline

#### Sourcing Geographic Route & Map
1. **Origin & Destination Zone Discovery**:
   - Origin: `zone.home` latitude/longitude (fallback: `hass.config.latitude`, `hass.config.longitude`).
   - Destination, from configuration only: the step's `destination_zone`, then the `commute_destination_zone` option (a zone picked in the config/options flow), then the primary drive sensor's `destination` attribute (a zone id or `"lat,lon"`), then the generic `zone.work` / `zone.office`. No coordinates are hardcoded. If no valid destination is found, gracefully skip external map fetching and use procedural dark vector background.
2. **API Key Resolution Hierarchy**:
   - 1st: `entry.options.get(CONF_MAPS_API_KEY)` / `entry.data.get(CONF_MAPS_API_KEY)`.
   - 2nd: Direct inspection of `secrets.yaml` for `google_maps_api_key`, `google_maps_key`, or `gmaps_api_key`.
   - 3rd: Inspection of `hass.data` entries for `google_maps` or `google_travel_time` domains.
   - 4th: Fallback to procedural dark vector background if no key is found or network is offline.
3. **Google Directions Query (Single Query per Routine)**:
   - To preserve API quotas and minimize routine compilation latency, only **one** Directions query is made per routine compilation: for the *selected/faster mode* (`driving` or `transit`).
   - Request: `https://maps.googleapis.com/maps/api/directions/json?origin={lat},{lon}&destination={dest_lat},{dest_lon}&mode={selected_mode}&key={api_key}`
   - Extracts:
     - `overview_polyline.points` (encoded polyline)
     - `summary` (e.g. `"I-66 W"`)
     - `distance.text` (e.g. `"24.5 mi"`)
     - `duration.text` (e.g. `"35 mins"`)
     - `duration.value` (in seconds)
4. **Google Static Maps Fetch**:
   - `https://maps.googleapis.com/maps/api/staticmap?size=512x300&scale=2&format=png&maptype=roadmap&style={dark_mode_styles}&path=weight:6%7Ccolor:0x38bdf8ff%7Cenc:{encoded_polyline}&markers=color:blue%7Clabel:H%7C{origin}&markers=color:red%7Clabel:W%7C{dest}&key={api_key}`
   - `512x300` at `scale=2` returns exactly 1024x600 px and stays within Google's documented 640x640 `size` limit.
   - Cached to `/config/www/cast_studio/cache/commute_map_{mode}_{date}.png`.
   - **Fallback**: If API key is missing or request fails, falls back to a clean procedural vector dark map or bundled asset.

#### Sensor Ingestion & Comparison Logic
In `generator.py` (`async_extract_entity_context`):
- **Sensor Discovery Heuristics**:
  - `drive_sensor`: First entity matching `drive`, `driving`, `waze`, `car`, or `traffic` in entity_id or `device_class == "duration"` (e.g. `sensor.javan_commute_drive_to_work`).
  - `transit_sensor`: First entity matching `metro`, `transit`, `subway`, `train`, or `bus` in entity_id, or `icon in ("mdi:train", "mdi:subway")` (e.g. `sensor.javan_commute_metro`).
- **Data Authority**:
  - Live travel times, differences, and ETAs are **authoritatively sourced from the Home Assistant sensors** so they match the user's dashboard.
  - The transit route label (e.g. `"Silver Line"`) is read directly from the transit sensor's `line` attribute (`sensor.javan_commute_metro.attributes.get("line") or "Metro Transit"`).
  - The driving route label (e.g. `"I-66 W"`) comes from the Directions API summary (or defaults to `"Driving"`).
- **Single-Mode Fallback**:
  - If only a driving sensor exists (no transit sensor found in HA), `transit_time` is set to `None`, the comparison chips are omitted in `slide_renderer.py`, and spoken TTS announces only the driving commute without comparison.
- **Contract Schema (`card["commute_data"]`)**:
  ```python
  {
      "selected_mode": "driving",
      "drive_time": 35,
      "transit_time": 44,
      "diff_minutes": 9,
      "faster_mode": "driving",
      "drive_summary": "I-66 W",
      "transit_summary": "Silver Line",
      "summary": "via I-66 W",
      "distance": "24.5 mi",
      "eta": "8:45 AM",
      "traffic_condition": "typical",
      "map_image_path": "/config/www/cast_studio/cache/commute_map_driving_2026-10-20.png",
  }
  ```

---

### 3.2 Daily Encouragement Slide Pipeline

#### Routine Step Definition
In `__init__.py` (`_build_default_morning_routine_config`):
```python
{
    "name": "daily_encouragement",
    "type": "daily_encouragement",
    "visual": True,
    "title": "Today's Takeaway",
    "subtitle": "Daily Encouragement",
    "entity": scripture_sensor,  # Contextual dependency on the daily text
    "prompt": (
        "Based on today's Scripture and Daily Text reflection, provide warm, uplifting, "
        "and practical encouraging remarks for having a great day. "
        "Distill exactly 2 short, actionable takeaways for everyday life (e.g. managing stress, "
        "patience with others, focusing on priorities). "
        "Keep the tone conversational, grounded, and human. Do NOT use religious jargon or the word 'blessed'. "
        "Conclude with a clean host send-off: 'Have a great day, {recipient_name}!'"
    ),
}
```

#### Dynamic Image Generation
In `generator.py` (`is_step_image_dynamic`):
- Include `daily_encouragement`:
  ```python
  if step_name in ("fun_fact", "daily_encouragement") or step_type in ("fun_fact", "daily_encouragement"):
      return True
  ```
- Prompt generator: creates a photorealistic, serene morning sunrise, peaceful path, or warm natural light landscape without text.
- Image cached daily to `/config/www/cast_studio/cache/daily_encouragement_{date}.jpg`.

---

### 3.3 Weather Actionable Preparation Advisory Pipeline

#### Data Ingestion & Deterministic Advisory Generation
In `generator.py` (`async_extract_weather_data`):
- In addition to temperatures and forecast points, compute an actionable preparation recommendation string:
  - If `precipitation_probability >= 30` or `condition` contains `"rain"`, `"storm"`, `"pouring"`, or `"drizzle"`:
    `advisory = "Pack an umbrella for rain today"`
  - Else if `current_temp <= 50` or `low <= 45`:
    `advisory = "Wear a warm coat today, it's chilly"`
  - Else if `current_temp <= 65` or `high <= 65`:
    `advisory = "Grab a light jacket or sweater"`
  - Else if `current_temp >= 85` or `high >= 85`:
    `advisory = "Dress light & stay hydrated, it's going to be hot"`
  - Else:
    `advisory = "Comfortable weather expected today"`
- Check AQI: if an air quality sensor in HA indicates unhealthy air (AQI > 100), prepend or override with:
  `"Limit outdoor exertion due to poor air quality"`.
- Store this in `weather_data["advisory"]` and forward into `card["weather_data"]["advisory"]`.

#### Weather Prompt Update
In `ai_client.py` (`STEP_PROMPT_GUIDELINES["weather"]`):
- Update guideline:
  `"4. WEATHER NARRATION: State the current sky condition and temperature, highlight today's expected high/low forecast, and crucially provide a practical daily recommendation based on the conditions (e.g., whether to grab an umbrella for rain, wear a warm jacket/coat if it's chilly, dress in light/breathable clothes if it's hot, or stay indoors/limit outdoor exertion if air quality or pollution is poor).\n"`

---

## 4. Visual Layout Specifications (1024×600)

### 4.1 Google Nest Hub Commute Layout (`slide_renderer.py`)
- **Canvas (1024×600)**:
  - Background: Full-bleed Google Static Maps dark-mode PNG image.
  - Subtle dark gradient vignette (`rgba(15, 23, 42, 0.45)`) applied to ensure text legibility while keeping the route clearly visible.
- **Top Header (`Y: 25`, `X: 30`)**:
  - Pill Badge: `🚗 COMMUTE TO WORK` (`#0284c7` background, white bold 13px text).
  - Subtitle: `Fastest route via I-66 W • Typical traffic` (Slate-300, 14px text).
- **Hero ETA HUD Card (`Y: 68`, `X: 30`, Width: ~340px, Radius: 16px)**:
  - Frosted slate container (`rgba(15, 23, 42, 0.88)`, blur 12px, border `rgba(255,255,255,0.14)`).
  - Hero Duration: Large 46px Emerald-400 (`#10b981`) bold text `35 min`.
  - Badge Chip: `FASTER BY 9 MIN` (`#065f46` fill, `#34d399` text, 12px bold).
  - ETA Row: `Arrive around 8:45 AM • 24.5 mi` (`#94a3b8`, 14px).
- **Mode Comparison Chips (`Bottom Left`, `Y: 515`, `X: 30`)**:
  - Drive Chip: Selected mode styling (`rgba(2, 132, 199, 0.28)`, border 2px `#38bdf8`, 🚗 icon, `35 min • I-66 W`).
  - Metro Chip: Unselected mode styling (`rgba(15, 23, 42, 0.78)`, border 1px `rgba(255,255,255,0.12)`, 🚇 icon, `44 min • Silver Line`).

### 4.2 Post-Scripture Daily Encouragement Layout (`slide_renderer.py`)
- **Canvas (1024×600)**:
  - Background: Dynamic Gemini Imagen sunrise / peaceful morning nature wallpaper.
- **Frosted Content Card (`Inset: 40px`, Width: 944px, Height: 520px, Radius: 18px)**:
  - Container: `rgba(15, 23, 42, 0.75)`, 16px blur, border `rgba(255, 255, 255, 0.18)`.
  - **Category Pill (`Y: 30`)**:
    - `☀️ TODAY'S TAKEAWAY` (`rgba(245, 158, 11, 0.25)` fill, `#f59e0b` border, `#fde68a` 13px bold text).
  - **Hero Insight Headline (`Y: 75`)**:
    - 32-34px bold `#ffffff` font, 2 lines max: e.g. *"Focus on what you can control, and take the day one step at a time."*
  - **Actionable Bullet Takeaways (`Y: 180`)**:
    - Exactly 2 clean bullet rows with Sky-400 / Emerald-400 bullets, 18px text:
      - `• Keep your perspective: Don't let morning delays derail your peace.`
      - `• Prioritize with care: Focus your energy on the people and tasks that matter most today.`
  - **Host Sign-Off (`Bottom`, `Y: 460`)**:
    - Border divider (`rgba(255,255,255,0.12)`).
    - Left: `"Have a great day, Javan!"` (Amber-200 `#fde68a`, 16px bold).
    - Right: `CastStudio Morning Host` (Slate-400 `#94a3b8`, 13px).

### 4.3 Google Nest Hub Weather Layout Advisory Chip Update (`slide_renderer.py`)
- In `_draw_google_nest_weather_slide`:
  - When `weather_data.get("advisory")` is present, render an advisory pill chip in the metrics row (`Y: 275` to `325`) next to humidity/wind/precip chips or in the hero section:
    - Chip background: `rgba(2, 132, 199, 0.35)` (or `rgba(245, 158, 11, 0.35)` if umbrella/chill warning), border 1.5px `#38bdf8`, text white/amber bold 14px: e.g. `☂️ Pack an umbrella today` or `🧥 Grab a light jacket`.

---

## 5. Configuration & Service Schema Updates

### Options Flow & Defaults
- In `config_flow.py` and `const.py`:
  - `CONF_DEFAULT_TRANSPORTATION_MODE`: Choice of `["fastest", "driving", "transit"]` (Default: `"fastest"`).
  - `CONF_MAPS_API_KEY`: Optional override for Google Maps API key (defaults to resolving from `secrets.yaml` or integration data).

### Service Parameters (`services.yaml`)
- `cast_studio.run_routine` and `cast_studio.pregenerate_routine`:
  - Add optional `transportation_mode` parameter:
    ```yaml
    transportation_mode:
      name: Transportation Mode Override
      description: Override mode for today's commute ('fastest', 'driving', 'transit').
      required: false
      selector:
        select:
          options:
            - label: "Fastest Travel Time"
              value: "fastest"
            - label: "Driving"
              value: "driving"
            - label: "Metro Transit"
              value: "transit"
    ```

---

## 6. What's Left to the Builder
- Exact polyline simplification/decoding utility or using standard Google Maps polyline query parameters.
- Handling network timeout when querying Google Maps API (fall back gracefully to procedural dark map).
- Unit tests covering missing API key, transit faster than drive, and zero-scripture encouragement fallback.

---

## 7. Verification & Acceptance Criteria
1. **Commute Map Accuracy**: When `run_routine` compiles, the commute slide features a real dark-mode Google Maps view with the exact route polyline between Home and Work.
2. **Nest Hub HUD**: Displays prominent travel time in minutes, fastest mode chip, and ETA.
3. **Daily Encouragement**: Appears immediately after Scripture, displays 2 practical takeaways without religious jargon, and ends with `"Have a great day, {name}!"`.
4. **Dynamic Imagen**: Only `fun_fact` and `daily_encouragement` trigger dynamic Imagen generation; all other slides use instant local renderers or cached templates.
5. **Quality Gates**: All unit tests pass (`pytest -v`), 0 Ruff errors (`ruff check .`, `ruff format --check .`).
