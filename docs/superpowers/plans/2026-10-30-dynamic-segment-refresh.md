# Dynamic Segment Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `cast_studio.refresh`, which rebuilds only a routine's (or person's) weather and commute segments; stop rebuilding commute at play time; let play wait up to 10 s for a running refresh; log how long every build takes.

**Architecture:** `briefing.py` gets one constant for the dynamic types, a shared build-and-store helper used by both `async_prebuild` and a new `async_refresh`, and a per-person "refresh running" marker that `async_play` checks before taking the person lock with a timeout. `segments.async_build_segments` gains a `source` label (for timing logs) and an optional commute-sensor timeout that it passes down to `generator.py` through the step dict.

**Tech Stack:** Python 3.13, Home Assistant custom integration, voluptuous, pytest + pytest-homeassistant-custom-component (`asyncio_mode = auto`), ruff.

**Spec:** `docs/superpowers/specs/2026-10-30-dynamic-segment-refresh-design.md`

## Global Constraints

- Dynamic segment types: exactly `weather` and `commute`, defined in one constant.
- Play waits for a running refresh at most **10 seconds** (`REFRESH_WAIT_SECONDS = 10.0`, one named constant).
- During a refresh, the commute-sensor `update_entity` cap is **5 seconds** (`REFRESH_SENSOR_TIMEOUT = 5`). Prebuild keeps the existing 15 s cap (`SENSOR_REFRESH_TIMEOUT`).
- A refresh never rebuilds non-dynamic segments and always rebuilds the dynamic ones, even if they are fresh.
- Each dynamic segment stands alone: if one fails, the other is kept and the failed one keeps its stored copy.
- Play never builds a dynamic segment. If one has no stored copy, it is skipped with the existing "Skipping segment" warning. Missing non-dynamic segments are still built live.
- Timing logs are at INFO level.
- No heads-up line, no weather wording changes, no significance check.
- Action name `refresh`, display name "Refresh dynamic segments". `strings.json` and `translations/en.json` stay identical.
- Run tests with `python3 -m pytest`, and lint with `scripts/lint` before each commit.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A refresh lands while the same routine is streaming.** The streamed file must not be overwritten, and the sensor must stay `playing`. Test in Task 4: `test_refresh_during_playback_keeps_streamed_file_and_playing_state`.
2. **Early false wake, then the real one** (6:28 then 8:32). The second refresh must rebuild again, not skip because the first copy is fresh. Test in Task 4: `test_second_refresh_rebuilds_fresh_dynamic_segments`.
3. **A routine-scoped refresh shares its fresh weather with another routine.** That routine's pre-stitched video no longer matches, so its next play must re-stitch with the fresh weather instead of playing the old video. Test in Task 4: `test_routine_refresh_makes_other_visual_routine_restitch`.
4. **Play gives up waiting and no prebuild ever ran.** The dynamic segments must be skipped and the rest played. Play must not start its own weather or commute build at the same time as the refresh. Test in Task 5: `test_play_after_wait_timeout_skips_unbuilt_dynamic_segments`.
5. **A refresh where every dynamic segment fails.** It raises, the 6 AM copies still play, and the sensor stays `ready`, not `error`. Test in Task 4: `test_refresh_all_failed_raises_and_keeps_prebuild_copies`.

---

### Task 1: Configurable commute-sensor refresh cap

**Files:**
- Modify: `custom_components/cast_studio/generator.py:141-142` (constant) and `:661-680` (`_async_refresh_sensors`), call site at `:788`
- Test: `tests/test_generator.py` (next to `test_extract_commute_data_refreshes_person_sensors_first`, ~line 3327)

**Interfaces:**
- Produces: the step key `"sensor_refresh_timeout"` (float seconds). If present, `async_extract_commute_data` uses it in place of `SENSOR_REFRESH_TIMEOUT`. Task 2 sets it.
- Produces: `_async_refresh_sensors(hass, entity_ids, *, timeout: float = SENSOR_REFRESH_TIMEOUT) -> None`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_extract_commute_data_uses_step_sensor_refresh_timeout(hass):
    _set_both_commute_sensors(hass)

    async def _slow_update(call: ServiceCall) -> None:
        await asyncio.sleep(5)

    hass.services.async_register("homeassistant", "update_entity", _slow_update)
    step = {**_standalone_commute_step(), "sensor_refresh_timeout": 0.05}
    started = time.monotonic()
    data = await async_extract_commute_data(hass, step)

    assert time.monotonic() - started < 1
    assert data["drive_time"] == 35  # last reading is used
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python3 -m pytest tests/test_generator.py::test_extract_commute_data_uses_step_sensor_refresh_timeout -v`
Expected: FAIL. It takes about 5 s or more, because the step key is ignored.

- [ ] **Step 3: Implement**

Add the `timeout` keyword to `_async_refresh_sensors` (use it in `asyncio.timeout`). At the call site, pass `timeout=float(step.get("sensor_refresh_timeout") or SENSOR_REFRESH_TIMEOUT)`.

- [ ] **Step 4: Run the generator tests and confirm they pass**

Run: `python3 -m pytest tests/test_generator.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
scripts/lint
git add custom_components/cast_studio/generator.py tests/test_generator.py
git commit -m "feat(commute): per-step cap on the commute sensor refresh"
```

---

### Task 2: Build timing logs and build options in `async_build_segments`

**Files:**
- Modify: `custom_components/cast_studio/segments.py:653-720` (`async_build_segments`)
- Test: `tests/test_segments.py` (next to `test_build_segments_one_failure_does_not_stop_others`, ~line 484)

**Interfaces:**
- Consumes: the step key `"sensor_refresh_timeout"` from Task 1.
- Produces: the new keyword-only parameters below. Existing callers keep working through the defaults.
  ```python
  async def async_build_segments(
      hass, store, person, keys, ai_creds, *, now, visual_keys=(), all_heard_visual=False,
      source: str = "prebuild",
      sensor_refresh_timeout: float | None = None,
  ) -> dict[str, StoredSegment | Exception]
  ```
- Produces the log lines:
  - INFO on success: `"Built segment '%s' for %s in %.1fs (%s)"`, with key, person name, elapsed seconds and source.
  - Failure: the existing warning becomes `"CastStudio could not build segment '%s' for %s after %.1fs (%s): %s"`, keeping `exc_info`.

- [ ] **Step 1: Write the failing tests**

- `test_build_segments_logs_each_build_time_with_source`: build `["weather"]` with `source="refresh"` at INFO level (caplog). Assert that one record matches `r"Built segment 'weather' for Sam in \d+\.\ds \(refresh\)"`.
- `test_build_segments_failure_log_includes_elapsed_and_source`: reuse the scripture-explodes setup from `test_build_segments_one_failure_does_not_stop_others`. Assert that a WARNING record contains `"scripture"`, `"after "` and `"(prebuild)"`.
- `test_build_segments_passes_sensor_refresh_timeout_to_commute_step`: patch `_async_build_one` to capture the `step` argument. Build `["commute", "weather"]` with `sensor_refresh_timeout=5`. Assert that the commute step has `step["sensor_refresh_timeout"] == 5` and the weather step has no such key. Without the argument, the commute step has no such key either. Give the person a commute zone and sensor so that `build_segment_step` yields a commute step, as in the existing commute tests in this file.

- [ ] **Step 2: Run them and confirm they fail**

Run: `python3 -m pytest tests/test_segments.py -k "logs_each_build_time or failure_log_includes or sensor_refresh_timeout" -v`
Expected: FAIL. The parameters don't exist yet, and the message doesn't match.

- [ ] **Step 3: Implement**

Time each job with `time.monotonic()` around the build plus store. Set `step["sensor_refresh_timeout"]` only when `segment_type_of(key) == "commute"` and the argument is not `None`. Log the all-heard clip's time too, under its key.

- [ ] **Step 4: Run the segment and briefing tests and confirm they pass**

Run: `python3 -m pytest tests/test_segments.py tests/test_briefing.py -q`
Expected: all pass. `FakeBuilder` in `test_briefing.py` still matches because the new parameters have defaults and briefing doesn't pass them yet.

- [ ] **Step 5: Commit**

```bash
scripts/lint
git add custom_components/cast_studio/segments.py tests/test_segments.py
git commit -m "feat(segments): log how long each segment build takes"
```

---

### Task 3: Dynamic segment types; play stops rebuilding commute

**Files:**
- Modify: `custom_components/cast_studio/briefing.py:131-132` (constant), `:608-659` (`_async_segments_to_play`), `:828` (its call in `async_play`)
- Modify: `tests/test_briefing.py`. Change `FakeBuilder.__call__` to accept `source: str = "prebuild"` and `sensor_refresh_timeout: float | None = None`, and record them in new `sources: list[str]` and `sensor_timeouts: list[float | None]` fields. Replace the tests at ~1360–1384.

**Interfaces:**
- Produces: `DYNAMIC_SEGMENT_TYPES: frozenset[str] = frozenset({"weather", "commute"})` in `briefing.py`, replacing `LIVE_SEGMENT_TYPES`.
- Produces: `_async_segments_to_play(hass, store, person, routine, keys, now, *, visual: bool, build_dynamic: bool = True) -> list[StoredSegment]`. It builds only the keys that have no usable stored segment. With `build_dynamic=False`, missing dynamic keys are not built. They fall through to the existing "Skipping segment" warning. Live builds pass `source="play"`. Task 5 passes `build_dynamic=False`.

- [ ] **Step 1: Write the failing tests and replace the old ones**

Delete `test_play_rebuilds_the_commute_live_after_a_prebuild` and `test_failed_live_commute_falls_back_to_the_prebuilt_one`. Add:
- `test_play_after_prebuild_builds_nothing_including_commute`: `sam_full`, prebuild "Full", clear calls, play "Full". Assert `h.builder.calls == []` and `h.ffmpeg.concats[-1] == FULL`.
- `test_play_builds_missing_segments_live_with_play_source`: `sam_full` with no prebuild, play "Full". Assert `h.builder.sources == ["play"]` and `h.builder.calls == [FULL]`.
- `test_dynamic_segment_types_are_weather_and_commute`: `assert briefing.DYNAMIC_SEGMENT_TYPES == {"weather", "commute"}`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `python3 -m pytest tests/test_briefing.py -k "builds_nothing_including_commute or play_source or dynamic_segment_types" -v`
Expected: FAIL. Commute is still rebuilt, `sources` is empty, and the constant is missing.

- [ ] **Step 3: Implement**

Rename the constant. Drop the "always rebuild" clause from the missing-key list, and add the `build_dynamic` filter. Update the docstring. Pass `source="play"` to `async_build_segments`.

- [ ] **Step 4: Run the briefing tests and confirm they pass**

Run: `python3 -m pytest tests/test_briefing.py -q`
Expected: all pass. If a remaining test assumed a commute rebuild at play time, update its expectation to the new behavior. Don't keep the old behavior.

- [ ] **Step 5: Commit**

```bash
scripts/lint
git add custom_components/cast_studio/briefing.py tests/test_briefing.py
git commit -m "feat(briefing): play no longer rebuilds the commute; weather and commute are dynamic"
```

---

### Task 4: `async_refresh`

**Files:**
- Modify: `custom_components/cast_studio/briefing.py`. Extract a helper from the body of `async_prebuild` (`:492-569`), add `async_refresh` and the refresh-running marker.
- Test: `tests/test_briefing.py`. Add a `Harness.refresh(name: str | None = None, person: Person | None = None)` helper like `prebuild`, and the tests below.

**Interfaces:**
- Consumes: `DYNAMIC_SEGMENT_TYPES` (Task 3), and the `source` and `sensor_refresh_timeout` parameters (Task 2).
- Produces:
  ```python
  REFRESH_SENSOR_TIMEOUT = 5
  DATA_REFRESHING = "briefing_refreshing"   # hass.data[DOMAIN][...] -> set[str] of person_ids

  def is_refreshing(hass: HomeAssistant, person_id: str) -> bool
  async def async_refresh(
      hass, entry, *, routine: Routine | None = None, person: Person | None = None
  ) -> None
  ```
  - Targeting matches `async_prebuild`: exactly one of `routine`/`person`, or `ValueError`.
  - Scope: with `routine`, `[routine]`. With `person`, that person's routines that contain a dynamic key.
  - Keys: the dynamic keys of the routines in scope (`segment_type_of(key) in DYNAMIC_SEGMENT_TYPES`). If there are none, log INFO `"Nothing to refresh for %s: no weather or commute segment"` and return.
  - The person id is in `DATA_REFRESHING` from before the lock is requested until the refresh ends (`try/finally`).
  - It builds with `source="refresh"` and `sensor_refresh_timeout=REFRESH_SENSOR_TIMEOUT`. It re-stores the pre-stitched files only for the visual routines in scope. Sensors are handled as in prebuild.
  - It logs INFO `"Refreshed %s for %s in %.1fs"`, with the key list, person name and total seconds.
  - If every key fails: `HomeAssistantError("CastStudio could not refresh any segment for <name>")`.
- Produces the private helper both entry points use. `async_prebuild` keeps its behavior and passes `source="prebuild"`:
  ```python
  async def _async_build_and_store(
      hass, entry, person: Person, routines: list[Routine], keys: list[str],
      *, source: str, sensor_refresh_timeout: float | None = None,
  ) -> dict[str, StoredSegment | Exception]
  ```
  This is the current prebuild body: lock, sensors set to generating, prune, credentials, visual keys, build, store visual routines, final sensor states. It returns the results, and the caller raises its own all-failed error.

- [ ] **Step 1: Write the failing tests**

Fixtures: use `sam_full` for audio cases. Add a fixture `sam_two_visual` with Sam on `DISPLAY` and two visual routines, "Morning" `["greeting", "weather"]` and "Night" `["weather", "closing"]`, built like `sam_visual_with_dashboard` without the dashboard.

- `test_refresh_routine_rebuilds_only_its_dynamic_segments`: prebuild "Full", clear calls, refresh "Full". Assert `h.builder.calls == [["weather", "commute"]]`, `h.builder.sources == ["refresh"]`, `h.builder.sensor_timeouts == [5]`.
- `test_refresh_person_covers_all_routines_with_dynamic_segments`: use `sam_quick_and_full`, refresh `person=Sam`. Assert one build call whose keys are exactly `{"weather", "commute"}`.
- `test_refresh_person_without_dynamic_segments_builds_nothing`: Sam with one routine `["greeting", "scripture"]`. Refresh person. Assert `h.builder.calls == []`, no exception, and an INFO record containing `"Nothing to refresh for Sam"`.
- `test_refresh_keeps_successful_segment_when_other_fails`: prebuild "Full", then `h.builder.fail = {"commute"}`, refresh "Full". Assert `store.get_fresh(person, "weather", now).built_at` is the refresh time (move the freezer 2 h before refreshing), and that commute's `built_at` is still the prebuild time.
- `test_refresh_all_failed_raises_and_keeps_prebuild_copies` (Review Focus 5): prebuild, `fail = {"weather", "commute"}`, and expect `HomeAssistantError` from refresh. Then play "Full": `h.ffmpeg.concats[-1] == FULL`, and the sensor state is `"ready"`.
- `test_second_refresh_rebuilds_fresh_dynamic_segments` (Review Focus 2): refresh "Full" twice with no time moved. Assert two build calls of `["weather", "commute"]`.
- `test_routine_refresh_restitches_its_visual_routine`: with `sam_two_visual`, prebuild person, refresh "Morning", then clear `h.ffmpeg.concats` and play "Morning". Assert `h.ffmpeg.concats == []` (no play-time stitch) and that the played media is the routine video stored by the refresh. Follow the assertion style of `test_visual_play_without_skip_uses_prestitched_video`.
- `test_routine_refresh_makes_other_visual_routine_restitch` (Review Focus 3): same setup, refresh "Morning", clear concats, play "Night". Assert one play-time concat `["weather", "closing"]`.
- `test_refresh_during_playback_keeps_streamed_file_and_playing_state` (Review Focus 1): adapt `test_prebuild_during_playback_never_overwrites_streamed_files` and `test_prebuild_does_not_flip_a_playing_routine`, calling `async_refresh` in place of prebuild.
- `test_is_refreshing_is_true_only_while_refresh_runs`: gate the builder, start a refresh task, wait for `builder.started`, and assert `briefing.is_refreshing(h.hass, SAM_ID)`. Release the gate, await, and assert it is false. Also assert it is false after a refresh that raised.
- `test_refresh_requires_exactly_one_target`: mirror `test_prebuild_requires_exactly_one_target`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `python3 -m pytest tests/test_briefing.py -k "refresh" -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'async_refresh'`.

- [ ] **Step 3: Implement**

Extract `_async_build_and_store` first, with no change to prebuild behavior. Then add `is_refreshing` and `async_refresh` on top of it.

- [ ] **Step 4: Run the full briefing tests and confirm they pass**

Run: `python3 -m pytest tests/test_briefing.py -q`
Expected: all pass, including every existing prebuild test.

- [ ] **Step 5: Commit**

```bash
scripts/lint
git add custom_components/cast_studio/briefing.py tests/test_briefing.py
git commit -m "feat(briefing): refresh rebuilds only a routine's weather and commute"
```

---

### Task 5: Play waits up to 10 s for a running refresh

**Files:**
- Modify: `custom_components/cast_studio/briefing.py`, `async_play` (`:789-880`) and the `get_lock` docstring (`:98-107`)
- Test: `tests/test_briefing.py` (next to `test_play_waits_for_inflight_prebuild`, ~line 587). Add `gate_source: str | None = None` to `FakeBuilder`. When it is set, only calls with that `source` wait on `gate`, so a play's own live build isn't blocked by a gated refresh. Tests below gate with `gate_source="refresh"`, except where they gate a prebuild.

**Interfaces:**
- Consumes: `is_refreshing` (Task 4), and `_async_segments_to_play(..., build_dynamic=...)` (Task 3).
- Produces: `REFRESH_WAIT_SECONDS = 10.0` in `briefing.py`, and the play-lock helper:
  ```python
  @asynccontextmanager
  async def _async_play_lock(hass, person: Person, routine: Routine) -> AsyncIterator[bool]
  ```
  - If no refresh is running for the person, it acquires `get_lock` with no time limit (unchanged, so plays still wait for a prebuild in full) and yields `True`.
  - If a refresh is running, it acquires within `REFRESH_WAIT_SECONDS`. On success it yields `True` and logs INFO `"Play of %s's '%s' waited %.1fs for a refresh"`. On timeout it yields `False` without the lock and logs INFO `"Play of %s's '%s' stopped waiting for a refresh after %.0fs; playing stored segments"`.
  - When it yields `False`, `async_play` calls `_async_segments_to_play(..., build_dynamic=False)`. Everything else in `async_play` is unchanged.

- [ ] **Step 1: Write the failing tests**

- `test_play_waits_for_a_running_refresh_and_uses_fresh_copies`: prebuild "Full" at the harness start time (08:00). Move the freezer forward 2 h, gate the builder, and start a refresh task. Once `started`, start a play task, yield a few times, and assert `h.played == []`. Release the gate and gather. Assert one play, and that the stored weather's `built_at` is 10:00 (the refresh time) before the play's concat ran. Check this through `h.builder.calls` order: the refresh call comes first and no play-sourced call follows.
- `test_play_stops_waiting_after_timeout_and_plays_prebuild_copies`: patch `f"{BRIEFING}.REFRESH_WAIT_SECONDS"` to `0.05`. Prebuild, gate the builder, start a refresh, then `await h.play("Full")` while the gate is closed. Assert one play with `concats[-1] == FULL` and builder calls unchanged by the play (only the refresh's call). An INFO record contains `"stopped waiting for a refresh"`. Then release the gate, await the refresh, and assert weather's `built_at` is the refresh time.
- `test_play_after_wait_timeout_skips_unbuilt_dynamic_segments` (Review Focus 4): same patch, no prebuild, gated refresh running. Play "Full". Assert that the builder's play-sourced call (if any) contains neither `"weather"` nor `"commute"`. Assert the played concat contains `greeting`, `scripture` and `closing`, without `weather` and `commute`. Assert "Skipping segment 'weather'" is in the warnings.
- `test_play_still_waits_without_limit_for_a_prebuild`: patch the wait to `0.05`, gate a **prebuild**, start a play, then `asyncio.sleep(0.2)`. Assert `h.played == []`. Release the gate, and assert one play.

- [ ] **Step 2: Run them and confirm they fail**

Run: `python3 -m pytest tests/test_briefing.py -k "running_refresh or stops_waiting or skips_unbuilt or without_limit" -v`
Expected: `stops_waiting` and `skips_unbuilt` FAIL. The play blocks until the gate opens, so give the test a guard of `asyncio.timeout(2)` around the play so it fails rather than hangs.

- [ ] **Step 3: Implement**

Add the `_async_play_lock` helper and use it in place of `async with get_lock(...)` in `async_play`. Update the `get_lock` docstring: a play waits on it without limit for a prebuild, and at most `REFRESH_WAIT_SECONDS` for a refresh.

- [ ] **Step 4: Run the full test suite and confirm it passes**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
scripts/lint
git add custom_components/cast_studio/briefing.py tests/test_briefing.py
git commit -m "feat(briefing): play waits at most 10 s for a running refresh"
```

---

### Task 6: `cast_studio.refresh` action and docs

**Files:**
- Modify: `custom_components/cast_studio/__init__.py:75-140`. Add `REFRESH_SCHEMA = PREBUILD_SCHEMA`, the handler and the registration.
- Modify: `custom_components/cast_studio/services.yaml`, `strings.json`, `translations/en.json`
- Modify: `SPEC.md` (lines 18, 88, 118, 147, 149) and `README.md` (lines 37, 110, the actions section near 132, the examples near 237)
- Test: `tests/test_services.py`

**Interfaces:**
- Consumes: `briefing.async_refresh` (Task 4).
- Produces: the action `cast_studio.refresh` with fields `person` and `routine` (at least one). The handler resolves them exactly like `_async_handle_prebuild`, with `find_routine`, or `find_person` plus the "not found" `HomeAssistantError`.

- [ ] **Step 1: Write the failing tests**

- Update `test_only_the_current_actions_are_registered` to expect `"refresh"` in the set.
- `test_refresh_action_resolves_routine_and_person`: patch `briefing.async_refresh` with `AsyncMock`. Call `refresh` with `{"routine": "Quick", "person": "Sam"}`, and assert it was awaited with `routine=` the Quick routine. Call it with `{"person": "Sam"}`, and assert `person=` Sam. Calling it with `{}` raises `vol.Invalid`. Calling it with `{"person": "Nobody"}` raises `HomeAssistantError`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `python3 -m pytest tests/test_services.py -v`
Expected: FAIL. The action isn't registered.

- [ ] **Step 3: Implement the action and the descriptions**

`services.yaml` / `strings.json` / `translations/en.json` text, word for word:
- name: `Refresh dynamic segments` (yaml: `Refresh Dynamic Segments`, matching that file's title case)
- description: `Rebuilds only the weather and commute segments of a routine, or of all of a person's routines that have them, so they reflect this moment. Other segments are left as they are. A play started while a refresh runs waits up to 10 seconds for it, then plays the stored segments.`
- person: `Person to refresh. Alone, refreshes every routine of that person that has a weather or commute segment; with a routine, picks which person's routine is meant.`
- routine: `Routine whose weather and commute segments to refresh. Give a person or a routine, or both.`

- [ ] **Step 4: Update SPEC.md and README.md**

Change the commute-at-play statements to: weather and commute are dynamic; they are refreshed by `cast_studio.refresh` (not at play); play uses the stored copy and waits up to 10 s for a running refresh. In SPEC.md:149, add `cast_studio.refresh` to the actions list. In SPEC.md:147, note the 5 s sensor cap during a refresh, and the INFO timing log for every segment build. In README, add a `cast_studio.refresh` section beside `prebuild`, with this example automation:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.javan_in_bed
    to: "off"
conditions:
  - condition: state
    entity_id: sensor.morning_rush
    state: "True"
actions:
  - action: cast_studio.refresh
    data:
      person: Javan
      routine: Morning Display
```

Add a line saying that a routine reused later in the day (for example a night routine) needs its own `refresh` or routine-scoped `prebuild` at that time, and that `max_age_minutes` is the safety net.

- [ ] **Step 5: Run the full suite and lint, then confirm they pass**

Run: `python3 -m pytest -q && scripts/lint`
Expected: all tests pass; lint leaves no changes or errors.

- [ ] **Step 6: Commit**

```bash
git add custom_components/cast_studio/__init__.py custom_components/cast_studio/services.yaml custom_components/cast_studio/strings.json custom_components/cast_studio/translations/en.json SPEC.md README.md tests/test_services.py
git commit -m "feat: cast_studio.refresh action for weather and commute"
```
