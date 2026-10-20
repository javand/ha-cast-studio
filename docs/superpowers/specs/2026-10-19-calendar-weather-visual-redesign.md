# Design Specification: Calendar Dual-Focus Agenda & Google Nest Display Weather

**Date:** 2026-10-19  
**Status:** Draft / Review  
**Author:** Antigravity  

---

## 1. Intent & Background

CastStudio renders 1024x600 visual slides for Google Nest Hub and Google Cast smart displays during daily routines (such as the morning briefing). 

During testing on Nest Hub displays, two specific visual slides required redesign:
1. **Calendar / Agenda Slide**: Previously, calendar and to-do items were drawn over a generic decorative background image of a desk planner, which lacked the feel of an actual live calendar and suffered from text contrast issues. The user selected **Option B: Dual-Focused Agenda**, a clean, dedicated schedule timeline and task board that directly reflects live calendar events and pending to-do items.
2. **Weather Slide**: Previously, the weather slide showed a generic or winter image background. The user wants the weather slide to mirror the authentic **Google Nest Hub Display Weather Experience** (the screen Google Assistant displays when asked for the weather), featuring a prominent hero temperature, condition icon, high/low spread, live condition chips, and an hourly forecast timeline along the bottom.

---

## 2. Goals, Non-Goals & Anti-Goals

### Goals
- **Dual-Focused Agenda Slide**:
  - Render a 2-column split board:
    - **Left Column (Schedule & Events)**: Chronological event timeline featuring time chips (`[ 10:00 AM ]`), event summaries, and location/subtext.
    - **Right Column (Priority Tasks)**: To-do checklist featuring status checkboxes (`[ ]`), task summaries, and pending task count chips.
  - Header with clean date badge (`Monday, Oct 19`) without redundant "Good Morning" or repetitive title bubbles.
  - Render on high-contrast frosted slate panels (`#0f172a` / `#1e293b`) over an ambient planner backdrop so text is crisp and legible from across the room.
  - Handle empty states gracefully (e.g., "No scheduled meetings today • Schedule is clear", "All tasks completed").
- **Google Nest Hub Weather Display**:
  - Render an authentic Google Assistant smart display weather layout:
    - **Hero Temperature**: Large bold current temperature (e.g., `68°`).
    - **Condition & Spread**: Large condition icon (`🌤️`), condition description (`Mostly Sunny`), and high/low spread (`High 74° • Low 54°`).
    - **Metrics Strip**: Chips for humidity (`💧 48%`), wind (`💨 8 mph`), and precipitation probability (`☂️ 10%`).
    - **Hourly Forecast Timeline**: 5 to 6 horizontal time slots along the bottom (`Now`, `+2h`, `+4h`, `+6h`, `+8h`, `Tomorrow`) with forecast weather icons and expected temperatures.
  - **Dynamic Weather Atmosphere**: Visual theme/backdrop dynamically selected based on current weather condition (sunny daylight blue gradient, rainy cool slate, overcast neutral gray, clear night deep indigo).
- **Data Pipeline Ingestion**:
  - In `generator.py`, enhance weather entity context extraction to call `weather.get_forecasts` (or inspect state attributes) to obtain `forecast` entries (hourly or daily), current humidity, wind speed, and precipitation chance.
  - In `generator.py` and `ai_client.py`, pass structured `calendar_events` and `todo_items` directly into the agenda card payload for `slide_renderer.py`.

### Non-Goals
- Changing audio narration synthesis or Gemini TTS voices (Pulcherrima / Enceladus).
- Modifying other slides (Greeting, National Day, Fun Fact, Commute, Scripture) which were already updated and validated.

### Anti-Goals
- Text overlapping busy, unshielded photographic backgrounds.
- Tiny fonts that cannot be read on a 7-inch or 10-inch Nest Hub at typical bathroom/kitchen viewing distances.
- Hardcoded or fake hourly temperatures when live forecast data is available from Home Assistant's `weather` platform.

---

## 3. Detailed Visual Architecture & Layout (1024x600)

### 3.1 Dual-Focused Agenda Slide (`slide_renderer.py`)

- **Canvas Size**: 1024 x 600 px.
- **Background**: Ambient modern planner aesthetic (`assets/calendar_agenda.jpg`) darkened by a subtle full-screen vignette/scrim (`rgba(10, 15, 26, 0.75)`).
- **Header**:
  - Left: Title `TODAY'S SCHEDULE & TASKS` (Font size 26, bold, `#f8fafc`).
  - Right: Date pill `Monday, Oct 19` (Background `#0284c7`, text `#ffffff`, font size 18, rounded pill).
  - Y-position: `30px` to `75px`.
- **Columns Layout** (`Y: 95px` to `550px`, Height `455px`):
  - **Left Column (Schedule)**:
    - Box: `X: 35px` to `495px` (Width `460px`).
    - Container: Frosted slate panel (Fill `rgba(26, 36, 56, 0.92)`, border `rgba(56, 189, 248, 0.4)`, radius `12px`).
    - Column Header: `📅 MEETINGS & EVENTS` (Sky-400 `#38bdf8`, font size 18, bold).
    - Event Cards: Up to 3 upcoming events.
      - Time chip: `10:00 AM – 11:00 AM` (Font size 15, `#38bdf8`, bold).
      - Title: `Team Architecture Alignment` (Font size 18, `#ffffff`, bold, truncated if needed).
      - Subtext: `Conference Room A • Google Meet` (Font size 14, `#94a3b8`).
    - Empty State: "Schedule is wide open today. No meetings scheduled." with a calendar-check icon.
  - **Right Column (Priority Tasks)**:
    - Box: `X: 525px` to `985px` (Width `460px`).
    - Container: Frosted slate panel (Fill `rgba(26, 36, 56, 0.92)`, border `rgba(16, 185, 129, 0.4)`, radius `12px`).
    - Column Header: `✓ PENDING TO-DOS ({count})` (Emerald-400 `#34d399`, font size 18, bold).
    - Task Items: Up to 4 pending tasks.
      - Checkbox: `[ ]` (Font size 18, Emerald-400).
      - Summary: `50 push-ups & morning mobility` (Font size 17, `#f1f5f9`).
    - Empty State: "All caught up! No pending to-do tasks."

### 3.2 Google Nest Hub Weather Slide (`slide_renderer.py`)

- **Canvas Size**: 1024 x 600 px.
- **Background**: Weather-aware sky gradient or atmospheric backdrop:
  - Sunny / Clear: `linear-gradient(135deg, #1e3a8a 0%, #0284c7 100%)`
  - Rainy / Pouring: `linear-gradient(135deg, #1e293b 0%, #334155 100%)`
  - Cloudy / Overcast: `linear-gradient(135deg, #1e293b 0%, #0369a1 100%)`
  - Night: `linear-gradient(135deg, #090d16 0%, #1e1b4b 100%)`
- **Location & Condition Header** (`Y: 35px`):
  - Location badge: `Washington, D.C. • Today` (`#ffffff`, opacity 0.85, font size 18, uppercase).
- **Hero Temperature Section** (`Y: 70px` to `260px`):
  - Current Temperature: Massive typography `68°` (Font size 110, `#ffffff`, font-weight light/regular).
  - Condition Text: `Mostly Sunny` (Font size 32, bold, `#ffffff`).
  - High / Low Spread: `High 74° • Low 54°` (Font size 22, `#e2e8f0`).
  - Condition Icon: Big glyph / weather symbol (`🌤️` or vector rendered weather symbol, size ~100px) aligned on the right.
- **Metrics Strip** (`Y: 275px` to `325px`):
  - Floating pill pills (`rgba(255, 255, 255, 0.15)`):
    - `💧 Humidity: 48%`
    - `💨 Wind: 8 mph`
    - `☂️ Precip: 10%`
- **Google Nest Hourly Forecast Timeline Card** (`Y: 345px` to `555px`, Height `210px`):
  - Full-width frosted glass container (`X: 35px` to `985px`, Width `950px`, radius `16px`, background `rgba(15, 23, 42, 0.5)`, border `rgba(255, 255, 255, 0.18)`).
  - 5 to 6 equally spaced forecast columns:
    - Time Label: `Now`, `10 AM`, `1 PM`, `4 PM`, `7 PM`, `Tomorrow` (Font size 16, `#94a3b8`).
    - Mini Weather Icon: `🌤️`, `☀️`, `☀️`, `🌤️`, `⛅`, `☀️` (Font size 28).
    - Temperature: `68°`, `70°`, `73°`, `74°`, `67°`, `72°/52°` (Font size 20, bold, `#ffffff`).

---

## 4. Ingestion & Generator Updates

### 4.1 Weather Service Ingestion (`generator.py`)
In `async_extract_entity_context`:
- Call `hass.services.async_call("weather", "get_forecasts", {"entity_id": eid, "type": "hourly"}, blocking=True, return_response=True)`.
- If unavailable or fails, fall back to `type: "daily"` or state attributes (`state_attr(weather_entity, "forecast")`).
- Extract:
  - Current state and temperature
  - High and low temperature for today
  - Humidity, wind speed, precipitation probability
  - Hourly forecast points (next 5-6 hours with condition and temperature)
- Pass these structured values in `card_payload` or metadata so `slide_renderer.py` renders them faithfully.

### 4.2 Agenda Service Ingestion (`generator.py`)
- Extract calendar events from `calendar.get_events` as list of objects: `[{"summary": ..., "start": ..., "location": ...}]`.
- Extract to-do items from `todo.get_items` as list of strings: `["Task 1", "Task 2"]`.
- Format into `agenda_data` dict:
  ```json
  {
    "calendar_events": [
      {"time": "10:00 AM", "title": "Architecture Alignment", "location": "Room A"}
    ],
    "todo_items": ["50 push-ups", "Prescription refill"],
    "date_str": "Monday, Oct 19"
  }
  ```
- `slide_renderer.py` consumes `agenda_data` directly to draw the two columns.

---

## 5. Backward Compatibility & Testing

- Preserves existing function signatures (`render_slide(slide_data, ...)`).
- When `weather` or `agenda` structured attributes are missing (e.g. in basic mock test cases), graceful fallbacks extract bullet points from existing `card.get("bullets", [])` or state strings without crashing.
- Unit tests to update/add:
  - `test_render_slide_google_nest_weather`: Verifies rendering hero temp, high/low, metrics, and hourly forecast timeline.
  - `test_render_slide_dual_focus_agenda`: Verifies rendering 2-column agenda with time chips and checkboxes.
  - `test_extract_weather_forecasts_hourly`: Verifies `weather.get_forecasts` ingestion in `generator.py`.
- Full test suite passes (236+ tests, 0 ruff errors).
