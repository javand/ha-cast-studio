# ha-automation-generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, test, and package the `ha-automation-generator` custom integration and automation blueprints to enable declarative, multi-routine media generation (1024x600 MP4) with zero-credential AI discovery, decoupled pre-generation, concurrent background actions, and synchronized Google Cast playback.

**Architecture:** The integration parses routine configurations from `configuration.yaml` or UI blueprints, offloads background device actions concurrently via `asyncio.gather`, silently pre-compiles multimedia slides via Pillow, synthesizes per-action dual-voice narration with phonetic pronunciation, and concatenates clips using local FFmpeg. An orchestrator coordinates Google Cast playback state tracking to fire timed display actions with zero buffer drift.

**Tech Stack:** Python 3.12+, Home Assistant Core integration architecture, Pillow (PIL), aiohttp, FFmpeg CLI, pytest-homeassistant-custom-component.

**Spec:** [`docs/superpowers/specs/2026-10-17-ha-automation-generator-design.md`](file:///Users/javan/Projects/ha-automation-generator/docs/superpowers/specs/2026-10-17-ha-automation-generator-design.md)

## Global Constraints
- **Platform:** Home Assistant 2024.6.0 or newer.
- **Integration Domain:** `automation_generator`.
- **Codeowner:** Javan Dicks (`@javand`).
- **Resolution:** 1024x600 pixels (16:9 aspect ratio) for smart displays.
- **Storage Policy:** Target media files (`/config/www/automations/<routine>.mp4`) overwrite by default; scratchpads in `/tmp` purge immediately; files >24h prune automatically.
- **Identity Contract:** Display real name (`name`) on visual cards; inject phonetic spelling (`phonetic_name`) exclusively into TTS prompts.
- **Async Safety:** All blocking I/O (PIL rendering, FFmpeg, disk operations) must run inside `await hass.async_add_executor_job(...)`.

## Review Focus
1. **Existing Google AI credential discovery failure:** When `hass.config_entries.async_entries("google_generative_ai")` is empty and no key is provided, system must fallback cleanly to `conversation.process` without crashing.
2. **Phonetic name leakage onto display:** The visual slide renderer must assert that phonetic spellings (e.g. `Jay-ven`) never appear in rendered text cards.
3. **Google Cast buffering drift:** When Cast displays take >3s to buffer, `actions_on_display` must not fire until the player reports `playing`.
4. **Intermediate storage leaks:** If FFmpeg concatenation fails mid-stream, scratchpad files in `/tmp` must still be cleaned up in a `finally` block.
5. **Concurrent background action errors:** If a non-visual background action raises an exception during `asyncio.gather`, it must log an error and allow media generation/playback to continue rather than aborting the entire routine.

---

### Task 1: Scaffolding, Domain Constants, and Manifest Configuration

**Files:**
- Modify: `custom_components/automation_generator/manifest.json`
- Modify: `custom_components/automation_generator/const.py`
- Modify: `custom_components/automation_generator/__init__.py`
- Create: `tests/test_scaffolding.py`

**Interfaces:**
- Consumes: None (root integration setup)
- Produces:
  - `DOMAIN = "automation_generator"` in `const.py`
  - `DEFAULT_OUTPUT_DIR = "/config/www/automations"` in `const.py`
  - `DEFAULT_HOST_VOICE = "Pulcherrima"`, `DEFAULT_SCRIPTURE_VOICE = "Enceladus"` in `const.py`
  - `async_setup(hass, config)` and `async_setup_entry(hass, entry)` in `__init__.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scaffolding.py
import json
import os
from custom_components.automation_generator.const import (
    DOMAIN,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_HOST_VOICE,
    DEFAULT_SCRIPTURE_VOICE,
)

def test_manifest_metadata():
    manifest_path = os.path.join(
        os.path.dirname(__file__), "..", "custom_components", "automation_generator", "manifest.json"
    )
    with open(manifest_path) as f:
        manifest = json.load(f)
    assert manifest["domain"] == "automation_generator"
    assert manifest["name"] == "Automation Generator"
    assert "@javand" in manifest["codeowners"]
    assert "ffmpeg" in manifest["dependencies"]
    assert any("pillow" in req.lower() for req in manifest["requirements"])

def test_constants():
    assert DOMAIN == "automation_generator"
    assert DEFAULT_OUTPUT_DIR == "/config/www/automations"
    assert DEFAULT_HOST_VOICE == "Pulcherrima"
    assert DEFAULT_SCRIPTURE_VOICE == "Enceladus"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scaffolding.py -v`  
Expected: FAIL with missing constants or manifest assertions

- [ ] **Step 3: Implement minimal code in `const.py` and `manifest.json`**

Update `const.py` with `DOMAIN`, `DEFAULT_OUTPUT_DIR`, `DEFAULT_HOST_VOICE`, `DEFAULT_SCRIPTURE_VOICE`. Ensure `manifest.json` has `ffmpeg` dependency and `pillow>=10.2.0` requirement.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scaffolding.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/automation_generator/ tests/test_scaffolding.py
git commit -m "feat(scaffolding): configure domain constants and integration manifest"
```

---

### Task 2: Storage Manager (Scratchpad Cleanup, Overwrite, & Retention Pruning)

**Files:**
- Create: `custom_components/automation_generator/storage_manager.py`
- Test: `tests/test_storage_manager.py`

**Interfaces:**
- Consumes: `DEFAULT_OUTPUT_DIR` from `const.py`
- Produces:
  - `get_routine_scratch_dir(routine_name: str) -> str`
  - `cleanup_scratch_dir(routine_name: str) -> None`
  - `prune_stale_files(directory: str, max_age_seconds: int = 86400) -> list[str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_storage_manager.py
import os
import time
from custom_components.automation_generator.storage_manager import (
    get_routine_scratch_dir,
    cleanup_scratch_dir,
    prune_stale_files,
)

def test_scratch_dir_lifecycle(tmp_path):
    scratch_dir = get_routine_scratch_dir("morning", base_tmp=str(tmp_path))
    assert os.path.exists(scratch_dir)
    test_file = os.path.join(scratch_dir, "clip_01.mp4")
    with open(test_file, "w") as f:
        f.write("dummy clip")
    assert os.path.exists(test_file)
    
    cleanup_scratch_dir("morning", base_tmp=str(tmp_path))
    assert not os.path.exists(scratch_dir)

def test_prune_stale_files(tmp_path):
    old_file = tmp_path / "old_routine.mp4"
    new_file = tmp_path / "new_routine.mp4"
    old_file.write_text("old")
    new_file.write_text("new")
    
    # Backdate old_file by 25 hours
    past_time = time.time() - 90000
    os.utime(str(old_file), (past_time, past_time))
    
    pruned = prune_stale_files(str(tmp_path), max_age_seconds=86400)
    assert str(old_file) in pruned
    assert not os.path.exists(str(old_file))
    assert os.path.exists(str(new_file))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_storage_manager.py -v`  
Expected: FAIL with `storage_manager` not found

- [ ] **Step 3: Implement `storage_manager.py`**

Implement scratch directory creation, recursive deletion in `cleanup_scratch_dir`, and timestamp inspection in `prune_stale_files`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_storage_manager.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/automation_generator/storage_manager.py tests/test_storage_manager.py
git commit -m "feat(storage): implement scratchpad cleanup and 24h retention pruning"
```

---

### Task 3: Zero-Credential AI Auto-Detection & Content Formatting

**Files:**
- Create: `custom_components/automation_generator/ai_client.py`
- Test: `tests/test_ai_client.py`

**Interfaces:**
- Consumes: Home Assistant state machine and config entries
- Produces:
  - `async_discover_ai_credentials(hass: HomeAssistant) -> dict[str, str]`
  - `async_generate_step_content(hass: HomeAssistant, step_config: dict, recipient: dict, ai_creds: dict) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ai_client.py
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from custom_components.automation_generator.ai_client import (
    async_discover_ai_credentials,
    async_generate_step_content,
)

@pytest.mark.asyncio
async def test_auto_detect_google_genai_credentials():
    hass = MagicMock()
    mock_entry = MagicMock()
    mock_entry.domain = "google_generative_ai"
    mock_entry.data = {"api_key": "discovered_secret_key"}
    hass.config_entries.async_entries.return_value = [mock_entry]

    creds = await async_discover_ai_credentials(hass)
    assert creds["platform"] == "gemini"
    assert creds["api_key"] == "discovered_secret_key"

@pytest.mark.asyncio
async def test_fallback_to_conversation_agent_when_no_key():
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = []

    creds = await async_discover_ai_credentials(hass)
    assert creds["platform"] == "conversation_agent"
    assert creds["api_key"] is None

@pytest.mark.asyncio
async def test_content_generation_separates_real_name_and_phonetic():
    hass = MagicMock()
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}
    step_config = {"name": "greeting", "type": "greeting"}
    
    mock_response = {
        "visual_card": {"title": "Good Morning, Javan!", "subtitle": "Saturday, October 17"},
        "narration_script": "Good morning, Jay-ven! Welcome to your briefing."
    }
    
    with patch("custom_components.automation_generator.ai_client._query_gemini_structured", new=AsyncMock(return_value=mock_response)):
        content = await async_generate_step_content(hass, step_config, recipient, {"platform": "gemini", "api_key": "fake"})
        assert content["visual_card"]["title"] == "Good Morning, Javan!"
        assert "Jay-ven" not in content["visual_card"]["title"]
        assert "Jay-ven" in content["narration_script"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ai_client.py -v`  
Expected: FAIL with `ai_client` not found

- [ ] **Step 3: Implement `ai_client.py`**

Implement `async_discover_ai_credentials` checking `hass.config_entries.async_entries("google_generative_ai")` first, and `async_generate_step_content` calling either Gemini API or `conversation.process` with JSON schema enforcement.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ai_client.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/automation_generator/ai_client.py tests/test_ai_client.py
git commit -m "feat(ai): add zero-credential discovery and structured content generation"
```

---

### Task 4: Pluggable TTS Client with Per-Action Voice & Personality

**Files:**
- Create: `custom_components/automation_generator/tts_client.py`
- Test: `tests/test_tts_client.py`

**Interfaces:**
- Consumes: Narration script, voice name, voice personality, AI credentials
- Produces:
  - `async_synthesize_speech(hass: HomeAssistant, script: str, voice: str, personality: str, output_wav_path: str, creds: dict) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tts_client.py
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from custom_components.automation_generator.tts_client import async_synthesize_speech

@pytest.mark.asyncio
async def test_synthesize_speech_gemini_voices(tmp_path):
    hass = MagicMock()
    output_wav = str(tmp_path / "slide_01.wav")
    creds = {"platform": "gemini", "api_key": "test_key"}
    
    with patch("custom_components.automation_generator.tts_client._synthesize_gemini_tts", new=AsyncMock(return_value=output_wav)) as mock_tts:
        result = await async_synthesize_speech(
            hass=hass,
            script="Good morning, Jay-ven!",
            voice="Pulcherrima",
            personality="news_anchor",
            output_wav_path=output_wav,
            creds=creds,
        )
        assert result == output_wav
        mock_tts.assert_awaited_once_with(
            "Good morning, Jay-ven!", "Pulcherrima", "news_anchor", output_wav, "test_key"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tts_client.py -v`  
Expected: FAIL with `tts_client` not found

- [ ] **Step 3: Implement `tts_client.py`**

Implement Gemini TTS invocation and Home Assistant `tts.speak` fallback with personality prompt conditioning.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tts_client.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/automation_generator/tts_client.py tests/test_tts_client.py
git commit -m "feat(tts): add dual-voice synthesis with personality conditioning"
```

---

### Task 5: Pillow Slide Visual Renderer (Real Name Enforced)

**Files:**
- Create: `custom_components/automation_generator/slide_renderer.py`
- Test: `tests/test_slide_renderer.py`

**Interfaces:**
- Consumes: Structured visual card dictionary (`title`, `subtitle`, `bullet_points`), output JPEG path
- Produces:
  - `render_slide(card_data: dict, output_jpeg_path: str) -> str` (synchronous, run in executor)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slide_renderer.py
import os
from PIL import Image
from custom_components.automation_generator.slide_renderer import render_slide

def test_render_slide_dimensions_and_output(tmp_path):
    output_path = str(tmp_path / "slide_01.jpg")
    card_data = {
        "title": "Good Morning, Javan!",
        "subtitle": "Saturday, October 17, 2026",
        "bullet_points": ["Sunny 72°F", "2 Events scheduled"]
    }
    result = render_slide(card_data, output_path)
    assert os.path.exists(result)
    with Image.open(result) as img:
        assert img.size == (1024, 600)
        assert img.mode == "RGB"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_slide_renderer.py -v`  
Expected: FAIL with `slide_renderer` not found

- [ ] **Step 3: Implement `slide_renderer.py`**

Implement `render_slide` using Pillow: 1024x600 canvas, dark gradient background, rounded translucent card, and clear typography rendering `title`, `subtitle`, and bullet points.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_slide_renderer.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/automation_generator/slide_renderer.py tests/test_slide_renderer.py
git commit -m "feat(renderer): implement 1024x600 Pillow typography slide renderer"
```

---

### Task 6: Native FFmpeg Muxing & Concatenation

**Files:**
- Create: `custom_components/automation_generator/ffmpeg_runner.py`
- Test: `tests/test_ffmpeg_runner.py`

**Interfaces:**
- Consumes: Slide image path, audio path, clip path, list of clip paths, final output path
- Produces:
  - `build_slide_clip_command(image_path: str, audio_path: str, output_clip_path: str) -> list[str]`
  - `build_concat_command(concat_list_file: str, output_mp4_path: str) -> list[str]`
  - `async_run_ffmpeg(hass: HomeAssistant, command: list[str]) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ffmpeg_runner.py
from custom_components.automation_generator.ffmpeg_runner import (
    build_slide_clip_command,
    build_concat_command,
)

def test_build_slide_clip_command():
    cmd = build_slide_clip_command("slide.jpg", "audio.wav", "clip.mp4")
    assert "ffmpeg" in cmd[0]
    assert "-loop" in cmd
    assert "slide.jpg" in cmd
    assert "audio.wav" in cmd
    assert "-c:v" in cmd
    assert "libx264" in cmd
    assert "-pix_fmt" in cmd
    assert "yuv420p" in cmd
    assert cmd[-1] == "clip.mp4"

def test_build_concat_command():
    cmd = build_concat_command("clips.txt", "final.mp4")
    assert "-f" in cmd
    assert "concat" in cmd
    assert "-c" in cmd
    assert "copy" in cmd
    assert cmd[-1] == "final.mp4"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ffmpeg_runner.py -v`  
Expected: FAIL with `ffmpeg_runner` not found

- [ ] **Step 3: Implement `ffmpeg_runner.py`**

Implement CLI argument builders and `async_run_ffmpeg` executing subprocess calls via `asyncio.create_subprocess_exec`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ffmpeg_runner.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/automation_generator/ffmpeg_runner.py tests/test_ffmpeg_runner.py
git commit -m "feat(ffmpeg): implement native FFmpeg clip muxing and concatenation"
```

---

### Task 7: Core Generator Engine (Media Pre-generation & Metadata Recording)

**Files:**
- Create: `custom_components/automation_generator/generator.py`
- Test: `tests/test_generator.py`

**Interfaces:**
- Consumes: Routine configuration dictionary, recipient profile, output directory
- Produces:
  - `async_compile_routine_media(hass: HomeAssistant, routine_name: str, routine_config: dict, recipient: dict, output_dir: str) -> dict`
    Returns metadata dict: `{"video_path": str, "duration": float, "slide_timestamps": list[dict]}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_generator.py
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from custom_components.automation_generator.generator import async_compile_routine_media

@pytest.mark.asyncio
async def test_compile_routine_media_flow(tmp_path):
    hass = MagicMock()
    routine_config = {
        "visuals": true,
        "steps": [
            {"name": "greeting", "visual": True, "voice": "Pulcherrima"},
            {"name": "weather", "visual": True, "voice": "Pulcherrima"}
        ]
    }
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}
    
    with patch("custom_components.automation_generator.generator.async_discover_ai_credentials", new=AsyncMock(return_value={"platform": "gemini", "api_key": "k"})), \
         patch("custom_components.automation_generator.generator.async_generate_step_content", new=AsyncMock(return_value={"visual_card": {"title": "T"}, "narration_script": "S"})), \
         patch("custom_components.automation_generator.generator.async_synthesize_speech", new=AsyncMock(return_value="audio.wav")), \
         patch("custom_components.automation_generator.generator.render_slide", return_value="slide.jpg"), \
         patch("custom_components.automation_generator.generator.async_run_ffmpeg", new=AsyncMock(return_value=True)):
        
        result = await async_compile_routine_media(hass, "morning", routine_config, recipient, str(tmp_path))
        assert "video_path" in result
        assert "slide_timestamps" in result
        assert len(result["slide_timestamps"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_generator.py -v`  
Expected: FAIL with `generator` not found

- [ ] **Step 3: Implement `generator.py`**

Orchestrate data ingestion, slide rendering, TTS synthesis, FFmpeg clip compilation, concatenation, saving companion `<routine>.metadata.json`, and cleaning up scratchpad files in a `finally` block.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_generator.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/automation_generator/generator.py tests/test_generator.py
git commit -m "feat(generator): implement full media compilation pipeline with metadata recording"
```

---

### Task 8: Routine Orchestrator (Action Partitioning & Cast Playback Sync)

**Files:**
- Create: `custom_components/automation_generator/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: Routine configuration dictionary
- Produces:
  - `async_execute_background_actions(hass: HomeAssistant, steps: list[dict]) -> None`
  - `async_play_and_sync_routine(hass: HomeAssistant, routine_name: str, target_player: str, metadata: dict, steps: list[dict]) -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_orchestrator.py
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from custom_components.automation_generator.orchestrator import (
    async_execute_background_actions,
    async_play_and_sync_routine,
)

@pytest.mark.asyncio
async def test_execute_background_actions_concurrently():
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    
    steps = [
        {
            "name": "morning_prep",
            "visual": False,
            "actions": [
                {"action": "switch.turn_on", "target": {"entity_id": "switch.espresso"}},
                {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}
            ]
        }
    ]
    
    await async_execute_background_actions(hass, steps)
    assert hass.services.async_call.call_count == 2

@pytest.mark.asyncio
async def test_play_and_sync_waits_for_playing_state():
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    
    # Mock media_player state listener
    with patch("custom_components.automation_generator.orchestrator.async_track_state_change_event") as mock_track:
        metadata = {"video_path": "/local/morning.mp4", "slide_timestamps": []}
        await async_play_and_sync_routine(hass, "morning", "media_player.display", metadata, [])
        hass.services.async_call.assert_awaited_with(
            "media_player", "play_media",
            {
                "entity_id": "media_player.display",
                "media_content_id": "/local/morning.mp4",
                "media_content_type": "video/mp4"
            }
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator.py -v`  
Expected: FAIL with `orchestrator` not found

- [ ] **Step 3: Implement `orchestrator.py`**

Implement concurrent background execution via `asyncio.gather(*[hass.services.async_call(...)], return_exceptions=True)` and player state listener that triggers timed `actions_on_display` once the player enters `'playing'`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/automation_generator/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): implement concurrent action partitioning and Cast sync"
```

---

### Task 9: Service Handlers, Sensor Entity, & Setup Registration

**Files:**
- Create: `custom_components/automation_generator/services.yaml`
- Create: `custom_components/automation_generator/sensor.py`
- Modify: `custom_components/automation_generator/__init__.py`
- Test: `tests/test_services.py`

**Interfaces:**
- Consumes: Orchestrator and Generator APIs
- Produces:
  - Service `automation_generator.pregenerate_routine`
  - Service `automation_generator.run_routine`
  - Service `automation_generator.reload`
  - Sensor `sensor.automation_generator_<routine_name>`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_services.py
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from custom_components.automation_generator import async_setup

@pytest.mark.asyncio
async def test_services_registered():
    hass = MagicMock()
    hass.services.async_register = MagicMock()
    config = {
        "automation_generator": {
            "recipient": {"name": "Javan", "phonetic_name": "Jay-ven"},
            "routines": {"morning": {"title": "Morning Briefing", "visuals": True}}
        }
    }
    
    assert await async_setup(hass, config) is True
    service_names = [call.args[1] for call in hass.services.async_register.call_args_list]
    assert "pregenerate_routine" in service_names
    assert "run_routine" in service_names
    assert "reload" in service_names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_services.py -v`  
Expected: FAIL with missing services

- [ ] **Step 3: Implement `services.yaml`, `sensor.py`, and `__init__.py` service handlers**

Define service schemas, wire service calls to `generator.py` and `orchestrator.py`, and expose the routine status sensor (`idle`, `generating`, `ready`, `playing`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_services.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/automation_generator/ tests/test_services.py
git commit -m "feat(services): register pregenerate_routine, run_routine, and status sensor"
```

---

### Task 10: Official Automation Blueprints Packaging & Verification

**Files:**
- Create: `blueprints/automation/automation_generator/morning_briefing.yaml`
- Create: `blueprints/automation/automation_generator/bedtime_routine.yaml`
- Test: `tests/test_blueprints.py`

**Interfaces:**
- Consumes: Integration services (`automation_generator.pregenerate_routine`, `run_routine`)
- Produces: HACS-distributable blueprints for Home Assistant UI automation creation

- [ ] **Step 1: Write the failing test**

```python
# tests/test_blueprints.py
import yaml
import os

def test_morning_briefing_blueprint_structure():
    path = "blueprints/automation/automation_generator/morning_briefing.yaml"
    assert os.path.exists(path)
    with open(path) as f:
        bp = yaml.safe_load(f)
    assert bp["blueprint"]["domain"] == "automation"
    assert "pregenerate_time" in bp["blueprint"]["input"]
    assert "wake_trigger" in bp["blueprint"]["input"]
    assert "recipient_name" in bp["blueprint"]["input"]
    assert "phonetic_name" in bp["blueprint"]["input"]
    assert "host_voice" in bp["blueprint"]["input"]
    assert "host_personality" in bp["blueprint"]["input"]
    assert "scripture_voice" in bp["blueprint"]["input"]
    assert "scripture_personality" in bp["blueprint"]["input"]
    assert "background_actions" in bp["blueprint"]["input"]

def test_bedtime_routine_blueprint_structure():
    path = "blueprints/automation/automation_generator/bedtime_routine.yaml"
    assert os.path.exists(path)
    with open(path) as f:
        bp = yaml.safe_load(f)
    assert bp["blueprint"]["domain"] == "automation"
    assert "bedtime_trigger" in bp["blueprint"]["input"]
    assert "background_shutdown_actions" in bp["blueprint"]["input"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_blueprints.py -v`  
Expected: FAIL with files missing

- [ ] **Step 3: Author `morning_briefing.yaml` and `bedtime_routine.yaml`**

Write standard Home Assistant blueprints using native trigger, entity, text, and action selectors.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_blueprints.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add blueprints/ tests/test_blueprints.py
git commit -m "feat(blueprints): package Morning Briefing and Bedtime Routine blueprints"
```
