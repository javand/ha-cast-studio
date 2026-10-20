# Geographically Accurate Commute Map & Daily Encouragement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static commute graphic with a live, geographically accurate Google Maps route overview comparing driving vs transit (defaulting to the fastest travel time), and add a warm, punchy post-scripture daily encouragement slide powered by a daily dynamic Gemini Imagen backdrop.

**Architecture:** 
1. In `generator.py`, query Google Directions & Static Maps API (using the user's `google_maps_api_key`) to fetch the authentic geographic overview polyline and map image between `zone.home` and the configured work zone. Ingest and compare live travel times from Home Assistant sensors (`sensor.javan_commute_drive_to_work` and `sensor.javan_commute_metro`) into structured `card["commute_data"]`.
2. In `slide_renderer.py`, implement the dark-mode Google Nest Hub commute layout (large ETA hero card, arrival time, and dual Driving vs Subway comparison chips over the live map).
3. In `__init__.py` and `generator.py`, define the new `daily_encouragement` step immediately following `scripture`, add it to the dynamic Imagen allowlist (`fun_fact` and `daily_encouragement`), and synthesize practical, grounded daily life applications without religious jargon.
4. In `slide_renderer.py`, render the minimalist frosted glass encouragement card (`☀️ TODAY'S TAKEAWAY`) over the dynamic sunrise backdrop.

**Tech Stack:** Python 3.13+, Home Assistant Custom Component (`cast_studio`), Pillow (PIL Image/ImageDraw/ImageFont), aiohttp, Google Maps Directions & Static Maps API, Gemini Imagen / Gemini TTS.

**Spec:** [docs/superpowers/specs/2026-10-20-commute-map-and-daily-encouragement-design.md](file:///Users/javan/Projects/ha-automation-generator/docs/superpowers/specs/2026-10-20-commute-map-and-daily-encouragement-design.md)

## Global Constraints
- **Canvas Resolution:** Exactly 1024×600 px (Google Nest Hub standard widescreen).
- **Target Branch:** `develop` in `/Users/javan/Projects/ha-automation-generator`.
- **Backward Compatibility:** All existing 256 tests in `tests/` must continue to pass; function signatures remain backward-compatible with optional parameters.
- **Dynamic Imagen Allowlist:** Only `fun_fact` and `daily_encouragement` trigger dynamic Imagen generation; all other slides (`greeting`, `weather`, `agenda`, `commute`, `scripture`) use instant local renderers or cached assets.
- **Data Authority:** Live travel times, differences, and ETAs are authoritatively sourced from Home Assistant sensors (`sensor.javan_commute_drive_to_work`, `sensor.javan_commute_metro`). Google Directions API is queried only once per routine compilation (for the selected mode) to obtain the overview polyline.
- **Copy Constraints:** Strictly avoid religious or preachy jargon (e.g. omitting "blessed") on the daily encouragement slide; maintain a grounded coaching tone ending with `"Have a great day, {recipient_name}!"`.
- **Quality Gates:** `pytest -v` passing, `ruff check .` with 0 errors, `ruff format --check .` with 0 issues.
- **Deployment Sync:** Sync updated components to `/Volumes/config/custom_components/cast_studio/`.

## Review Focus
1. **API Key Absence / Network Failure Fallback:** If `google_maps_api_key` is not configured, or if the Google Directions / Static Maps API call fails or times out, the commute slide must gracefully fall back to a clean procedural dark map or bundled asset without throwing an unhandled exception or breaking routine compilation.
2. **Single-Mode Commute Handling:** If Home Assistant has only a driving sensor and no transit sensor (or vice versa), the commute layout and TTS narration must cleanly adapt to a single-mode card without broken placeholders, `None` errors, or comparison chips.
3. **Dynamic Imagen Denylist Matrix Migration:** Migrating `is_step_image_dynamic()` from a denylist to an allowlist must preserve existing behavior for `fun_fact` while adding `daily_encouragement`, ensuring `weather`, `scripture`, `commute`, `greeting`, and `agenda` remain exempt.
4. **Timezone-Aware ETA Formatting:** Arrival times (`eta`) must be formatted in Home Assistant's local timezone using `dt_util.now()` or `dt_util.as_local()`.
5. **Card Text Truncation & Boundary Safety:** On both the commute HUD and encouragement card, font sizes and line heights must prevent any text clipping or overflow beyond the 1024×600 canvas boundary.

---

### Task 1: Commute Map Fetcher, Google Directions / Static Maps Ingestion & Multi-Sensor Comparison

**Files:**
- Modify: `custom_components/cast_studio/const.py:10-50`
- Modify: `custom_components/cast_studio/config_flow.py:20-60`
- Modify: `custom_components/cast_studio/generator.py:120-260, 1100-1160`
- Modify: `custom_components/cast_studio/services.yaml:20-80`
- Test: `tests/test_generator.py`

**Interfaces:**
- Consumes: `hass.states`, `hass.config`, `secrets.yaml` (if present), `entry.options`
- Produces:
  - `CONF_DEFAULT_TRANSPORTATION_MODE = "default_transportation_mode"`
  - `CONF_MAPS_API_KEY = "maps_api_key"`
  - `async_resolve_google_maps_api_key(hass: HomeAssistant) -> str | None`
  - `async_fetch_commute_map(hass: HomeAssistant, origin: tuple[float, float], destination: tuple[float, float], mode: str, api_key: str, output_dir: str) -> dict[str, Any] | None`
  - `async_extract_commute_data(hass: HomeAssistant, step: dict[str, Any], mode_override: str | None = None) -> dict[str, Any]`
  - Attaches `step_to_process.setdefault("card", {})["commute_data"] = commute_data`

- [x] **Step 1: Write the failing tests**

```python
# In tests/test_generator.py
async def test_extract_commute_data_multi_sensor_comparison(hass: HomeAssistant, tmp_path: Path):
    """Test multi-sensor commute extraction comparing driving and transit."""
    hass.states.async_set(
        "sensor.javan_commute_drive_to_work",
        "35",
        {"destination": "zone.work", "mode": "driving", "unit_of_measurement": "min"},
    )
    hass.states.async_set(
        "sensor.javan_commute_metro",
        "44",
        {"line": "Silver Line", "unit_of_measurement": "min"},
    )
    step = {"name": "commute", "type": "commute", "commute_sensors": ["sensor.javan_commute_drive_to_work", "sensor.javan_commute_metro"]}
    
    with patch("custom_components/cast_studio.generator.async_fetch_commute_map", return_value={"summary": "I-66 W", "distance": "24.5 mi", "map_image_path": str(tmp_path / "map.png")}):
        data = await async_extract_commute_data(hass, step, mode_override="fastest")
    
    assert data["selected_mode"] == "driving"
    assert data["drive_time"] == 35
    assert data["transit_time"] == 44
    assert data["diff_minutes"] == 9
    assert data["faster_mode"] == "driving"
    assert data["drive_summary"] == "I-66 W"
    assert data["transit_summary"] == "Silver Line"

async def test_extract_commute_data_single_mode_fallback(hass: HomeAssistant):
    """Test commute extraction when only driving sensor exists."""
    hass.states.async_set("sensor.javan_commute_drive_to_work", "28", {"unit_of_measurement": "min"})
    step = {"name": "commute", "type": "commute", "commute_sensors": ["sensor.javan_commute_drive_to_work"]}
    
    data = await async_extract_commute_data(hass, step)
    assert data["selected_mode"] == "driving"
    assert data["drive_time"] == 28
    assert data["transit_time"] is None
    assert data["diff_minutes"] is None
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_generator.py -k "commute_data" -v`
Expected: FAIL with `NameError: name 'async_extract_commute_data' is not defined`

- [x] **Step 3: Implement commute extraction and Google Maps fetcher in `generator.py`**

- In `custom_components/cast_studio/const.py`: Define `CONF_DEFAULT_TRANSPORTATION_MODE = "default_transportation_mode"` and `CONF_MAPS_API_KEY = "maps_api_key"`.
- In `custom_components/cast_studio/generator.py`:
  - Implement `async_resolve_google_maps_api_key(hass: HomeAssistant) -> str | None`: Check `entry.options`, `entry.data`, inspect `secrets.yaml` via `hass.config.path("secrets.yaml")`, and `hass.data` entries for Google Maps.
  - Implement `async_fetch_commute_map`: Query Google Directions API with `aiohttp` for the selected mode, parse `overview_polyline.points`, `summary`, and `distance.text`. Then query Google Static Maps API in dark mode, saving the image to `{output_dir}/cache/commute_map_{mode}_{date}.png`. Handle network exceptions and timeouts with fallback to `None`.
  - Implement `async_extract_commute_data`: Discovers drive vs transit sensors via heuristics (`drive|waze|car` vs `metro|transit|subway|train`), calculates `drive_time`, `transit_time`, `diff_minutes`, local arrival `eta`, and attaches `card["commute_data"]`.
  - In `_async_compile_single_step`: Forward `commute_data` from `step_to_process["card"]["commute_data"]` into `card_data["commute_data"]` to ensure live HA and map data overrides any LLM dummy values.
- In `custom_components/cast_studio/services.yaml`: Add optional parameter `transportation_mode` to `run_routine` and `pregenerate_routine`.

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_generator.py -k "commute_data" -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add custom_components/cast_studio/const.py custom_components/cast_studio/generator.py custom_components/cast_studio/config_flow.py custom_components/cast_studio/services.yaml tests/test_generator.py
git commit -m "feat(commute): add Google Maps route fetcher and multi-sensor commute ingestion"
```

---

### Task 2: Google Nest Hub Commute Slide Renderer

**Files:**
- Modify: `custom_components/cast_studio/slide_renderer.py:50-200, 700-850`
- Test: `tests/test_slide_renderer.py`

**Interfaces:**
- Consumes: `card.get("commute_data")` produced by Task 1
- Produces: `_draw_google_nest_commute_slide(canvas: Image.Image, card: dict[str, Any], theme: SlideTheme) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# In tests/test_slide_renderer.py
def test_render_slide_google_nest_commute_layout(tmp_path: Path):
    """Test rendering Google Nest Hub commute layout with live map and comparison HUD."""
    dummy_map = tmp_path / "map.png"
    Image.new("RGB", (1024, 600), (20, 25, 35)).save(dummy_map)
    
    card = {
        "title": "Commute to Work",
        "subtitle": "Traffic Overview",
        "commute_data": {
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
            "map_image_path": str(dummy_map),
        },
    }
    img = render_slide(card, step_name="commute")
    assert img.size == (1024, 600)
    # Verify image rendered without crashing

def test_render_slide_google_nest_commute_single_mode_fallback():
    """Test commute rendering when transit comparison is absent."""
    card = {
        "title": "Commute to Work",
        "commute_data": {
            "selected_mode": "driving",
            "drive_time": 30,
            "transit_time": None,
            "diff_minutes": None,
            "faster_mode": "driving",
            "summary": "via I-66 W",
            "distance": "20.0 mi",
            "eta": "8:30 AM",
        },
    }
    img = render_slide(card, step_name="commute")
    assert img.size == (1024, 600)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_slide_renderer.py -k "google_nest_commute" -v`
Expected: FAIL (rendering defaults to legacy slide instead of Nest Hub commute layout)

- [ ] **Step 3: Implement `_draw_google_nest_commute_slide` in `slide_renderer.py`**

- If `card.get("commute_data")` has `map_image_path` and file exists, composite the full-bleed 1024×600 map onto the canvas; otherwise render procedural dark grid with route styling.
- Draw subtle dark gradient vignette (`rgba(15, 23, 42, 0.45)`).
- Top Header (`Y: 25`, `X: 30`): Pill badge `🚗 COMMUTE TO WORK` (`#0284c7`, font 13 bold) and subtitle `Fastest route via I-66 W • Typical traffic` (Slate-300, font 14).
- Hero ETA HUD Card (`Y: 68`, `X: 30`, width 340, radius 16, fill `rgba(15, 23, 42, 0.88)`, border `rgba(255,255,255,0.14)`):
  - Large 46px Emerald-400 (`#10b981`) duration `35 min`.
  - Faster Badge `FASTER BY {diff_min} MIN` (if `diff_minutes` is present).
  - ETA Row: `Arrive around {eta} • {distance}` (`#94a3b8`, font 14).
- Mode Comparison Chips (`Bottom Left`, `Y: 515`, `X: 30`):
  - Active Driving Chip: `rgba(2, 132, 199, 0.28)`, border 2px `#38bdf8`, 🚗 icon, `35 min • {drive_summary}`.
  - Metro Chip (if `transit_time` present): `rgba(15, 23, 42, 0.78)`, border 1px `rgba(255, 255, 255, 0.12)`, 🚇 icon, `44 min • {transit_summary}`.
- In `render_slide()`: if `step_name == "commute"` and `card.get("commute_data")` is present, call `_draw_google_nest_commute_slide` and return early.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_slide_renderer.py -k "google_nest_commute" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/cast_studio/slide_renderer.py tests/test_slide_renderer.py
git commit -m "feat(commute): implement Google Nest Hub commute layout with live map and HUD"
```

---

### Task 3: Weather Actionable Advice, Daily Encouragement Step & Dynamic Imagen Allowlist

**Files:**
- Modify: `custom_components/cast_studio/__init__.py:230-310, 480-530`
- Modify: `custom_components/cast_studio/generator.py:110-180, 200-260, 400-480`
- Modify: `custom_components/cast_studio/ai_client.py:25-45, 100-180`
- Test: `tests/test_generator.py`
- Test: `tests/test_services.py`

**Interfaces:**
- Consumes: `scripture_sensor`, `weather.*` entity in Home Assistant
- Produces:
  - `weather_data["advisory"]` computed in `async_extract_weather_data` (e.g. `"Pack an umbrella for rain today"`, `"Wear a warm coat today, it's chilly"`, `"Grab a light jacket"`, `"Dress light & stay hydrated"`, or air quality warning).
  - Updated `STEP_PROMPT_GUIDELINES["weather"]` instructing Gemini LLM to give practical preparation advice.
  - Step `daily_encouragement` inserted immediately after `scripture` in default morning routine config.
  - `is_step_image_dynamic(step)` returns `True` for `fun_fact` and `daily_encouragement`, `False` for all others.
  - Prompt generator for `daily_encouragement`: warm, punchy, grounded everyday application without religious jargon ("blessed").

- [ ] **Step 1: Write the failing tests**

```python
# In tests/test_generator.py
def test_is_step_image_dynamic_allowlist():
    """Verify dynamic Imagen generation is restricted to fun_fact and daily_encouragement."""
    assert is_step_image_dynamic({"name": "fun_fact"}) is True
    assert is_step_image_dynamic({"name": "daily_encouragement"}) is True
    assert is_step_image_dynamic({"name": "greeting"}) is False
    assert is_step_image_dynamic({"name": "weather"}) is False
    assert is_step_image_dynamic({"name": "agenda"}) is False
    assert is_step_image_dynamic({"name": "commute"}) is False
    assert is_step_image_dynamic({"name": "scripture"}) is False

async def test_extract_weather_data_actionable_advisory(hass: HomeAssistant):
    """Verify weather data extracts actionable preparation advisory (e.g. umbrella, coat)."""
    hass.states.async_set("weather.home", "rainy", {"temperature": 58, "precipitation_probability": 80})
    with patch.object(hass.services, "async_call", return_value={"weather.home": {"forecast": []}}):
        data = await async_extract_weather_data(hass, "weather.home")
    assert "umbrella" in data["advisory"].lower()

# In tests/test_services.py
def test_default_morning_routine_includes_daily_encouragement(hass: HomeAssistant):
    """Verify default morning routine orders daily_encouragement immediately after scripture."""
    config = _build_default_morning_routine_config(hass, {"name": "Javan"})
    step_names = [s["name"] for s in config["steps"]]
    assert "scripture" in step_names
    assert "daily_encouragement" in step_names
    scripture_idx = step_names.index("scripture")
    encouragement_idx = step_names.index("daily_encouragement")
    assert encouragement_idx == scripture_idx + 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_generator.py -k "actionable_advisory or is_step_image_dynamic_allowlist" tests/test_services.py -k "includes_daily_encouragement" -v`
Expected: FAIL with assertion failures (advisory missing or allowlist not matching)

- [ ] **Step 3: Implement weather advisory, `daily_encouragement` step and allowlist in `generator.py`, `ai_client.py`, and `__init__.py`**

- In `custom_components/cast_studio/generator.py`:
  - In `async_extract_weather_data`: compute `advisory` based on precipitation probability, temperature thresholds, condition keywords (rain, chilly, hot), and AQI, storing it in `weather_data["advisory"]`.
  - In `_async_compile_single_step`: ensure `weather_data["advisory"]` is preserved into `card_data["weather_data"]["advisory"]`.
  - Update `is_step_image_dynamic(step: dict[str, Any]) -> bool`:
    ```python
    name = (step.get("name") or step.get("type") or "").lower()
    return name in ("fun_fact", "daily_encouragement")
    ```
- In `custom_components/cast_studio/ai_client.py`:
  - Update `STEP_PROMPT_GUIDELINES["weather"]` to instruct Gemini LLM to include practical daily preparation recommendations (e.g. umbrella, jacket, light clothes, air quality precautions).
- In `custom_components/cast_studio/__init__.py` (`_build_default_morning_routine_config`):
  - Insert the `daily_encouragement` step immediately following `scripture`:
    ```python
    {
        "name": "daily_encouragement",
        "type": "daily_encouragement",
        "visual": True,
        "title": "Today's Takeaway",
        "subtitle": "Daily Encouragement",
        "entity": scripture_sensor,
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
- In `tests/test_services.py`: Update expected executable steps count (from 6 to 7 when scripture exists).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_generator.py -k "actionable_advisory or is_step_image_dynamic_allowlist" tests/test_services.py -k "includes_daily_encouragement" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/cast_studio/__init__.py custom_components/cast_studio/generator.py custom_components/cast_studio/ai_client.py tests/test_generator.py tests/test_services.py
git commit -m "feat(weather,encouragement): add weather preparation advisory, daily_encouragement step and Imagen allowlist"
```

```bash
git add custom_components/cast_studio/__init__.py custom_components/cast_studio/generator.py custom_components/cast_studio/ai_client.py tests/test_generator.py tests/test_services.py
git commit -m "feat(encouragement): define daily_encouragement step and configure dynamic Imagen allowlist"
```

---

### Task 4: Weather Advisory Chip & Daily Encouragement Slide Renderer

**Files:**
- Modify: `custom_components/cast_studio/slide_renderer.py:200-350, 600-700, 850-950`
- Test: `tests/test_slide_renderer.py`

**Interfaces:**
- Consumes: `card["weather_data"]["advisory"]` and `card` for `daily_encouragement` step
- Produces:
  - Weather advisory pill chip rendered in `_draw_google_nest_weather_slide`
  - `_draw_daily_encouragement_slide(canvas: Image.Image, card: dict[str, Any], theme: SlideTheme) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# In tests/test_slide_renderer.py
def test_render_slide_weather_advisory_chip():
    """Test rendering Google Nest Hub weather slide with actionable preparation advisory chip."""
    card = {
        "title": "Weather",
        "weather_data": {
            "current_temp": 52,
            "condition": "rainy",
            "high": 60,
            "low": 48,
            "humidity": 85,
            "wind_speed": 12,
            "precipitation_probability": 90,
            "advisory": "Pack an umbrella for rain today",
            "forecast_points": [{"time_label": "Now", "condition": "rainy", "temp": 52}],
        },
    }
    img = render_slide(card, step_name="weather")
    assert img.size == (1024, 600)

def test_render_slide_daily_encouragement_layout():
    """Test rendering daily encouragement slide with clean frosted card and takeaways."""
    card = {
        "title": "Focus on what you can control, and take the day one step at a time.",
        "bullets": [
            "Keep your perspective: Don't let morning delays derail your peace.",
            "Prioritize with care: Focus your energy on the people and tasks that matter most today.",
        ],
        "host_signoff": "Have a great day, Javan!",
    }
    img = render_slide(card, step_name="daily_encouragement")
    assert img.size == (1024, 600)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_slide_renderer.py -k "weather_advisory or daily_encouragement" -v`
Expected: FAIL (advisory chip not drawn or encouragement routing missing)

- [ ] **Step 3: Implement weather advisory chip and `_draw_daily_encouragement_slide` in `slide_renderer.py`**

- In `_draw_google_nest_weather_slide`:
  - If `weather_data.get("advisory")` is present, render an advisory pill chip in the metrics row (`Y: 275` to `325`) or hero section (`rgba(2, 132, 199, 0.35)` background, `#38bdf8` border, bold white 14px text: e.g. `☂️ Pack an umbrella today`).
- In `_draw_daily_encouragement_slide`:
  - Canvas (1024×600): Composite over dynamic Imagen sunrise backdrop (or atmospheric dawn gradient fallback).
  - Frosted Card (`Inset: 40px`, width 944, height 520, radius 18, fill `rgba(15, 23, 42, 0.75)`, border `rgba(255, 255, 255, 0.18)`).
  - Category Pill (`Y: 30`): `☀️ TODAY'S TAKEAWAY` (`rgba(245, 158, 11, 0.25)` fill, `#f59e0b` border, `#fde68a` font 13 bold).
  - Hero Insight Headline (`Y: 75`): 32-34px bold `#ffffff` font, wrapping cleanly up to 2 lines.
  - Actionable Bullet Takeaways (`Y: 180` to `380`):
    - Exactly 2 clean bullet rows with Sky-400 / Emerald-400 bullet markers, 18px text (`#e2e8f0`).
  - Host Sign-Off (`Bottom`, `Y: 460`):
    - Divider line (`rgba(255, 255, 255, 0.12)`).
    - Left text: `"{host_signoff}"` (Amber-200 `#fde68a`, font 16 bold).
    - Right text: `CastStudio Morning Host` (Slate-400 `#94a3b8`, font 13).
- In `render_slide()`: if `step_name in ("daily_encouragement", "encouragement")`, route to `_draw_daily_encouragement_slide`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_slide_renderer.py -k "weather_advisory or daily_encouragement" -v`
Expected: PASS

- [ ] **Step 5: Run full test suite & linters**

Run:
```bash
pytest -v
ruff check .
ruff format --check .
```
Expected: All 256+ tests passing, 0 Ruff errors.

- [ ] **Step 6: Commit**

```bash
git add custom_components/cast_studio/slide_renderer.py tests/test_slide_renderer.py
git commit -m "feat(encouragement): implement clean daily encouragement slide renderer"
```

---

### Task 5: Production Deployment & Verification Sync

**Files:**
- Sync: `/Volumes/config/custom_components/cast_studio/`
- Documentation: `README.md`, `SPEC.md`

- [ ] **Step 1: Sync to Home Assistant mount**

Run:
```bash
rsync -av --delete --exclude='__pycache__' custom_components/cast_studio/ /Volumes/config/custom_components/cast_studio/
```

- [ ] **Step 2: Update documentation**

Document the new `daily_encouragement` step and Google Maps commute configuration in `README.md` and `SPEC.md`.

- [ ] **Step 3: Commit**

```bash
git add README.md SPEC.md
git commit -m "docs: document Google Maps commute and daily encouragement features"
```
