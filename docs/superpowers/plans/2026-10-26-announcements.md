# Announcements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Household announcements: a new subentry type the user describes once, which CastStudio builds (AI words, persona voice, AI-painted slide) and plays on demand, interrupting and resuming a playing briefing.

**Architecture:** Four new modules split by job: `announcements.py` (settings model, persona pick, picture paths), `announcement_build.py` (one build on disk with its sidecar), `announcement_runtime.py` (which announcement needs a build and when; status; daily timers), `announcement_play.py` (the `announce` action: per-target queue, briefing pause/resume). Two new entity platforms (`image.py`, `button.py`). The orchestrator's duration wait becomes extendable, and `briefing.py` exposes the play running on a player so an announcement can pause and resume it.

**Tech Stack:** Home Assistant custom integration (HA 2026.2), Python 3.13, pytest + pytest-homeassistant-custom-component, FFmpeg, Gemini REST (`gemini-2.5-flash` text, `gemini-2.5-flash-image`), Pillow.

**Spec:** `docs/superpowers/specs/2026-10-26-announcements-design.md`

## Global Constraints

- Public repo: tests and docs use only fake values (Alex, Sam, `media_player.kitchen_speaker`, `media_player.office_display`, `zone.office`, 40.0/-75.0). No real names, places or ids.
- Building an announcement must never touch any person's briefing files or heard records: everything lives under `<output_dir>/announcements/<announcement id>/`.
- An announcement must never fail to play because image generation failed: it falls back to a plain slide and still speaks.
- An announcement must never leave an interrupted briefing stopped; a failed resume shows the briefing sensor `error`, never stuck at `playing`.
- Static: built once; rebuilt only on a settings change, a rebuild request, or no successful build on disk. Daily: also rebuilt once a day at its rebuild time (default `03:00:00`). A restart reuses the last build.
- Plays never wait for a rebuild when a previous build exists.
- Files are written with versioned names (`segments.versioned_path`); the current and the previous build's files are kept, older ones removed after a successful build.
- At most 4 pictures; local media only (`media-source://media_source/...`).
- Commands: `python -m pytest -q -p no:cacheprovider --timeout 60`, `ruff check custom_components tests`, `ruff format custom_components tests`. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Announcement removed while it is building** — the finished build is discarded, its folder is removed, nothing raises, and no entity is left updating. Test in Task 5.
2. **The briefing ends, is superseded, or is replaced during the announcement** — no resume is attempted and nothing re-casts old media. Test in Task 8.
3. **Gemini creds missing or OpenAI is the platform, or the image response has no inline image** — plain fallback slide, status `fallback`, words still spoken. Test in Task 4.
4. **A picture was deleted from the media folder after it was saved in the form** — skipped with a warning; the build goes on. Test in Task 4.
5. **HA was down at a Daily announcement's rebuild time** — it rebuilds once at startup, and a second restart the same day does not rebuild again. Test in Task 5.

---

### Task 1: Announcement settings model

**Files:**
- Create: `custom_components/cast_studio/announcements.py`
- Test: `tests/test_announcements.py`
- Modify: `tests/conftest.py` (add `announcement_subentry`)

**Interfaces:**
- Produces (all in `announcements.py`):
  - Constants: `SUBENTRY_ANNOUNCEMENT = "announcement"`; data keys `ANN_NAME = "name"`, `ANN_DESCRIPTION = "description"`, `ANN_WORDING = "wording"`, `ANN_MESSAGE = "message"`, `ANN_FRESHNESS = "freshness"`, `ANN_PERSONAS = "personas"`, `ANN_REBUILD_TIME = "rebuild_time"`, `ANN_PICTURES = "pictures"`, `ANN_OUTPUT_FORMAT = "output_format"`; `WORDINGS = ("verbatim", "creative")`, `FRESHNESS = ("static", "daily")`, `DEFAULT_REBUILD_TIME = "03:00:00"`, `DEFAULT_PERSONA = "default_host"`, `MAX_PICTURES = 4`, `LOCAL_MEDIA_PREFIX = "media-source://media_source/"`.
  - `@dataclass(frozen=True) class Announcement`: `announcement_id: str`, `name: str`, `description: str`, `wording: Literal["verbatim","creative"]`, `message: str`, `freshness: Literal["static","daily"]`, `persona_ids: tuple[str, ...]`, `rebuild_time: datetime.time`, `pictures: tuple[str, ...]` (media_content_ids), `output_format: Literal["audio","visual"]`.
  - `get_announcements(entry) -> list[Announcement]`, `find_announcement(entry, name_or_id: str) -> Announcement` (id, then slug of name; raises `HomeAssistantError` "CastStudio announcement '<x>' not found").
  - `settings_fingerprint(a: Announcement) -> str` — sha256 hex of the sorted JSON of every field except `announcement_id` and `rebuild_time` (Claude's call: moving the time is not a content change).
  - `announcement_day(a, now: datetime) -> date` — local date, minus one day before `a.rebuild_time` (same rule as `people.briefing_day`).
  - `pick_build_persona(a, previous_id: str | None, rng: random.Random | None = None) -> Persona` — valid catalog ids from `a.persona_ids` (any role); none valid → `get_persona(DEFAULT_PERSONA)`. Static: the first valid id. Daily: `rng.choice` over valid ids, excluding `previous_id` when two or more remain.
  - `resolve_local_picture(hass, media_content_id: str) -> Path | None` — `media-source://media_source/<dir_id>/<rel>` → `Path(hass.config.media_dirs[dir_id]) / rel`; None for other prefixes, unknown `dir_id`, a path escaping the media dir, or a missing file.
- `tests/conftest.py`: `announcement_subentry(subentry_id, name, **data) -> dict` with defaults: description "A friendly reminder", wording "verbatim", message "Close the freezer door", freshness "static", personas `["default_host"]`, rebuild_time `"03:00:00"`, pictures `[]`, output_format "visual".

- [ ] **Step 1: Write the failing tests** in `tests/test_announcements.py`:
  - `test_get_announcements_reads_subentries_and_defaults` — an entry with one announcement subentry and one person; `get_announcements` returns one `Announcement` with the conftest values and `rebuild_time == time(3, 0)`; a subentry missing `personas`, `freshness`, `output_format` reads `("default_host",)`, `"static"`, `"visual"`.
  - `test_find_announcement_by_id_and_name` — `find_announcement(entry, "freezer door")` matches name "Freezer Door"; unknown name raises `HomeAssistantError`.
  - `test_fingerprint_changes_with_settings_not_rebuild_time` — changing message changes it; changing rebuild_time does not.
  - `test_announcement_day_rolls_at_rebuild_time` — rebuild 03:00: 02:59 on the 20th → 19th; 03:00 → 20th.
  - `test_pick_persona_static_uses_first_valid` — ids `("nope", "default_host")` → default_host.
  - `test_pick_persona_daily_never_repeats_previous` — two valid ids, previous = first, 20 picks with `random.Random(n)` → always the second.
  - `test_pick_persona_daily_single_choice_repeats` and `test_pick_persona_unknown_ids_fall_back_to_default_host`.
  - `test_resolve_local_picture` — with `hass.config.media_dirs = {"local": str(tmp_path)}` and a file `a.jpg`: `media-source://media_source/local/a.jpg` → that path; `.../local/../x.jpg` → None; `media-source://other/x` → None; missing file → None.
- [ ] **Step 2: Run** `python -m pytest -q -p no:cacheprovider tests/test_announcements.py` — Expected: FAIL, `ModuleNotFoundError: custom_components.cast_studio.announcements`.
- [ ] **Step 3: Implement** `announcements.py` per the Interfaces block; `_announcement_from_subentry` mirrors `people._routine_from_subentry`. `resolve_local_picture` does the file check, so callers run it in the executor.
- [ ] **Step 4: Run** the same command — Expected: all pass.
- [ ] **Step 5: Commit** `feat(announcements): settings model, persona pick and local pictures`.

---

### Task 2: Announcement subentry form

**Files:**
- Modify: `custom_components/cast_studio/config_flow.py` (new `AnnouncementSubentryFlow`, register in `async_get_supported_subentry_types`)
- Modify: `custom_components/cast_studio/strings.json`, `translations/en.json`
- Test: `tests/test_config_flow.py`

**Interfaces:**
- Consumes: Task 1 constants, `get_announcements`, `is_valid_name`, `slugify_name`, `personas.PERSONAS`.
- Produces: stored subentry data with exactly the Task 1 keys; title = name.

Form fields, in order: name (text, required), description (multiline text, required), wording (LIST select, `translation_key="announcement_wording"`, default verbatim), message (multiline text, optional), freshness (LIST select, `translation_key="announcement_freshness"`, default static), personas (multi-select DROPDOWN over all `PERSONAS`, default `["default_host"]`), rebuild_time (`TimeSelector`, default `03:00:00`), pictures (`MediaSelector(MediaSelectorConfig(accept=["image/*"], multiple=True))`, optional; stored as the list of `media_content_id` strings, shown back as `[{"media_content_id": id, "media_content_type": "image/jpeg"}]`), output_format (LIST select, existing `output_format` translation key, default visual). Reconfigure uses the same form with current values. No entry reload: the runtime picks up changes through the update listener (Task 5).

Validation errors (base unless noted): `invalid_name`, `duplicate_announcement` (slug clash with another announcement), `message_required` (field `message`, when wording is verbatim and message is blank), `one_persona` (field `personas`, static with more than one), `too_many_pictures` (field `pictures`, more than 4), `local_pictures_only` (field `pictures`, any id not starting with `LOCAL_MEDIA_PREFIX`). Empty personas store `["default_host"]`.

Strings: `config_subentries.announcement` with `entry_type` "Announcement", `initiate_flow` user "Add announcement" / reconfigure "Edit announcement", step titles, a `data` label and `data_description` for every field (description help gives the examples from the spec: "AI background of a cosy front door at dusk…", "use the uploaded image as is…"; freshness help: "Static is built once. Daily is rebuilt every day at the rebuild time, with a persona picked from your list."; rebuild time help: "Daily only."), the errors above, and `selector.announcement_wording` (verbatim "Exactly as written", creative "Written by the persona") and `selector.announcement_freshness` (static "Static", daily "Daily"). `en.json` stays identical to `strings.json`.

- [ ] **Step 1: Write the failing tests** in `tests/test_config_flow.py`:
  - `test_add_announcement_stores_settings` — create flow on `("<entry_id>", "announcement")`, submit name "Freezer Door", description, wording verbatim, message, pictures `[{"media_content_id": "media-source://media_source/local/door.jpg", "media_content_type": "image/jpeg"}]`; result CREATE_ENTRY, data `pictures == ["media-source://media_source/local/door.jpg"]`, `personas == ["default_host"]`, title "Freezer Door".
  - `test_announcement_errors` (parametrized) — each error above with the input that triggers it, including a duplicate of an existing "Freezer Door" as "freezer door".
  - `test_reconfigure_announcement_updates_data` — changing message updates the subentry and aborts `reconfigure_successful`.
  - `test_strings_match_translations` already exists — keep it green.
- [ ] **Step 2: Run** `python -m pytest -q -p no:cacheprovider tests/test_config_flow.py` — Expected: new tests FAIL (unknown subentry type).
- [ ] **Step 3: Implement** the flow and strings.
- [ ] **Step 4: Run** the same command — Expected: all pass.
- [ ] **Step 5: Commit** `feat(config): announcement subentry form`.

---

### Task 3: AI words and slide image for announcements

**Files:**
- Modify: `custom_components/cast_studio/ai_client.py`
- Test: `tests/test_ai_client.py`

**Interfaces:**
- Produces:
  - `build_announcement_prompt(description: str, character: str) -> str`
  - `async def async_generate_announcement_words(hass, description: str, persona: Persona, ai_creds: dict[str, Any]) -> str` — Gemini structured first (falling back to the conversation agent exactly as `async_generate_step_content` does), returns `narration_script.strip()`; raises `AIClientError("No announcement words were returned")` when empty.
  - `_query_gemini_image_api(..., *, images: Sequence[tuple[str, bytes]] = (), aspect_ratio: str | None = None)` — each image becomes a part `{"inlineData": {"mimeType": mime, "data": b64}}` after the text part; `aspect_ratio` sets `generationConfig.imageConfig = {"aspectRatio": aspect_ratio}`.
  - `async_generate_ai_image(..., *, reference_images: Sequence[tuple[str, bytes]] = (), aspect_ratio: str | None = None)` — passes both through; existing callers unchanged.

The prompt must contain: the description verbatim; the persona character as "Voice Personality"; "This is a short household announcement spoken aloud on a smart speaker"; "Write only the words to be said, in the speaker's character, but never state, invent or introduce a name for the speaker"; the spoken rules copied from the existing SPEAKER CHARACTER block (no stage directions in asterisks or brackets, no markdown or emojis, numbers/times/dates said the way a person says them); "Keep it under 40 words unless the description asks for more" (Claude's call); and the JSON shape `{"narration_script": "<text>"}`.

- [ ] **Step 1: Write the failing tests**:
  - `test_announcement_prompt_carries_description_character_and_rules` — asserts each required phrase above is in the prompt.
  - `test_generate_announcement_words_returns_narration` — patch `_query_gemini_structured` to return `{"narration_script": "  Welcome home, Sam.  "}` with gemini creds → `"Welcome home, Sam."`.
  - `test_generate_announcement_words_empty_raises` — returns `{}` → `AIClientError`.
  - `test_image_api_sends_reference_images_and_aspect_ratio` — mock aioclient; assert payload parts `[{"text": ...}, {"inlineData": {"mimeType": "image/jpeg", "data": base64("abc")}}]` and `generationConfig["imageConfig"] == {"aspectRatio": "16:9"}`.
- [ ] **Step 2: Run** `python -m pytest -q -p no:cacheprovider tests/test_ai_client.py` — Expected: new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command — Expected: all pass.
- [ ] **Step 5: Commit** `feat(ai): announcement words and reference-image slides`.

---

### Task 4: Building one announcement

**Files:**
- Create: `custom_components/cast_studio/announcement_build.py`
- Modify: `custom_components/cast_studio/pronunciation.py` (add `build_household_lexicon`)
- Test: `tests/test_announcement_build.py`, `tests/test_pronunciation.py`

**Interfaces:**
- Consumes: Task 1 `Announcement`, `announcement_day`, `pick_build_persona`, `resolve_local_picture`, `settings_fingerprint`; Task 3 `async_generate_announcement_words`, `async_generate_ai_image`; existing `tts_client.async_synthesize_speech`, `generator.get_audio_duration`, `generator.FINAL_SLIDE_PAD_SECONDS`, `slide_renderer.render_slide`, `ffmpeg_runner.async_run_ffmpeg`, `build_slide_clip_command`, `segments.SEGMENT_AUDIO_ARGS`, `SEGMENT_CLIP_UNIFORM_ARGS`, `versioned_path`.
- Produces:
  - `pronunciation.build_household_lexicon(people) -> list[tuple[str, str]]` — every person's phonetic name and every person's pronunciation rows (later person wins a conflict), longest first.
  - `ANNOUNCEMENTS_DIR = "announcements"`, `SIDECAR = "build.json"`.
  - `announcement_dir(output_dir: str, announcement_id: str) -> Path` — raises `ValueError` if outside `output_dir` (reuse `segments._is_safe_subpath`).
  - `@dataclass(frozen=True) class AnnouncementBuild`: `fingerprint: str`, `build_day: date | None` (Daily only), `built_at: datetime`, `words: str`, `wording: str`, `persona_id: str`, `audio_path: str`, `audio_duration: float`, `clip_path: str | None`, `clip_duration: float | None`, `slide_path: str | None`, `status: Literal["ready", "fallback"]`, `note: str | None` (why it fell back).
  - `read_build(directory: Path) -> AnnouncementBuild | None` (None if the sidecar is missing/unreadable or its audio file is gone; paths stored relative to the folder) and `write_build(directory, build) -> None` (atomic, like `briefing._write_json`).
  - `class WordsFailedError(HomeAssistantError)`.
  - `async def async_build_announcement(hass, directory: Path, announcement, ai_creds, now: datetime, previous: AnnouncementBuild | None) -> AnnouncementBuild` — builds into a scratch dir, moves files in with versioned names, writes the sidecar, then removes files in `directory` referenced by neither the new nor the `previous` build.

Build steps (in order):
1. Persona = `pick_build_persona(announcement, previous.persona_id if previous else None)`.
2. Words: verbatim → message. Creative → `async_generate_announcement_words`; on any exception: with `previous` raise `WordsFailedError(str(err))`; without, use the message if non-blank else the name, and mark the build `fallback` with note "AI words unavailable".
3. Speech: `apply_pronunciations(words, build_household_lexicon(get_people(entry)))` (entry from `get_loaded_entry`; none → words unchanged) → `async_synthesize_speech(hass, text, persona.gemini_voice, persona.character, wav, creds, voice_profile=persona.delivery, openai_voice=persona.openai_voice)` → encode to `speech.mp3` with `SEGMENT_AUDIO_ARGS`; `audio_duration = get_audio_duration(wav, fallback_text=words)`.
4. Visual only: read pictures (first `MAX_PICTURES`, each resolved with `resolve_local_picture` in the executor; missing → `LOGGER.warning("Skipping picture %s of announcement '%s': not readable", ...)`), mime from suffix. `async_generate_ai_image(hass, prompt, scratch/"slide.png", creds, reference_images=..., aspect_ratio="16:9")`; the prompt states it is one 16:9 slide for a smart display, gives the description and the words, and says any text on it must be short and spelled exactly. None returned → `render_slide({"title": announcement.name, "subtitle": "", "image_path": first readable picture, "layout": "photo"} if a picture else {"title": announcement.name, "subtitle": ""}, scratch/"slide.jpg")`, build status `fallback`, note "AI slide unavailable".
5. Clip: `build_slide_clip_command(slide, wav, clip, duration=audio_duration + FINAL_SLIDE_PAD_SECONDS, pad_seconds=FINAL_SLIDE_PAD_SECONDS)` with `SEGMENT_CLIP_UNIFORM_ARGS` inserted before the output (as `segments._async_build_one` does); `clip_duration = audio_duration + FINAL_SLIDE_PAD_SECONDS`.
6. Any FFmpeg failure raises `HomeAssistantError`.

`build_day` = `announcement_day(announcement, now)` for Daily, None for Static.

- [ ] **Step 1: Write the failing tests** (patch TTS to write a WAV, FFmpeg to write the output path, AI functions as noted; `get_people` entry from conftest):
  - `test_household_lexicon_merges_everyones_rows` (in `test_pronunciation.py`).
  - `test_verbatim_visual_build_writes_files_and_sidecar` — ready status, words == message, `read_build` round-trips, audio/clip/slide exist in `directory`.
  - `test_audio_announcement_builds_no_slide` — no image call, `clip_path is None`, `slide_path is None`.
  - `test_creative_words_come_from_ai_with_persona` — words from the patched writer; persona passed is the configured one.
  - `test_creative_failure_with_previous_raises_words_failed`.
  - `test_creative_failure_without_previous_speaks_message_then_name` — status fallback, note set.
  - `test_no_ai_image_falls_back_to_plain_slide` (Review Focus 3) — `async_generate_ai_image` returns None; `render_slide` called with the name as title; status fallback.
  - `test_unreadable_picture_is_skipped` (Review Focus 4) — one existing and one deleted picture; one reference image sent; a warning logged.
  - `test_pictures_capped_at_four`.
  - `test_speech_uses_household_pronunciations` — Sam with phonetic "Sahm": TTS receives "Welcome home, Sahm".
  - `test_old_versions_removed_but_previous_kept` — three builds in a row: only files of builds 2 and 3 remain.
  - `test_daily_build_records_build_day`.
- [ ] **Step 2: Run** `python -m pytest -q -p no:cacheprovider tests/test_announcement_build.py tests/test_pronunciation.py` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command — Expected: all pass.
- [ ] **Step 5: Commit** `feat(announcements): build words, speech and slide`.

---

### Task 5: Announcement runtime, triggers and the rebuild action

**Files:**
- Create: `custom_components/cast_studio/announcement_runtime.py`
- Modify: `custom_components/cast_studio/__init__.py` (setup/unload, update listener, `rebuild_announcement` action), `services.yaml`, `strings.json`/`en.json` (`services.rebuild_announcement`)
- Test: `tests/test_announcement_runtime.py`

**Interfaces:**
- Consumes: Tasks 1 and 4.
- Produces:
  - `DATA_ANNOUNCEMENTS = "announcements"`; `SIGNAL_ANNOUNCEMENT_UPDATED = f"{DOMAIN}_announcement_updated"` (dispatched with the announcement id).
  - `@dataclass class AnnouncementState`: `build: AnnouncementBuild | None`, `status: Literal["idle","building","ready","error","fallback"]`, `error: str | None`.
  - `class AnnouncementManager`:
    - `__init__(hass, entry, output_dir: str)`
    - `async_setup() -> None` — reads every sidecar, sets states (`ready`/`fallback` from the build, `idle` without), registers daily timers, then schedules (background, not awaited) a build for each announcement where `needs_build` is true.
    - `async_sync() -> None` — called from the entry update listener: new/changed/removed announcements; removed ones get their pending build cancelled, timer removed, state dropped and folder deleted (Review Focus 1).
    - `needs_build(a, now) -> bool` — no build, fingerprint differs, or Daily with `build.build_day != announcement_day(a, now)`.
    - `async_rebuild(announcement_id) -> None` — runs one build now (serialized per announcement by an `asyncio.Lock`); returns when it finishes.
    - `async_ensure_built(announcement_id) -> AnnouncementBuild` — the current build if there is one (even if a rebuild is pending); otherwise waits for/runs a build; raises `HomeAssistantError` if none results.
    - `state(announcement_id) -> AnnouncementState | None`
    - `async_unload() -> None` — cancels timers and in-flight build tasks.
  - `get_manager(hass) -> AnnouncementManager` (raises `HomeAssistantError` when not set up).

A build: state → `building` (signal); `creds = await async_discover_ai_credentials(hass)`; `async_build_announcement(..., previous=state.build)`; success → state build = new, status = build.status, error None; `WordsFailedError`/any exception → keep the previous build, status `error`, error = message, `LOGGER.warning`. If the announcement was removed while building, discard the result and remove the folder. Signal after every change.

Daily timers: one `async_track_time_change(hass, cb, hour=, minute=, second=)` per Daily announcement at its rebuild time; the callback schedules a build when `needs_build` (so it is a no-op if a rebuild already happened). Re-registered by `async_sync` when the time or freshness changes.

`__init__.py`: `async_setup_entry` creates the manager after `briefing.async_setup_runtime`, stores it in `hass.data[DOMAIN][DATA_ANNOUNCEMENTS]`, calls `async_setup`, and registers `entry.async_on_unload(entry.add_update_listener(...))` whose callback awaits `manager.async_sync()`. `async_unload_entry` awaits `async_unload` and drops it. Action `cast_studio.rebuild_announcement` with field `announcement` (string, required): resolves with `find_announcement`, awaits `async_rebuild`. `services.yaml` entry with name "Rebuild Announcement" and description "Builds an announcement again now: new words for creative wording, and a new slide."

- [ ] **Step 1: Write the failing tests** (patch `async_build_announcement` with a fake that writes a real sidecar via `write_build` and records calls; use `freezer` and `async_fire_time_changed`):
  - `test_setup_builds_announcement_without_a_build`.
  - `test_restart_reuses_sidecar_without_building` — sidecar with the current fingerprint present → no build, state ready.
  - `test_settings_edit_rebuilds_only_that_announcement` — `hass.config_entries.async_update_subentry` on one of two → exactly one build.
  - `test_person_edit_rebuilds_nothing`.
  - `test_daily_rebuilds_at_rebuild_time` — move time to 03:00 next day → one build with the new day.
  - `test_daily_missed_while_down_rebuilds_once_at_startup` (Review Focus 5) — sidecar from yesterday → one build at setup; set up again → no second build.
  - `test_static_never_rebuilds_on_day_change`.
  - `test_failed_rebuild_keeps_previous_and_shows_error`.
  - `test_removed_while_building_discards_result` (Review Focus 1) — gate the fake build, remove the subentry, release the gate: state gone, folder gone, no exception logged.
  - `test_ensure_built_returns_previous_during_rebuild`.
  - `test_rebuild_action_rebuilds_by_name` and `test_rebuild_action_unknown_name_raises`.
- [ ] **Step 2: Run** `python -m pytest -q -p no:cacheprovider tests/test_announcement_runtime.py` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite `python -m pytest -q -p no:cacheprovider --timeout 60` — Expected: all pass.
- [ ] **Step 5: Commit** `feat(announcements): build triggers, daily rebuild and rebuild action`.

---

### Task 6: Image and button entities

**Files:**
- Create: `custom_components/cast_studio/image.py`, `custom_components/cast_studio/button.py`
- Modify: `custom_components/cast_studio/__init__.py` (`PLATFORMS` gains `Platform.IMAGE`, `Platform.BUTTON`), `announcement_runtime.py` (adds entities for announcements created after setup), `strings.json`/`en.json` if entity names use translation keys
- Test: `tests/test_announcement_entities.py`

**Interfaces:**
- Consumes: Task 5 manager, `SIGNAL_ANNOUNCEMENT_UPDATED`, `AnnouncementState`.
- Produces:
  - `CastStudioAnnouncementImage(ImageEntity)` — `entity_id = f"image.cast_studio_announcement_{slug}"`, unique id `f"cast_studio_announcement_{id}"`, name = announcement name; `image_last_updated = build.built_at`; `async_image()` returns the slide bytes (executor read) or None (Audio, or no build); `content_type` from the slide suffix; attributes `words`, `wording`, `freshness`, `persona` (persona name of the current build), `built_at`, `status`, `error`.
  - `CastStudioAnnouncementRebuildButton(ButtonEntity)` — `entity_id = f"button.cast_studio_rebuild_{slug}"`, unique id `f"cast_studio_announcement_rebuild_{id}"`, name "Rebuild"; `async_press` → `manager.async_rebuild(id)`.
  - Both share `device_info` identifiers `{(DOMAIN, f"announcement_{id}")}`, name = announcement name, and are added with `async_add_entities([...], config_subentry_id=id)` so removing the subentry removes them.
  - Each platform stores its add-callback in `hass.data[DOMAIN]` (`"announcement_add_image"`, `"announcement_add_button"`); `AnnouncementManager.async_sync` calls them for new announcements.

- [ ] **Step 1: Write the failing tests**:
  - `test_image_entity_shows_slide_and_attributes` — after a fake build, `hass.states.get("image.cast_studio_announcement_freezer_door")` has the attributes; the image endpoint (`async_get_image` from `homeassistant.components.image`) returns the slide bytes.
  - `test_audio_announcement_image_has_no_picture`.
  - `test_button_press_rebuilds`.
  - `test_new_announcement_gets_entities_without_reload` — add a subentry after setup → both entities exist.
  - `test_removed_announcement_loses_entities`.
- [ ] **Step 2: Run** `python -m pytest -q -p no:cacheprovider tests/test_announcement_entities.py` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite — Expected: all pass.
- [ ] **Step 5: Commit** `feat(announcements): slide image and rebuild button entities`.

---

### Task 7: Pausable briefing playback

**Files:**
- Modify: `custom_components/cast_studio/orchestrator.py`, `custom_components/cast_studio/briefing.py`
- Test: `tests/test_orchestrator.py`, `tests/test_briefing.py`

**Interfaces:**
- Produces:
  - `orchestrator.EXTRA_SECONDS_KEY = "_extra_seconds"` and `STARTED_AT_KEY = "_started_at"`. The lifecycle records `metadata[STARTED_AT_KEY] = loop.time()` when playback starts, and its duration wait becomes a loop: sleep until `duration + metadata.get(EXTRA_SECONDS_KEY, 0) - elapsed <= 0`, re-reading the key after each sleep.
  - `briefing._CurrentPlay` gains `player: str | None = None` (set in `_async_dispatch_play`) and `failed: str | None = None`.
  - `briefing.playing_on(hass, player: str) -> _CurrentPlay | None` — the current play on that player whose task is not done.
  - `briefing.async_fail_play(current: _CurrentPlay, reason: str) -> None` — sets `failed` and cancels the task; `_async_back_to_ready` treats a cancelled task with `failed` set as an error: logs a warning, sets the sensor `error`, skips the release.
  - `briefing.media_of(current) -> tuple[str, bool] | None` — the playing file path and whether it is video, from `current.metadata`.

- [ ] **Step 1: Write the failing tests**:
  - `test_lifecycle_waits_extra_seconds_added_during_playback` (orchestrator) — duration 10; at t=5 set `metadata[EXTRA_SECONDS_KEY] = 7`; post-routine actions run at t≈17, not 10.
  - `test_playing_on_returns_the_play_on_that_player` (briefing) — after `play("Quick", target=DISPLAY)`, `playing_on(hass, DISPLAY)` is the current play; `playing_on(hass, SPEAKER)` is None.
  - `test_fail_play_sets_error_and_skips_release`.
- [ ] **Step 2: Run** `python -m pytest -q -p no:cacheprovider tests/test_orchestrator.py tests/test_briefing.py` — Expected: new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite — Expected: all pass.
- [ ] **Step 5: Commit** `feat(briefing): playback that an announcement can pause`.

---

### Task 8: The announce action, with briefing pause and resume

**Files:**
- Create: `custom_components/cast_studio/announcement_play.py`
- Modify: `custom_components/cast_studio/__init__.py` (`announce` action), `services.yaml`, `strings.json`/`en.json`, `README.md` (Announcements section)
- Test: `tests/test_announcement_play.py`

**Interfaces:**
- Consumes: Task 5 `get_manager`, `async_ensure_built`; Task 7 `playing_on`, `async_fail_play`, `media_of`, `EXTRA_SECONDS_KEY`, `STARTED_AT_KEY`; existing `orchestrator.async_play_and_sync_routine`, `has_screen`, `_is_cast_player`, `PlaybackNotStartedError`; `briefing._async_release_player`.
- Produces: `async def async_announce(hass, entry, announcement: Announcement, target: str) -> None` — returns once the announcement is playing (or raises); the rest runs in a background task. Action `cast_studio.announce` with `announcement` (string, required) and `target` (media_player entity, required).

Flow, under a per-target `asyncio.Lock` (`hass.data[DOMAIN]["announce_locks"][target]`) held by the background task from start to finish so calls for one target play in order:
1. Target unavailable/unknown → `HomeAssistantError`. `build = await manager.async_ensure_built(id)`.
2. `briefing = playing_on(hass, target)`. If one: read the target state; position = `media_position + (now - media_position_updated_at)` when the state is `playing` and both attributes exist, else None (log a warning: resumes from the start). Record `paused_at = loop.time()`. Media = audio only. If none: video when `announcement.output_format == "visual"` and `has_screen(hass, target)` and `build.clip_path`, else audio; remember `woke = state in ("off", "idle", "standby", "unknown")`.
3. Play with `async_play_and_sync_routine(hass, f"announcement_{slug}", target, {"audio_path"|"video_path": path, "duration": build.audio_duration or clip_duration, "prepare_player": briefing is None}, [])`; set the caller's "started" future once the lifecycle reports playing (wait on a state listener for `_is_routine_media_playing`, or the lifecycle's own safety timeout → `PlaybackNotStartedError` → propagate to the caller); await the lifecycle task.
4. Resume, only if the briefing is still `playing_on(hass, target)` and is the same object (Review Focus 2): play its media again with `async_play_and_sync_routine(..., {..., "duration": 0, "prepare_player": False}, [])`, await it, then `media_player.media_seek` to `position` when known. Extend the briefing: `metadata[EXTRA_SECONDS_KEY] += (loop.time() - paused_at) + (elapsed_at_pause - (position or 0))`, where `elapsed_at_pause = paused_at - metadata[STARTED_AT_KEY]`. Any exception while resuming → `async_fail_play(briefing, str(err))`.
5. No briefing and `woke` and Cast player → `_async_release_player(hass, target)`.

README: new "Announcements" section — what static and daily mean, the form fields, the image entity and rebuild button, the two actions with YAML examples using `media_player.kitchen_speaker`, and the briefing pause/resume behaviour.

- [ ] **Step 1: Write the failing tests** (fake player states and service calls like `test_briefing.py`'s harness; fake manager build with known durations):
  - `test_announce_on_screen_plays_video` and `test_announce_on_speaker_plays_audio` and `test_audio_announcement_on_screen_plays_audio`.
  - `test_announce_builds_first_when_never_built`.
  - `test_announce_returns_once_playing_and_releases_woken_cast_player`.
  - `test_announce_does_not_release_player_already_in_use`.
  - `test_announce_pauses_and_resumes_briefing_at_position` — briefing playing on DISPLAY at position 30 s: play_media calls are briefing video, announcement audio, briefing video again; then `media_seek` with 30; briefing post-actions fire at the extended time.
  - `test_unknown_position_resumes_from_start` — no seek call; warning logged.
  - `test_no_resume_when_briefing_superseded` (Review Focus 2) — a new play on another player supersedes the briefing during the announcement → no re-cast.
  - `test_failed_resume_sets_briefing_error`.
  - `test_two_announcements_on_one_target_play_in_order` — the second call returns only after the first finished and the second started.
  - `test_announce_unknown_announcement_raises` and `test_announce_unavailable_target_raises`.
- [ ] **Step 2: Run** `python -m pytest -q -p no:cacheprovider tests/test_announcement_play.py` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite, `ruff check custom_components tests`, `ruff format --check custom_components tests` — Expected: all pass, no lint errors.
- [ ] **Step 5: Commit** `feat(announcements): announce action with briefing pause and resume`.
