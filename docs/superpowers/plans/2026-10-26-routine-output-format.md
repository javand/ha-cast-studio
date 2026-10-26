# Output Format per Routine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Audio/Visual from the person to the routine, and have play send video to screen devices and audio to everything else.

**Architecture:** A new `has_screen(hass, entity_id)` decides screen devices from the device registry model. Segment building takes explicit visual flags instead of reading the person. Prebuild and play read `Routine.output_format`; play picks video only for a Visual routine on a screen. A minor-version config migration copies each person's format onto their routines, then the person field is removed.

**Tech Stack:** Python 3.13, Home Assistant custom integration, pytest + pytest-homeassistant-custom-component, ruff.

**Spec:** `docs/superpowers/specs/2026-10-26-routine-output-format-design.md`

## Global Constraints

- Formats are exactly `"audio"` and `"visual"` (`OUTPUT_FORMATS`); default for new routines `"visual"` (`DEFAULT_OUTPUT_FORMAT`).
- Routine data key: `output_format` (constant `ROUTINE_OUTPUT_FORMAT`).
- Screen models: model contains `Hub`, or contains `Chromecast` but not `Chromecast Audio`, or contains `Google TV`. Only Cast players (`_is_cast_player`) can be screens. `Google Cast Group` is never a screen. Fallback when the device has no model or there is no device: a device/entity name or entity id containing `display` (case-insensitive).
- No new field on the `play` service; automations keep passing `target`.
- An Audio routine never builds slides or clips; a Visual routine on a speaker never waits on a build because the device can't show video.
- Never commit real names, addresses, coordinates or personal zone ids; tests use Alex/Sam, `zone.office`, `media_player.kitchen_speaker`, `media_player.office_display`.
- Test command: `python -m pytest -q -p no:cacheprovider --timeout 60`; lint: `ruff check custom_components tests` and `ruff format custom_components tests`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A Visual routine played on a speaker with no stored routine audio (prebuilt before this change) stitches the segments' `.mp3` files and builds nothing. Test in Task 4.
2. A person prebuild where an Audio and a Visual routine share a segment builds that segment visual once, and both routines show ready. Test in Task 3.
3. A Cast group target gets audio even when a member is a Nest Hub. Test in Task 1.
4. A `Chromecast Audio` gets audio, while a `Chromecast` gets video. Test in Task 1.
5. Migration runs once: a routine that already has `output_format` keeps it, an orphan routine gets `visual`, and a v1 entry ends at version 2 / minor 2. Test in Task 5.

---

### Task 1: Screen detection

**Files:**
- Modify: `custom_components/cast_studio/orchestrator.py` (next to `_is_cast_player`, ~line 391)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Produces: `has_screen(hass: HomeAssistant, entity_id: str | None) -> bool` in `orchestrator.py`.

- [ ] **Step 1: Write the failing tests.** Use the real `hass` fixture. Register a `MockConfigEntry(domain="cast")`, create a device with `dr.async_get(hass).async_get_or_create(config_entry_id=..., identifiers={("cast", uid)}, name=..., model=...)`, and an entity with `er.async_get(hass).async_get_or_create("media_player", "cast", uid, config_entry=..., device_id=device.id, suggested_object_id=...)`.

```python
@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("Google Nest Hub", True),
        ("Google Nest Hub Max", True),
        ("Chromecast", True),
        ("Google TV Streamer", True),
        ("Chromecast Audio", False),
        ("Google Nest Mini", False),
        ("Google Home Max", False),
        ("Google Cast Group", False),
        ("SRS-XB501G", False),
    ],
)
async def test_has_screen_by_model(hass, model, expected): ...
    # entity named "media_player.office_speaker" so the name fallback can't help
    assert has_screen(hass, entity_id) is expected

async def test_has_screen_group_named_display_is_not_a_screen(hass): ...
    # model "Google Cast Group", device name "Display Group" -> False

async def test_has_screen_name_fallback_without_model(hass): ...
    # cast entity, device model None, entity id media_player.hall_display -> True
    # same with media_player.hall_speaker -> False

async def test_has_screen_unregistered_entity_uses_name(hass): ...
    # no registry entry; state set with {"app_name": "Default Media Receiver"}
    # media_player.office_display -> True; media_player.kitchen_speaker -> False

async def test_has_screen_non_cast_player_is_never_a_screen(hass): ...
    # platform "squeezebox", model "Google Nest Hub", id media_player.den_display -> False
    # None / "" -> False
```

- [ ] **Step 2: Run** `python -m pytest -q -p no:cacheprovider tests/test_orchestrator.py -k has_screen`. Expected: collection error, `has_screen` not importable.
- [ ] **Step 3: Implement `has_screen`.** Return False when `not _is_cast_player(hass, entity_id)`. Read the entity registry entry, then its device. If the device has a model, apply the model rule from Global Constraints (`Cast Group` → False before any name check). Otherwise apply the name fallback over device `name_by_user`/`name`, entity `name`/`original_name`, the state's `friendly_name`, and the entity id.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(orchestrator): tell screen devices from speakers`.

---

### Task 2: Segment builds take explicit visual flags

**Files:**
- Modify: `custom_components/cast_studio/segments.py` (`build_segment_step` ~188, `_all_heard_step` ~274, `_async_build_one` ~533/558, `async_build_segments` ~615)
- Modify: `custom_components/cast_studio/briefing.py` (the three `async_build_segments` calls)
- Test: `tests/test_segments.py`, `tests/test_briefing.py` (`FakeBuilder`)

**Interfaces:**
- Produces:
  - `build_segment_step(person, segment_key, day=None, *, visual: bool = True) -> dict` sets `step["visual"] = visual`.
  - `_all_heard_step(person, day=None, *, visual: bool = True)`.
  - `async_build_segments(hass, store, person, keys, ai_creds, *, now, visual_keys: Collection[str] = (), all_heard_visual: bool = False)`. A key is built with slide and clip only when it is in `visual_keys`; the all-heard line only when `all_heard_visual`.
  - `_async_build_one` reads `step["visual"]` instead of `person.output_format`.

- [ ] **Step 1: Write the failing tests** in `tests/test_segments.py`, following the existing tests there that patch `_async_compile_single_step`:
  - `test_build_segment_step_visual_flag`: `build_segment_step(alex, "weather", visual=False)["visual"] is False`; the default is True.
  - `test_build_segments_visual_only_for_visual_keys`: with an audio-format person, `async_build_segments(..., ["greeting", "weather"], ..., visual_keys={"weather"}, all_heard_visual=False)` stores a clip for weather only. Neither greeting nor the all-heard line has a `clip_path`, and the compile helper got `is_visual=False` for them.
- [ ] **Step 2: Run** `-k "visual_flag or visual_only_for_visual_keys"`. Expected: FAIL (unexpected keyword).
- [ ] **Step 3: Implement** the signatures above. In `briefing.py`, keep today's behaviour for now: each call passes `visual_keys=keys if person.output_format == "visual" else ()` and `all_heard_visual=person.output_format == "visual"`. Update `FakeBuilder.__call__` in `tests/test_briefing.py` to accept `visual_keys=()` and `all_heard_visual=False`, writing a clip for a key iff it is in `visual_keys`, and for the all-heard line iff `all_heard_visual`.
- [ ] **Step 4: Run the full suite.** Expected: all pass.
- [ ] **Step 5: Commit** `refactor(segments): pass visual per key instead of reading the person`.

---

### Task 3: Routine format drives prebuild; stored routine audio

**Files:**
- Modify: `custom_components/cast_studio/people.py` (constants, `Routine`, `_routine_from_subentry`)
- Modify: `custom_components/cast_studio/briefing.py` (`_usable_segment`, `_ready_keys`, `_async_store_routine_video`, `async_prebuild`)
- Modify: `tests/conftest.py` (`routine_subentry(..., **data)`, default `"output_format": "visual"`)
- Test: `tests/test_briefing.py`, `tests/test_people.py`

**Interfaces:**
- Consumes: Task 2's `visual_keys` / `all_heard_visual`.
- Produces:
  - `ROUTINE_OUTPUT_FORMAT = "output_format"` in `people.py`.
  - `Routine.output_format: Literal["audio", "visual"]`, parsed with default `DEFAULT_OUTPUT_FORMAT`.
  - `_usable_segment(store, person, key, now, *, visual: bool)`: needs a clip only when `visual`.
  - `_ready_keys(store, person, routine, now)` uses `visual=routine.output_format == "visual"`.
  - `_person_has_visual_routine(entry, person) -> bool`.
  - The stored routine audio is `routine_<slug>-audio.<ver>.mp3`, with a sidecar `routine_<slug>-audio.json` in the same shape as the video sidecar (key `"audio"` instead of `"video"`). The `-` can't come out of `slugify`, so its names can't collide with another routine's.
  - `_async_store_routine_media(hass, store, person, routine, now, *, video: bool)` and `_async_fresh_routine_media(hass, store, person, routine, segments, now, *, video: bool) -> _Media | None` replace the video-only pair. The file-name check accepts `.mp4` for video and `.mp3` for audio.

- [ ] **Step 1: Write the failing tests:**
  - `tests/test_people.py::test_routine_output_format_parsed_with_default`: a routine with `output_format="audio"` parses as audio; one without the key parses as visual.
  - `tests/test_briefing.py::test_prebuild_audio_routine_builds_no_clips_and_stitches_nothing`: Sam (person visual) with a routine Quick `output_format="audio"`. After prebuild, `h.builder` got `visual_keys` empty, `h.ffmpeg.concats == []`, and the sensor is ready.
  - `test_prebuild_visual_routine_stores_video_and_audio`: a visual Quick. Both `routine_quick.json` and `routine_quick-audio.json` exist, and the audio sidecar's file ends `.mp3`. `h.ffmpeg.concats` has two lists of the same segment names.
  - `test_prebuild_person_shared_segment_built_visual_once` (Review Focus 2): Sam with an Audio routine Night `["greeting", "closing"]` and a Visual routine Quick `["greeting", "weather"]`. A person prebuild calls the builder once, with keys greeting, closing and weather and `visual_keys == {"greeting", "weather"}`. Both sensors are ready. Only Quick has routine sidecars.
  - Record `visual_keys` per call in `FakeBuilder` (e.g. `visual_calls: list[set[str]]`).
- [ ] **Step 2: Run** these tests. Expected: FAIL.
- [ ] **Step 3: Implement.** In `async_prebuild`:
  - `visual_keys` is the union of keys of the Visual routines being built.
  - `all_heard_visual = _person_has_visual_routine(entry, person)`.
  - After building, every Visual routine stores both video and audio; Audio routines store nothing.

  Move tests that set `output_format="audio"` on the person and rely on audio behaviour to set it on their routines instead (the person value is no longer read by prebuild).
- [ ] **Step 4: Run the full suite.** Expected: all pass.
- [ ] **Step 5: Commit** `feat(briefing): prebuild by the routine's output format`.

---

### Task 4: Play picks video or audio from the routine and the device

**Files:**
- Modify: `custom_components/cast_studio/briefing.py` (`async_play`, `_async_segments_to_play`, `_async_stitch_for_play`, `_async_all_heard`)
- Test: `tests/test_briefing.py`

**Interfaces:**
- Consumes: `has_screen` (Task 1); `Routine.output_format`, `_usable_segment(..., visual=)`, `_async_fresh_routine_media(..., video=)` (Task 3).
- Produces: inside `async_play`, `video = routine.output_format == "visual" and has_screen(hass, player)`. That flag is passed as `visual=video` to `_async_segments_to_play`, `_async_stitch_for_play` and `_async_all_heard`, which stop reading `person.output_format`. Live builds of missing keys pass `visual_keys=missing if video else ()` and `all_heard_visual=video`. (The spec's "any Visual routine" rule for the all-heard line applies to prebuild; a play builds only what it is about to play, per the anti-goal.)

- [ ] **Step 1: Write the failing tests** (the harness's `DISPLAY` is a Cast display by name, and `SPEAKER` is not):
  - `test_visual_routine_on_speaker_plays_prebuilt_audio`: prebuild a visual Quick, then `h.play("Quick", target=SPEAKER)`. The played media ends `routine_quick-audio.mp3` (after `unversioned`), and nothing is built.
  - `test_visual_routine_on_speaker_without_routine_audio_stitches_mp3s` (Review Focus 1): prebuild, delete `routine_quick-audio.json`, play to SPEAKER. One `.mp3` concat of greeting and weather, and no build call.
  - `test_visual_routine_on_display_plays_video`: play to DISPLAY ends `routine_quick.mp4`.
  - `test_audio_routine_on_display_plays_audio`: an Audio Quick played to DISPLAY plays `.mp3`, and no clip is built.
  - `test_visual_routine_on_speaker_builds_missing_audio_only`: no prebuild, play to SPEAKER. The builder's `visual_keys` is empty.
  - `test_all_heard_on_speaker_plays_mp3`: everything heard, then `skip_played=True` to SPEAKER plays the all-heard `.mp3`, even when the clip has an `.mp4`.
- [ ] **Step 2: Run** these tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - When `keys == all_keys`, try `_async_fresh_routine_media(..., video=video)` first, then stitch with `visual=video`.
  - Release-player and dashboard logic stay unchanged.
  - Move any remaining tests that depend on an audio person to audio routines.
- [ ] **Step 4: Run the full suite.** Expected: all pass. `grep -n "output_format" custom_components/cast_studio/briefing.py custom_components/cast_studio/segments.py` should show only `routine.output_format` uses.
- [ ] **Step 5: Commit** `feat(briefing): play video on screens and audio on speakers`.

---

### Task 5: Settings move to the routine; migration

**Files:**
- Modify: `custom_components/cast_studio/config_flow.py` (`MINOR_VERSION = 2`; `_person_schema` drops the field; `_routine_schema`/`_routine_data` add it)
- Modify: `custom_components/cast_studio/__init__.py` (`async_migrate_entry`)
- Modify: `custom_components/cast_studio/people.py` (drop `PERSON_OUTPUT_FORMAT` from `PERSON_CORE_KEYS` and `build_person_data`; remove `Person.output_format`)
- Modify: `custom_components/cast_studio/strings.json`, `translations/en.json` (move `output_format` label and description from the person user/reconfigure steps to the routine steps; keep the `output_format` selector translations)
- Modify: `README.md` (move the Output format row from the person table to the routine settings; explain screen vs speaker play in one short paragraph)
- Modify: `tests/conftest.py` (`person_subentry` drops `output_format`), and every test that still passes it to a person
- Test: `tests/test_people.py`, `tests/test_config_flow.py`

**Interfaces:**
- Consumes: `ROUTINE_OUTPUT_FORMAT`, `DEFAULT_OUTPUT_FORMAT`, `OUTPUT_FORMATS`.
- Produces: entries at version 2, minor 2, with `output_format` on every routine and on no person.

- [ ] **Step 1: Write the failing tests:**
  - `test_people.py::test_migrate_minor_copies_person_format_to_routines`: a v2 / minor 1 entry. Alex has `output_format: "audio"`, with routines A (no key) and B (`output_format: "visual"`). Plus an orphan routine C whose `person_id` is unknown. After setup: A is audio, B is visual, C is visual, Alex's data has no `output_format`, and `entry.minor_version == 2`.
  - `test_migrate_v1_entry_creates_first_person_and_no_routine`: also assert `entry.minor_version == 2`, and drop the `alex.output_format` assertion.
  - `test_config_flow.py`: the routine add and reconfigure forms show `output_format` with options `{"audio", "visual"}`, default `visual` on add and the current value on reconfigure, and save it. The person forms no longer have `output_format`. Update the existing person-form key-order assertion (`["name", "phonetic_name", "media_player", ...]`).
- [ ] **Step 2: Run** these tests. Expected: FAIL.
- [ ] **Step 3: Implement.** Migration branch: `if entry.version == 2 and entry.minor_version < 2`. For each routine subentry without the key, set the format from its person's data (default `visual`) with `hass.config_entries.async_update_subentry(entry, subentry, data=...)`. Then strip the key from each person and call `async_update_entry(entry, minor_version=2)`. The v1 branch also ends at minor 2. Remove `Person.output_format` and fix any remaining readers.
- [ ] **Step 4: Run** the full suite plus `ruff check` and `ruff format --check`. Expected: all pass, and `grep -rn "person.output_format\|PERSON_OUTPUT_FORMAT" custom_components tests` finds nothing.
- [ ] **Step 5: Commit** `feat(routine): output format is a routine setting`.
