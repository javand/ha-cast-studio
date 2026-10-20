# Calendar Dual-Focus Agenda & Google Nest Display Weather Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the morning briefing's Calendar/Agenda slide into an authentic 2-column Dual-Focused Agenda board and the Weather slide into a Google Nest Hub display weather layout with live hourly forecast timeline, directly fed by Home Assistant service data with zero Imagen generation delay.

**Architecture:** 
1. In `generator.py`, inject verified Home Assistant service data (`calendar.get_events`, `todo.get_items`, `weather.get_forecasts` hourly and daily) directly into `card_data` so visual cards display 100% verified live data with zero LLM hallucination, and exempt `weather` from dynamic Imagen generation.
2. In `slide_renderer.py`, implement `_draw_google_nest_weather_slide` using bundled transparent weather PNG icons (`assets/weather/`), a condition-aware 135° atmospheric sky gradient, hero temperature, high/low spread, live metric chips, and an hourly forecast timeline.
3. In `slide_renderer.py`, update `_draw_two_column_agenda` to render Option B (Dual-Focused Agenda): title and date badge in header, left column with meeting cards and time chips, right column with priority to-dos and checkboxes.

**Tech Stack:** Python 3.13, Pillow (PIL), Home Assistant Core service APIs (`weather.get_forecasts`, `calendar.get_events`, `todo.get_items`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-19-calendar-weather-visual-redesign.md`

## Global Constraints
- Target branch: `develop` in `/Users/javan/Projects/ha-automation-generator`.
- Resolution: 1024x600 px (Nest Hub standard).
- Backward compatibility: All existing 236 tests in `tests/` must continue to pass; function signatures must remain backward-compatible with optional card attributes.
- Quality gates: `pytest -v` passing, `ruff check .` with 0 errors, `ruff format --check .` with 0 issues.
- Deployed files must be synced to `/Volumes/config/custom_components/cast_studio/`.

## Review Focus
1. Weather service response variations: `weather.get_forecasts` may return dict with entity ID key `{"weather.home": {"forecast": [...]}}` or direct list in older core mocks; code must handle both formats without raising `KeyError` or `TypeError`.
2. Daily-only weather integrations: If an integration only supports daily forecasts (hourly fails or returns empty), code must calculate today's high/low from daily and adapt the timeline smoothly.
3. Empty calendar or tasks: When no calendar events exist or todo list is empty, display clear, friendly empty states ("Schedule is wide open today", "All caught up! No pending tasks") rather than blank panels.
4. Missing weather assets fallback: If a weather icon PNG is missing or fails to load, gracefully fall back to procedural drawing or text glyph without crashing the slide render.
5. Date badge formatting: The agenda header date badge must display the current local date (e.g. `Monday, Oct 19`).

---

### Task 1: Weather Service Ingestion & Imagen Generation Bypass

**Files:**
- Modify: `custom_components/cast_studio/generator.py:120-220, 600-660`
- Modify: `tests/test_generator.py`

**Interfaces:**
- Produces: `async_extract_weather_data(hass: HomeAssistant, eid: str) -> dict[str, Any]` returning `{"current_temp": ..., "condition": ..., "high": ..., "low": ..., "humidity": ..., "wind_speed": ..., "precipitation_probability": ..., "forecast_points": [...]}`.
- Produces: `is_step_image_dynamic(step)` returns `False` for `name == "weather"` (or `type == "weather"`).

- [ ] **Step 1: Write the failing tests in `tests/test_generator.py`**

```python
@pytest.mark.asyncio
async def test_async_extract_weather_data_hourly_and_daily():
    mock_hass = MagicMock()
    mock_hass.states.get.return_value = MagicMock(
        state="partlycloudy",
        attributes={"temperature": 68, "humidity": 48, "wind_speed": 8, "precipitation_probability": 10, "friendly_name": "Home"}
    )
    async def mock_async_call(domain, service, data=None, blocking=True, return_response=False):
        if domain == "weather" and service == "get_forecasts":
            ftype = data.get("type")
            if ftype == "hourly":
                return {"weather.home": {"forecast": [
                    {"datetime": "2026-10-19T20:00:00", "temperature": 68, "condition": "partlycloudy"},
                    {"datetime": "2026-10-19T22:00:00", "temperature": 70, "condition": "sunny"},
                    {"datetime": "2026-10-20T01:00:00", "temperature": 73, "condition": "sunny"},
                    {"datetime": "2026-10-20T04:00:00", "temperature": 74, "condition": "partlycloudy"},
                    {"datetime": "2026-10-20T07:00:00", "temperature": 67, "condition": "cloudy"},
                ]}}
            elif ftype == "daily":
                return {"weather.home": {"forecast": [
                    {"datetime": "2026-10-19", "temperature": 74, "templow": 54, "condition": "partlycloudy"},
                    {"datetime": "2026-10-20", "temperature": 72, "templow": 52, "condition": "sunny"},
                ]}}
        return {}
    mock_hass.services.async_call = AsyncMock(side_effect=mock_async_call)

    weather_data = await async_extract_weather_data(mock_hass, "weather.home")
    assert weather_data["current_temp"] == 68
    assert weather_data["high"] == 74
    assert weather_data["low"] == 54
    assert len(weather_data["forecast_points"]) >= 5
    assert weather_data["forecast_points"][0]["temperature"] == 68

def test_is_step_image_dynamic_exempts_weather():
    assert is_step_image_dynamic({"name": "weather", "visual": True}) is False
    assert is_step_image_dynamic({"name": "fun_fact", "visual": True}) is True
    assert is_step_image_dynamic({"name": "scripture", "visual": True}) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_generator.py -k "extract_weather_data or exempts_weather" -v`  
Expected: FAIL with `NameError: name 'async_extract_weather_data' is not defined` or assertion failure.

- [ ] **Step 3: Implement `async_extract_weather_data` and exempt `weather` in `custom_components/cast_studio/generator.py`**

- In `is_step_image_dynamic(step)`: add `weather` to static/templated steps that skip dynamic Gemini Imagen generation.
- Implement `async_extract_weather_data(hass: HomeAssistant, eid: str) -> dict[str, Any]` querying `weather.get_forecasts` for `hourly` and `daily`, with graceful min/max fallback.
- In `async_extract_entity_context`: when processing `weather.*`, call `async_extract_weather_data` and store the result in `step_to_process.setdefault("card", {})["weather_data"] = weather_data`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_generator.py -k "extract_weather_data or exempts_weather" -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/cast_studio/generator.py tests/test_generator.py
git commit -m "feat(generator): add async_extract_weather_data and exempt weather from Imagen generation"
```

---

### Task 2: Weather PNG Icons & Google Nest Hub Weather Slide Renderer

**Files:**
- Create: `custom_components/cast_studio/assets/weather/sunny.png`
- Create: `custom_components/cast_studio/assets/weather/partly_cloudy.png`
- Create: `custom_components/cast_studio/assets/weather/cloudy.png`
- Create: `custom_components/cast_studio/assets/weather/rainy.png`
- Create: `custom_components/cast_studio/assets/weather/stormy.png`
- Create: `custom_components/cast_studio/assets/weather/snowy.png`
- Create: `custom_components/cast_studio/assets/weather/humidity.png`
- Create: `custom_components/cast_studio/assets/weather/wind.png`
- Create: `custom_components/cast_studio/assets/weather/precip.png`
- Modify: `custom_components/cast_studio/slide_renderer.py:200-350`
- Modify: `tests/test_slide_renderer.py`

**Interfaces:**
- Produces: `_draw_google_nest_weather_slide(canvas: Image.Image, card: dict[str, Any], theme: SlideTheme) -> None`.
- Produces: `load_weather_icon(condition: str, size: tuple[int, int]) -> Image.Image`.

- [ ] **Step 1: Create crisp weather icon PNG assets in `custom_components/cast_studio/assets/weather/`**

Generate or procedurally build clean 128x128 RGBA transparent weather icon assets for standard conditions (`sunny`, `partly_cloudy`, `cloudy`, `rainy`, `stormy`, `snowy`, `humidity`, `wind`, `precip`).

- [ ] **Step 2: Write failing tests in `tests/test_slide_renderer.py`**

```python
def test_render_slide_google_nest_weather_layout(tmp_path):
    output_png = str(tmp_path / "slide_weather.png")
    slide_data = {
        "step_name": "weather",
        "card": {
            "title": "Weather",
            "weather_data": {
                "current_temp": 68,
                "condition": "partlycloudy",
                "condition_text": "Mostly Sunny",
                "high": 74,
                "low": 54,
                "humidity": 48,
                "wind_speed": 8,
                "precipitation_probability": 10,
                "location": "Washington, D.C.",
                "forecast_points": [
                    {"time": "Now", "temperature": 68, "condition": "partlycloudy"},
                    {"time": "10 AM", "temperature": 70, "condition": "sunny"},
                    {"time": "1 PM", "temperature": 73, "condition": "sunny"},
                    {"time": "4 PM", "temperature": 74, "condition": "partlycloudy"},
                    {"time": "7 PM", "temperature": 67, "condition": "cloudy"},
                    {"time": "Tomorrow", "temperature": "72° / 52°", "condition": "sunny"},
                ],
            },
        },
    }
    render_slide(slide_data, output_png)
    assert Path(output_png).is_file()
    with Image.open(output_png) as img:
        assert img.size == (1024, 600)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_slide_renderer.py -k "google_nest_weather_layout" -v`  
Expected: FAIL.

- [ ] **Step 4: Implement `_draw_google_nest_weather_slide` in `custom_components/cast_studio/slide_renderer.py`**

- Condition-aware atmospheric 135° linear gradient base canvas (`linear-gradient(135deg, #1e3a8a 0%, #0369a1 50%, #0284c7 100%)`).
- Header: Location badge `Washington, D.C. • Today`.
- Hero row: Large 110px current temperature (`68°`), condition text (`Mostly Sunny`), High/Low spread (`High 74° • Low 54°`), weather icon asset on right.
- Metrics chips: `💧 Humidity: 48%`, `💨 Wind: 8 mph`, `☂️ Precip: 10%`.
- Hourly forecast timeline card: frosted container (`#0f172a` 50% opacity, rounded corners) with 6 columns showing time label, weather icon, and temperature.
- Dispatch to `_draw_google_nest_weather_slide` when `step_name == "weather"` and `weather_data` is present in `card`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_slide_renderer.py -k "google_nest_weather_layout" -v`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add custom_components/cast_studio/assets/weather/ custom_components/cast_studio/slide_renderer.py tests/test_slide_renderer.py
git commit -m "feat(slide_renderer): add Google Nest Hub weather layout with atmospheric gradient and forecast timeline"
```

---

### Task 3: Dual-Focused Agenda Slide Ingestion & Renderer (Option B)

**Files:**
- Modify: `custom_components/cast_studio/generator.py:160-210`
- Modify: `custom_components/cast_studio/slide_renderer.py:750-890`
- Modify: `tests/test_generator.py`
- Modify: `tests/test_slide_renderer.py`

**Interfaces:**
- Produces: `card["agenda_data"] = {"calendar_events": [...], "todo_items": [...], "date_str": "Monday, Oct 19"}` in `generator.py`.
- Produces: `_draw_two_column_agenda(canvas, card, theme)` rendering Option B dual board.

- [ ] **Step 1: Write failing tests in `tests/test_generator.py` and `tests/test_slide_renderer.py`**

```python
def test_render_slide_dual_focus_agenda_layout(tmp_path):
    output_png = str(tmp_path / "slide_agenda.png")
    slide_data = {
        "step_name": "agenda",
        "card": {
            "title": "Today's Schedule & Tasks",
            "agenda_data": {
                "date_str": "Monday, Oct 19",
                "calendar_events": [
                    {"time": "10:00 AM – 11:00 AM", "title": "Team Architecture Alignment", "location": "Room A"},
                    {"time": "2:30 PM – 3:30 PM", "title": "Weekly Project Review", "location": "Google Meet"},
                ],
                "todo_items": [
                    "50 push-ups & morning mobility",
                    "Prescription refill reminder",
                    "Review pull request",
                ],
            },
        },
    }
    render_slide(slide_data, output_png)
    assert Path(output_png).is_file()
    with Image.open(output_png) as img:
        assert img.size == (1024, 600)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_slide_renderer.py -k "dual_focus_agenda_layout" -v`  
Expected: FAIL.

- [ ] **Step 3: Implement structured `agenda_data` in `generator.py` and Dual-Focus Agenda in `slide_renderer.py`**

- In `generator.py` (`async_extract_entity_context`):
  - When processing `calendar.*` or `todo.*`, format events into `{"time": "...", "title": "...", "location": "..."}` and tasks into strings.
  - Set `step_to_process.setdefault("card", {})["agenda_data"] = {"date_str": today_str, "calendar_events": events, "todo_items": tasks}`.
- In `slide_renderer.py` (`_draw_two_column_agenda`):
  - Draw header: Left `TODAY'S SCHEDULE & TASKS`, Right date badge `Monday, Oct 19`.
  - Draw left container: `📅 MEETINGS & EVENTS`, event cards with time chips (`[ 10:00 AM ]`), bold event titles, location subtext.
  - Draw right container: `✓ PENDING TO-DOS ({count})`, checkboxes `[ ]`, task summaries.
  - Provide fallback when `agenda_data` is missing (using `card.get("calendar_items")` and `card.get("todo_items")` or legacy bullets).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_generator.py tests/test_slide_renderer.py -k "agenda" -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/cast_studio/generator.py custom_components/cast_studio/slide_renderer.py tests/test_generator.py tests/test_slide_renderer.py
git commit -m "feat(slide_renderer): implement Option B dual-focused agenda slide with live HA events and tasks"
```

---

### Task 4: End-to-End Verification, Volume Sync & Branch Completion

**Files:**
- Modify: `custom_components/cast_studio/__init__.py` (if imports or wiring needed)
- Sync to: `/Volumes/config/custom_components/cast_studio/`

- [ ] **Step 1: Run full test suite and linters**

Run: `pytest -v`  
Run: `ruff check .`  
Run: `ruff format --check .`  
Expected: All tests pass (238+ tests), 0 ruff errors.

- [ ] **Step 2: Sync updated components and assets to Home Assistant volume**

```bash
cp -r custom_components/cast_studio/* /Volumes/config/custom_components/cast_studio/
```

- [ ] **Step 3: Commit and push changes to `develop`**

```bash
git add custom_components/ tests/ docs/
git commit -m "feat(cast_studio): complete calendar dual-focus agenda and google nest weather redesign"
git push origin develop
```
