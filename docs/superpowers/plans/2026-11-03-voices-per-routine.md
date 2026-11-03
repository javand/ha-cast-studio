# One Default Voice, Voice Changes per Routine: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the host/reader persona split with one default voice per person, and let each routine give individual segments a different voice.

**Architecture:** Personas lose their roles. The persona that voices a segment becomes part of its storage key (`weather--theo`), so a segment is built once per persona per briefing day and every routine asking for the same (segment, persona) shares it; the build/play/refresh code keeps working on keys. A routine stores its voice changes as `[{segment, personas}]`, edited in an Advanced section of the routine form.

**Tech Stack:** Python 3.13, Home Assistant 2026.2 config subentries and selectors, pytest (`pytest-homeassistant-custom-component`), ruff.

**Spec:** `docs/superpowers/specs/2026-11-03-voices-per-routine-design.md`

## Global Constraints

- Work on branch `feat/person-form-cleanup`; ship with it as one PR at `manifest.json` version `0.7.0`.
- `strings.json` and `translations/en.json` must stay identical.
- Persona ids never change (`default_host` is referenced by announcements and stored data); only display names change.
- The daily text (`scripture`) and the "That's everything" line are read word for word by every persona.
- Only two person profiles exist; reading the old `host_personas` key as a fallback is the whole migration. `reader_personas` is ignored.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Run `python -m pytest tests -q`, `ruff check custom_components tests` and `ruff format --check custom_components tests` before each commit.

## Review Focus

1. A day folder built before this change (manifest keys without a persona, e.g. `weather`) is loaded on the same day: play must build the new keys, not crash or replay the old file.
2. A voice change (or a person's voice) naming a persona id no longer in the catalogue falls back to the person's voice, then to `default_host`.
3. The daily text voiced by a voice-change persona is still read verbatim (no writer call when the sensor has text).
4. Two routines listing the same personas in a different order, or a voice change identical to the person's voice, resolve to the same key on the same day.
5. Duplicate voice-change rows for one segment: the last row wins; rows with no personas are dropped.

---

### Task 1: One voice per person; personas without roles

**Files:**
- Modify: `custom_components/cast_studio/personas.py`
- Modify: `custom_components/cast_studio/people.py`
- Modify: `custom_components/cast_studio/segments.py` (`_ROLE_SETTINGS`, `_role_voice`, `build_segment_step`)
- Modify: `custom_components/cast_studio/config_flow.py` (`_persona_selector`, `_person_settings_selectors`, `_person_schema`)
- Modify: `custom_components/cast_studio/strings.json`, `translations/en.json` (person `user` and `reconfigure` advanced section)
- Test: `tests/test_personas.py`, `tests/test_people.py`, `tests/test_segments.py`, `tests/test_config_flow.py`

**Interfaces:**
- Produces (`personas.py`): `Persona` without `roles`; `DEFAULT_VOICE_PERSONA = "default_host"`; `pick_persona(persona_ids: Sequence[str] | None, person_id: str, day: date) -> Persona | None`. Removed: `ROLE_HOST`, `ROLE_READER`, `HOST`/`READER`/`BOTH`, `personas_for_role`, `DEFAULT_HOST_PERSONA`, `DEFAULT_READER_PERSONA`.
- Produces (`people.py`): `PERSON_VOICE = "voice_personas"`, `PERSON_LEGACY_HOST_PERSONAS = "host_personas"`, `person_voice(settings: Mapping[str, Any]) -> list[str]`. `PERSON_SETTINGS_KEYS` contains `PERSON_VOICE` and no longer `host_personas`/`reader_personas`.
- Produces (`segments.py`): `persona_voice(persona: Persona) -> dict[str, Any]` (the step keys `voice`, `voice_personality`, `voice_profile`, `openai_voice`), `person_default_persona(person: Person, day: date) -> Persona`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_personas.py
def test_pick_ignores_list_order():
    a = pick_persona(["theo", "arthur", "ray"], "p1", DAY)
    b = pick_persona(["ray", "theo", "arthur"], "p1", DAY)
    assert a == b

def test_pick_skips_unknown_ids_and_returns_none_when_empty():
    assert pick_persona(["gone"], "p1", DAY) is None
    assert pick_persona(["gone", "theo"], "p1", DAY).persona_id == "theo"

def test_defaults_renamed():
    assert get_persona("default_host").name == "News Anchor"
    assert get_persona("default_reader").name == "Life Coach"

# tests/test_people.py
@pytest.mark.parametrize(("settings", "expected"), [
    ({"voice_personas": ["theo"]}, ["theo"]),
    ({"host_personas": ["elena"]}, ["elena"]),
    ({"voice_personas": ["theo"], "host_personas": ["elena"]}, ["theo"]),
    ({"reader_personas": ["sage"]}, []),
    ({}, []),
])
def test_person_voice_reads_new_and_legacy_keys(settings, expected):
    assert person_voice(settings) == expected

# tests/test_segments.py
def test_every_segment_uses_the_person_voice():
    person = _with_settings(voice_personas=["theo"])
    for key in ("greeting", "weather", "scripture", "daily_encouragement"):
        assert build_segment_step(person, key, PERSONA_DAY)["voice"] == "Puck"

def test_no_voice_means_news_anchor():
    step = build_segment_step(_with_settings(), "scripture", PERSONA_DAY)
    assert step["voice"] == "Pulcherrima"
    assert step["voice_profile"] == get_persona("default_host").delivery

def test_legacy_host_persona_becomes_the_voice():
    step = build_segment_step(_with_settings(host_personas=["elena"]), "weather", PERSONA_DAY)
    assert step["voice"] == "Kore"

def test_unknown_voice_ids_fall_back_to_news_anchor():
    step = build_segment_step(_with_settings(voice_personas=["gone"]), "weather", PERSONA_DAY)
    assert step["voice"] == "Pulcherrima"

# tests/test_config_flow.py
async def test_person_form_has_one_voice_picker(hass, config_entry):
    result = await _start_subentry(hass, config_entry, "person")
    selectors = _advanced_selectors(result["data_schema"])
    assert "voice_personas" in selectors
    assert "host_personas" not in selectors and "reader_personas" not in selectors
    assert {"malenfant", "theo", "default_reader"} <= _option_values(selectors["voice_personas"])
    assert _advanced_suggestions(result["data_schema"])["voice_personas"] == ["default_host"]
```

Also update or delete the existing tests that pin the host/reader split (find them with `grep -rn "host_personas\|reader_personas\|ROLE_\|personas_for_role\|roles" tests`), including the strings test `test_persona_help_text_names_defaults_and_segments`: replace it with one asserting the `voice_personas` description in both files equals the copy in Step 3.

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m pytest tests -q`
Expected: the new tests fail (ImportError for `person_voice`, missing `voice_personas`, wrong voice for `scripture`).

- [ ] **Step 3: Implement**

- `personas.py`: drop `roles` and role constants; rename `default_host` to name `"News Anchor"`, summary `"Polished, clear and steady on air"`; `default_reader` to `"Life Coach"`, summary `"Calm, warm and encouraging"`. `pick_persona` keeps only known ids, sorts them, seeds with `f"{person_id}:{day.isoformat()}:{','.join(valid)}"` (so the pick depends on the person, the day and the set, nothing else).
- `people.py`: `person_voice` returns `settings.get(PERSON_VOICE)` if non-empty, else `settings.get(PERSON_LEGACY_HOST_PERSONAS)`, else `[]`, as a list of strings.
- `segments.py`: `person_default_persona` = `pick_persona(person_voice(person.settings), person.person_id, day)` or `get_persona(DEFAULT_VOICE_PERSONA)`. `build_segment_step` uses it for every segment type (no reader branch). Delete `_ROLE_SETTINGS`, `_role_voice` and the now-unused personality defaults.
- `config_flow.py`: `_persona_selector()` lists all `PERSONAS` with `"Name: summary"` labels; the person settings have one `PERSON_VOICE` picker in place of the two; `_person_schema` suggests `[pid for pid in person_voice(current) if get_persona(pid)] or [DEFAULT_VOICE_PERSONA]`. The announcement form keeps using `_persona_selector()`.
- Strings (person `user` and `reconfigure`, `sections.advanced`): remove `host_personas`/`reader_personas` from `data` and `data_description`; add `data.voice_personas = "Voice"` and `data_description.voice_personas = "Who speaks every segment of this person's routines. Pick several to get a different one each day. A routine can give individual segments a different voice under Advanced."`

- [ ] **Step 4: Run tests, lint, format**

Run: `python -m pytest tests -q && ruff check custom_components tests && ruff format --check custom_components tests`
Expected: all pass.

- [ ] **Step 5: Commit** `feat(voice): one voice per person; personas without host/reader roles`

---

### Task 2: Voice changes in routines, keyed storage

**Files:**
- Modify: `custom_components/cast_studio/people.py` (`Routine`, `_routine_from_subentry`)
- Modify: `custom_components/cast_studio/segments.py` (`segment_type_of`, `routine_segment_keys`, `build_segment_step`)
- Modify: `custom_components/cast_studio/briefing.py` (every `routine_segment_keys` / `_dynamic_keys` call; `_ready_keys`)
- Test: `tests/test_people.py`, `tests/test_segments.py`, `tests/test_briefing.py`, `tests/test_generator.py`

**Interfaces:**
- Consumes: `pick_persona`, `person_default_persona`, `persona_voice`, `get_persona`, `DEFAULT_VOICE_PERSONA` (Task 1).
- Produces (`people.py`): `ROUTINE_VOICE_CHANGES = "voice_changes"`; `Routine.voice_changes: tuple[tuple[str, tuple[str, ...]], ...] = ()` (segment type, persona ids).
- Produces (`segments.py`): `PERSONA_SEPARATOR = "--"`; `voiced_key(key: str, persona_id: str) -> str`; `persona_of(key: str) -> str | None`; `unvoiced_key(key: str) -> str`; `segment_persona(person: Person, routine: Routine | None, segment_type: str, day: date) -> Persona`; `routine_segment_keys(person: Person, routine: Routine, day: date) -> list[str]` (new signature; returns voiced keys). `segment_type_of` strips the persona, then `_nosignoff`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_people.py
def test_routine_voice_changes_parsed_and_cleaned():
    entry = make_entry([person_subentry(SAM_ID, "Sam"), routine_subentry(
        "r1", SAM_ID, "Quick", ["weather", "scripture"],
        voice_changes=[
            {"segment": "scripture", "personas": ["sage"]},
            {"segment": "scripture", "personas": ["malenfant"]},  # last wins
            {"segment": "agenda", "personas": ["theo"]},           # not in routine
            {"segment": "weather", "personas": []},                # empty
        ])])
    (routine,) = get_routines(entry)
    assert routine.voice_changes == (("scripture", ("malenfant",)),)

# tests/test_segments.py
def test_keys_carry_the_persona(): ...
    # routine ["weather", "scripture"], no changes, person voice ["theo"]
    assert routine_segment_keys(person, routine, PERSONA_DAY) == ["weather--theo", "scripture--theo"]

def test_voice_change_only_changes_its_segment():
    # voice_changes (("scripture", ("malenfant",)),)
    assert routine_segment_keys(person, routine, PERSONA_DAY) == ["weather--theo", "scripture--malenfant"]

def test_change_equal_to_person_voice_shares_the_key():
    # person voice ["theo", "ray"]; routine A no change; routine B change weather -> ["ray", "theo"]
    assert routine_segment_keys(person, a, DAY)[0] == routine_segment_keys(person, b, DAY)[0]

def test_unknown_change_persona_falls_back_to_person_voice():
    # change weather -> ["gone"]; person voice ["theo"]
    assert routine_segment_keys(person, routine, DAY)[0] == "weather--theo"

def test_segment_type_of_strips_persona_and_signoff():
    assert segment_type_of("daily_encouragement_nosignoff--nana_rose") == "daily_encouragement"
    assert unvoiced_key("daily_encouragement_nosignoff--nana_rose") == "daily_encouragement_nosignoff"

def test_build_step_uses_the_key_persona():
    step = build_segment_step(person, "scripture--malenfant", PERSONA_DAY)
    assert step["voice"] == "Charon" and step["type"] == "scripture"
    assert build_segment_step(person, "weather--gone", PERSONA_DAY)["voice"] == "Puck"  # person voice theo
    assert build_segment_step(person, "closing", PERSONA_DAY)["voice"] == "Puck"         # unvoiced = person voice

# tests/test_briefing.py
async def test_prebuild_builds_each_persona_variant_once(...):
    # Sam voice ["theo"]; routine A [weather, scripture]; routine B [weather] with weather -> ["arthur"]
    # patch segments.async_build_segments / briefing.async_build_segments, capture keys
    assert captured_keys == ["weather--theo", "scripture--theo", "weather--arthur"]

async def test_play_rebuilds_a_day_built_before_voices(...):
    # day folder manifest has unvoiced "weather" fresh for today; play routine [weather]
    # expect a build of "weather--default_host" and the played file is that build's

async def test_refresh_rebuilds_every_persona_variant(...):
    # same routines as above; refresh person -> keys == ["weather--theo", "weather--arthur"]

# tests/test_generator.py
async def test_verbatim_scripture_holds_for_a_persona(...):
    # copy test_async_compile_single_step_verbatim_scripture_bypasses_llm, add the
    # step keys from persona_voice(get_persona("theo")); assert the LLM is not called
    # and the TTS narration is still the verbatim daily text.
```

Use the existing fixtures and patch targets in each test file; the bodies marked `...` follow the shape of their neighbours.

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m pytest tests -q`
Expected: the new tests fail (`voice_changes` unknown field, `routine_segment_keys` takes one argument, unvoiced keys).

- [ ] **Step 3: Implement**

- `people.py`: parse `voice_changes` into `Routine.voice_changes`: keep rows whose `segment` is in `routine.segments` and whose `personas` is non-empty; a later row for the same segment replaces an earlier one; preserve routine segment order.
- `segments.py`: `segment_persona` picks from the routine's voice change for that segment type if any (`pick_persona(ids, person.person_id, day)`), falling back to `person_default_persona`. `routine_segment_keys(person, routine, day)` maps each existing key to `voiced_key(key, segment_persona(...).persona_id)`. `build_segment_step` resolves the persona from `persona_of(key)` via `get_persona`, falling back to `person_default_persona` when the suffix is missing or unknown, and works on `unvoiced_key(key)` for everything else. `_all_heard_step` is unchanged (unvoiced `closing` → person voice).
- `briefing.py`: pass `person` and `briefing_day(person, now)` to every `routine_segment_keys` call and to `_dynamic_keys`; `_ready_keys` reports `unvoiced_key(key)` so the sensor's `ready_segments` attribute keeps today's values.

- [ ] **Step 4: Run tests, lint, format** (as Task 1 Step 4)

- [ ] **Step 5: Commit** `feat(voice): routines change a segment's voice; storage keyed by persona`

---

### Task 3: Voice changes in the routine form

**Files:**
- Modify: `custom_components/cast_studio/config_flow.py` (`RoutineSubentryFlow._async_step_form`, `_routine_data`, `_routine_schema`)
- Modify: `custom_components/cast_studio/strings.json`, `translations/en.json`
- Test: `tests/test_config_flow.py`

**Interfaces:**
- Consumes: `ROUTINE_VOICE_CHANGES` (Task 2), `_persona_selector()` (Task 1), `SEGMENT_TYPES`.
- Produces: routine subentry data key `voice_changes`: `list[{"segment": str, "personas": list[str]}]`, stored flat (no `advanced` key), omitted rows with no personas.

- [ ] **Step 1: Write the failing tests**

```python
async def test_edit_routine_lists_only_its_segments(hass, entry_with_routine):
    # routine segments ["greeting", "weather", "daily_encouragement", "closing"]
    result = await entry.start_subentry_reconfigure_flow(hass, ROUTINE_ID)
    row = _advanced_selectors(result["data_schema"])["voice_changes"]
    assert [o for o in row.config["fields"]["segment"]["selector"]["select"]["options"]] == [
        "greeting", "weather", "daily_encouragement", "closing"]

async def test_add_routine_lists_every_segment(hass, config_entry_with_person):
    # options == list(SEGMENT_TYPES)

async def test_add_routine_rejects_change_for_unpicked_segment(...):
    # segments ["greeting"], advanced.voice_changes [{"segment": "weather", "personas": ["theo"]}]
    assert result["errors"] == {"base": "voice_change_not_in_routine"}

async def test_edit_routine_drops_change_for_removed_segment(...):
    # stored change for "weather"; submit segments without "weather" and the row still present
    assert "voice_changes" not in subentry.data or subentry.data["voice_changes"] == []

async def test_routine_voice_changes_saved_flat(...):
    # submit advanced.voice_changes [{"segment": "scripture", "personas": ["malenfant"]}]
    assert subentry.data["voice_changes"] == [{"segment": "scripture", "personas": ["malenfant"]}]
    assert "advanced" not in subentry.data

def test_routine_voice_change_strings():
    # both files, user and reconfigure: sections.advanced.data.voice_changes == "Voice changes",
    # data_description.voice_changes contains "word for word";
    # error voice_change_not_in_routine present; selector.voice_changes.fields.segment/personas named
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m pytest tests/test_config_flow.py -q`
Expected: the new tests fail (no `advanced` section on the routine form).

- [ ] **Step 3: Implement**

- `_routine_schema(people, current, dashboards, segment_choices: list[str])` adds `vol.Required("advanced"): section(vol.Schema({vol.Optional(ROUTINE_VOICE_CHANGES, description=_suggested(...)): ObjectSelector(multiple=True, label_field="segment", translation_key="voice_changes", fields={"segment": {"selector": {"select": {"options": segment_choices, "translation_key": "segment_type"}}, "required": True}, "personas": {"selector": {"select": {"options": <persona options>, "multiple": True}}, "required": True}})}), {"collapsed": True})`. Reuse the person form's section pattern.
- `_async_step_form`: `segment_choices` = the saved subentry's segments when editing, else `list(SEGMENT_TYPES)` (unchanged across error re-shows). On submit, rows naming a segment not in the submitted segments: adding → `errors["base"] = "voice_change_not_in_routine"`; editing → drop the rows.
- `_routine_data` flattens `advanced.voice_changes` into `ROUTINE_VOICE_CHANGES`, keeping rows with a segment and at least one persona.
- Strings for routine `user` and `reconfigure`: `sections.advanced = {"name": "Advanced", "data": {"voice_changes": "Voice changes"}, "data_description": {"voice_changes": "Give a segment of this routine a different voice. Segments not listed use the person's voice. Pick several personas to get a different one each day. The daily text is always read word for word; a persona only changes how it sounds."}}`; `error.voice_change_not_in_routine = "A voice change names a segment that isn't in this routine. Add the segment or remove the voice change."`; `selector.voice_changes.fields = {"segment": {"name": "Segment"}, "personas": {"name": "Voice", "description": "Pick several to get a different one each day."}}`.

- [ ] **Step 4: Run tests, lint, format** (as Task 1 Step 4)

- [ ] **Step 5: Commit** `feat(routine): voice changes per segment in the routine form`

---

### Task 4: Docs, deploy and check on Home Assistant

**Files:**
- Modify: `README.md` (Personas section and table), `SPEC.md` (person and routine fields)

- [ ] **Step 1: Update docs.** README: replace the host/reader explanation and the table's Host/Reader columns with one **Voice** setting on the person and a **Voice changes** paragraph for routines (including "the daily text is always read word for word"); table columns `Persona | Style | Voice`, with the renamed News Anchor and Life Coach. SPEC: person `voice_personas` (fallback `host_personas`; `reader_personas` ignored); routine `voice_changes` and the `<segment>--<persona>` storage key.
- [ ] **Step 2: Full check.** `python -m pytest tests -q && ruff check custom_components tests && ruff format --check custom_components tests`, all pass. Commit `docs: one voice per person, voice changes per routine`.
- [ ] **Step 3: Deploy** with `rsync -rt --inplace --exclude __pycache__ custom_components/cast_studio/ /Volumes/config/custom_components/cast_studio/`, restart HA, wait until `__pycache__/segments*.pyc` is newer than `segments.py`.
- [ ] **Step 4: Verify.** Press `button.cast_studio_rebuild_javan_morning_display`; the day's `manifest.json` gains keys ending `--default_host` with `degraded: false`, and `routine_morning_display.json`'s segment map matches them. Ask the owner to check the person form (one **Voice** picker) and a routine's **Advanced → Voice changes**.

---

### Task 5: Delete superseded files within the day

Added during execution at the owner's request ("make sure we overwrite and cleanup when it makes sense").

**Files:**
- Modify: `custom_components/cast_studio/segments.py` (new `_remove_superseded_day_files`, called from `SegmentStore.async_prune`)
- Test: `tests/test_segments.py`

**Interfaces:**
- Produces: `_remove_superseded_day_files(directory: Path, now: datetime) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_superseded_day_files_removed_after_grace(tmp_path):
    # day dir with manifest.json pointing at weather--theo.new.mp3, routine_x.json pointing at
    # routine_x.new.mp4, plus weather--theo.old.mp3, routine_x.old.mp4, play_x.old.mp3,
    # all files (and the JSONs) with mtime now - 2h
    removed = _remove_superseded_day_files(day, now)
    assert sorted(Path(p).name for p in removed) == ["play_x.old.mp3", "routine_x.old.mp4", "weather--theo.old.mp3"]
    assert (day / "weather--theo.new.mp3").exists() and (day / "routine_x.new.mp4").exists()

def test_superseded_day_files_kept_within_grace(tmp_path):
    # same layout, but manifest.json mtime is now - 10min (a recent commit may have just
    # superseded a file that a play is still streaming)
    assert _remove_superseded_day_files(day, now) == []
```

- [ ] **Step 2: Run them and confirm they fail** — ImportError for `_remove_superseded_day_files`.
- [ ] **Step 3: Implement.** A file is kept if its name appears as a path in `manifest.json` or any other `*.json` in the folder (routine sidecars), or is a `.json`. Otherwise it is deleted only when its own mtime and the newest `*.json` mtime are both older than `ALL_HEARD_KEEP_SUPERSEDED` (1 h): nothing was superseded within the hour, so no play can still be streaming it. `async_prune` runs it on today's folder.
- [ ] **Step 4: Run tests, lint, format** (as Task 1 Step 4)
- [ ] **Step 5: Commit** `feat(storage): delete superseded files within the day`
