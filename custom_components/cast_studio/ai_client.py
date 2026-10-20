"""AI Client with zero-credential discovery and structured content generation."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

STEP_PROMPT_GUIDELINES: dict[str, str] = {
    "greeting": (
        "4. GREETING NARRATION: Welcome the recipient warmly by name, mention the "
        "current day of the week and date, and set an energizing tone for the day.\n"
    ),
    "weather": (
        "4. WEATHER NARRATION: State the current sky condition and temperature, "
        "then highlight today's expected high/low forecast and any rain or wind "
        "outlook.\n"
    ),
    "national_day": (
        "4. NATIONAL DAY NARRATION: Announce today's official National Day holiday "
        "observance for the current date. Mention 1-2 interesting traditions "
        "or reasons for the celebration in an engaging tone.\n"
        "   - In 'visual_card', set 'title': 'National Day', "
        "'subtitle': '<Holiday Name>', and 1-2 festive bullet points.\n"
    ),
    "fun_fact": (
        "4. FUN FACT NARRATION: Share an engaging, mind-blowing, and surprising "
        "morning fun fact or trivia. Keep it concise (1-2 sentences) and intriguing.\n"
        "   - In 'visual_card', set 'title': 'Fun Fact', "
        "'subtitle': '<Trivia Topic>', and 1-2 fascinating bullet points.\n"
    ),
    "calendar": (
        "4. SCHEDULE NARRATION: Summarize today's scheduled events chronologically "
        "with their start times, or confirm that the schedule is clear if no "
        "events are listed.\n"
    ),
    "todo": (
        "4. TASKS NARRATION: State how many tasks are pending on the to-do list and "
        "call out the top priority task items clearly.\n"
    ),
    "agenda": (
        "4. AGENDA NARRATION & CARD: Combine today's scheduled meetings and "
        "priority to-do tasks into a single briefing.\n"
        "   - In 'narration_script', summarize today's calendar events chronologically "
        "with start times (or confirm schedule is clear), then transition smoothly "
        "into the key priority tasks to tackle.\n"
        "   - In 'visual_card', return a unified 2-column agenda layout:\n"
        "     'title': 'Today\\'s Agenda',\n"
        "     'subtitle': '<X Events • Y Tasks Pending>',\n"
        "     'layout': 'agenda_split',\n"
        "     'calendar_items': ['<Time> — <Event Name>', ...],\n"
        "     'todo_items': ['<Task Description>', ...],\n"
        "     'bullet_points': ['<Combined fallback items>']\n"
    ),
    "commute": (
        "4. COMMUTE COMPARISON: Report current travel times for all available "
        "commute options (e.g., Driving vs. Metro/Transit) and explicitly state "
        "which option is faster and by how many minutes.\n"
    ),
    "scripture": (
        "4. SCRIPTURE NARRATION: Read the scripture passage and commentary verbatim "
        "without summarizing or altering the text. "
        "Conclude the narration with an uplifting, encouraging thought "
        "along the lines of walking with purpose, focusing on what matters, "
        "and having a wonderful day.\n"
    ),
    "wind_down": (
        "4. BEDTIME NARRATION: Deliver a calm, soothing evening wind-down message "
        "wishing the recipient a peaceful and restful night.\n"
    ),
}


class AIClientError(Exception):
    """Base exception for AI client operations."""


def _clean_json_text(raw_text: str) -> str:
    """Extract and clean JSON substring or markdown code fence blocks."""
    # Look for markdown code fence (```json ... ``` or ``` ... ```)
    code_block_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_text)
    if code_block_match:
        return code_block_match.group(1).strip()

    # Look for outer JSON object {...}
    json_object_match = re.search(r"(\{[\s\S]*\})", raw_text)
    if json_object_match:
        return json_object_match.group(1).strip()

    return raw_text.strip()


def _build_prompt(
    step_config: dict[str, Any],
    recipient: dict[str, Any] | None = None,
) -> str:
    """Construct prompt for structured content generation."""
    rec = recipient or {}
    name = rec.get("name", "User")
    phonetic_name = rec.get("phonetic_name") or name
    step_name = step_config.get("name", "briefing")
    step_type = step_config.get("type", step_name)
    personality = (
        step_config.get("voice_personality")
        or step_config.get("personality")
        or "informative and clear"
    )
    context = step_config.get("context", "")
    now_str = datetime.now().astimezone().strftime("%A, %B %-d, %Y")

    step_key = str(step_type or step_name or "").strip().lower()
    name_key = str(step_name or "").strip().lower()
    if "is_first_step" in step_config:
        is_first = bool(step_config["is_first_step"])
    else:
        is_first = step_key in ("greeting", "welcome") or name_key in (
            "greeting",
            "welcome",
        )

    prompt = (
        "You are generating content for an automated smart home briefing routine.\n"
        f"Current Local Date: {now_str}\n"
        f"Step Name: {step_name}\n"
        f"Step Type: {step_type}\n"
        f"Recipient Real Name: {name}\n"
        f"Recipient Phonetic Pronunciation: {phonetic_name}\n"
        f"Voice Personality: {personality}\n"
        f"Context / State: {context}\n\n"
        "CRITICAL REQUIREMENTS:\n"
        "1. Return ONLY a valid JSON object with the following structure:\n"
        "   {\n"
        '     "visual_card": {\n'
        '       "title": "<Short title for screen display>",\n'
        '       "subtitle": "<Short subtitle or date>",\n'
        '       "bullet_points": ["<point 1>", "<point 2>"]\n'
        "     },\n"
        '     "narration_script": "<Text to be spoken aloud by TTS>"\n'
        "   }\n"
    )

    if is_first:
        prompt += (
            "2. VISUAL CARD IDENTITY CONTRACT: In 'visual_card',\n"
            f"   always display the recipient's real name '{name}' "
            "if naming the recipient.\n"
            f"   NEVER use the phonetic spelling '{phonetic_name}' in 'visual_card'.\n"
            "3. NARRATION IDENTITY CONTRACT: In 'narration_script', always use\n"
            f"   the phonetic spelling '{phonetic_name}' when speaking aloud for TTS.\n"
        )
    else:
        prompt += (
            "2. VISUAL CARD DIRECTIVE: In 'visual_card', "
            "DO NOT include the recipient's name in the title, subtitle, "
            "or bullet points. Use clean, direct, simplified titles "
            "(e.g. 'Weather', 'Today Schedule', 'Tasks', 'Commute', 'Fun Fact'). "
            f"NEVER use '{name}' or '{phonetic_name}' in 'visual_card' "
            "on intermediate slides.\n"
            "3. NARRATION DIRECTIVE: In 'narration_script', "
            f"DO NOT speak the recipient's name '{phonetic_name}' "
            "and DO NOT greet them again. Jump directly into narrating the content.\n"
        )

    guideline = STEP_PROMPT_GUIDELINES.get(step_key) or STEP_PROMPT_GUIDELINES.get(
        name_key
    )
    if guideline:
        prompt += guideline

    if is_first:
        prompt += (
            "5. GREETING DIRECTIVE: This is the opening slide of the briefing. "
            "Welcome the recipient warmly with an energizing morning greeting.\n"
        )
    else:
        prompt += (
            "5. RE-GREETING DIRECTIVE: This is an intermediate slide in an ongoing "
            "briefing routine. CRITICAL: DO NOT say 'Good morning', do NOT greet the "
            "recipient by name again, and do NOT use opening pleasantries or filler. "
            "Jump directly into narrating the content immediately.\n"
        )

    return prompt


def _enforce_recipient_separation(
    content: dict[str, Any],
    recipient: dict[str, Any],
    *,
    is_first: bool | None = None,
) -> dict[str, Any]:
    """Enforce contract: real name in visual_card and phonetic in narration."""
    name = recipient.get("name", "")
    phonetic_name = recipient.get("phonetic_name", "")

    visual_card = dict(content.get("visual_card") or {})
    title = str(visual_card.get("title", ""))
    subtitle = str(visual_card.get("subtitle", ""))
    raw_bullets = visual_card.get("bullet_points", [])
    bullet_points = (
        [str(b) for b in raw_bullets] if isinstance(raw_bullets, list) else []
    )

    narration_script = str(content.get("narration_script", ""))

    # Strip any leaked phonetic_name from visual_card
    if phonetic_name and phonetic_name != name:
        pattern = re.compile(re.escape(phonetic_name), flags=re.IGNORECASE)
        title = pattern.sub(name, title)
        subtitle = pattern.sub(name, subtitle)
        bullet_points = [pattern.sub(name, b) for b in bullet_points]

    # Ensure narration_script uses phonetic_name for pronunciation
    if phonetic_name and name and phonetic_name != name:
        name_word_pattern = re.compile(rf"\b{re.escape(name)}\b", flags=re.IGNORECASE)
        narration_script = name_word_pattern.sub(phonetic_name, narration_script)

    # When explicitly marked as an intermediate step (is_first is False),
    # simplify visual card titles and suppress repeated greetings/names in narration.
    if is_first is False and name:
        title = re.sub(
            rf"\b(for|to|of)\s+{re.escape(name)}(?:'s)?\b",
            "",
            title,
            flags=re.IGNORECASE,
        )
        title = re.sub(rf"\b{re.escape(name)}(?:'s)?\b", "", title, flags=re.IGNORECASE)
        title = " ".join(title.strip(" -:,").split())

        subtitle = re.sub(
            rf"\b(for|to|of)\s+{re.escape(name)}(?:'s)?\b",
            "",
            subtitle,
            flags=re.IGNORECASE,
        )
        subtitle = re.sub(
            rf"\b{re.escape(name)}(?:'s)?\b", "", subtitle, flags=re.IGNORECASE
        )
        subtitle = " ".join(subtitle.strip(" -:,").split())

        name_opts = (
            f"({re.escape(name)}|{re.escape(phonetic_name)})"
            if phonetic_name
            else re.escape(name)
        )
        narration_script = re.sub(
            rf"^(good\s+morning|hello|hi|hey)\s*,?\s*({name_opts})?[!,.]*\s*",
            "",
            narration_script,
            flags=re.IGNORECASE,
        ).strip()
        narration_script = re.sub(
            rf",\s*{name_opts}\b",
            "",
            narration_script,
            flags=re.IGNORECASE,
        ).strip()
        if narration_script and narration_script[0].islower():
            narration_script = narration_script[0].upper() + narration_script[1:]

    return {
        "visual_card": {
            **visual_card,
            "title": title,
            "subtitle": subtitle,
            "bullet_points": bullet_points,
        },
        "narration_script": narration_script,
    }


async def _query_gemini_structured(
    hass: HomeAssistant,
    prompt: str,
    api_key: str,
    model: str = "gemini-2.5-flash",
) -> dict[str, Any]:
    """Execute direct REST request to Gemini 2.5 Flash with structured JSON output."""
    session = async_get_clientsession(hass)
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        f"?key={api_key}"
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                ],
            },
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
        },
    }

    try:
        async with asyncio.timeout(15):
            async with session.post(url, json=payload) as resp:
                if resp.status != HTTPStatus.OK:
                    err_body = await resp.text()
                    msg = f"Gemini API returned status {resp.status}: {err_body}"
                    raise AIClientError(msg)
                data = await resp.json()

        candidate = data["candidates"][0]
        part = candidate["content"]["parts"][0]
        raw_text = part["text"]
        clean_text = _clean_json_text(raw_text)
        return json.loads(clean_text)  # type: ignore[no-any-return]

    except (KeyError, IndexError) as err:
        msg = f"Unexpected Gemini response structure: {err}"
        raise AIClientError(msg) from err
    except json.JSONDecodeError as err:
        msg = f"Failed to parse JSON response from Gemini: {err}"
        raise AIClientError(msg) from err
    except TimeoutError as err:
        msg = "Timeout while communicating with Gemini API"
        raise AIClientError(msg) from err
    except aiohttp.ClientError as err:
        msg = f"Network error while communicating with Gemini API: {err}"
        raise AIClientError(msg) from err


async def _query_gemini_image_api(
    hass: HomeAssistant,
    prompt: str,
    api_key: str,
    model: str = "models/gemini-2.5-flash-image",
) -> dict[str, Any]:
    """Query Gemini multimodal image generation API with 429 retry."""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent"
        f"?key={api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    session = async_get_clientsession(hass)
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with asyncio.timeout(30):
                async with session.post(url, json=payload) as resp:
                    if (
                        resp.status == HTTPStatus.TOO_MANY_REQUESTS
                        and attempt < max_retries - 1
                    ):
                        LOGGER.warning(
                            "Gemini Image API rate limit (429) hit, "
                            "retrying in %ds (attempt %d/%d)",
                            attempt + 2,
                            attempt + 1,
                            max_retries,
                        )
                        await asyncio.sleep(float(attempt + 2))
                        continue
                    if resp.status != HTTPStatus.OK:
                        err_text = await resp.text()
                        truncated = err_text[:120]
                        msg = (
                            f"Gemini Image API returned status {resp.status}: "
                            f"{truncated}"
                        )
                        raise AIClientError(msg)
                    return await resp.json()  # type: ignore[no-any-return]
        except TimeoutError:
            if attempt < max_retries - 1:
                LOGGER.warning(
                    "Gemini Image API timeout, retrying (attempt %d/%d)",
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(1.0)
                continue
            msg = "Timeout while communicating with Gemini Image API"
            raise AIClientError(msg) from None
    msg = "Failed to query Gemini Image API after retries"
    raise AIClientError(msg)


async def async_generate_ai_image(
    hass: HomeAssistant,
    prompt: str,
    target_image_path: str,
    ai_creds: dict[str, Any] | None = None,
) -> str | None:
    """Generate an image using Gemini, saving to target_image_path."""
    creds = ai_creds or await async_discover_ai_credentials(hass)
    if creds.get("platform") != "gemini" or not creds.get("api_key"):
        return None

    try:
        data = await _query_gemini_image_api(hass, prompt, creds["api_key"])
        candidates = data.get("candidates", [])
        if not candidates:
            return None
        parts = candidates[0].get("content", {}).get("parts", [])
        for part in parts:
            inline = part.get("inlineData")
            if inline and "data" in inline:
                img_bytes = base64.b64decode(inline["data"])
                out_path = Path(target_image_path)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(img_bytes)
                return str(out_path)
    except Exception as err:  # noqa: BLE001
        LOGGER.warning("AI image generation failed for '%s': %s", prompt[:60], err)
    return None


def _extract_conversation_speech(response: Any) -> tuple[str, bool]:
    """Extract spoken text and error flag from conversation.process response."""
    if isinstance(response, Mapping):
        resp_inner = response.get("response", {})
        is_err = (
            isinstance(resp_inner, Mapping)
            and resp_inner.get("response_type") == "error"
        )
        speech = (
            resp_inner.get("speech", {}).get("plain", {}).get("speech", "")
            if isinstance(resp_inner, Mapping)
            else ""
        )
        if speech:
            return str(speech), is_err
        if "text" in response:
            return str(response["text"]), is_err
        if "speech" in response:
            return str(response["speech"]), is_err
        return json.dumps(dict(response)), is_err

    if isinstance(response, str):
        return response, False

    return "", False


async def _query_conversation_agent_structured(
    hass: HomeAssistant,
    prompt: str,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Route structured generation request through Home Assistant conversation agent."""
    service_data: dict[str, Any] = {"text": prompt}
    if agent_id:
        service_data["agent_id"] = agent_id

    try:
        response = await hass.services.async_call(
            "conversation",
            "process",
            service_data,
            blocking=True,
            return_response=True,
        )
    except Exception as err:
        msg = f"Failed to call conversation.process service: {err}"
        raise AIClientError(msg) from err

    raw_text, is_error_response = _extract_conversation_speech(response)
    if is_error_response or raw_text.startswith(
        ("Sorry, I am not aware", "Sorry, I couldn't understand")
    ):
        LOGGER.warning(
            "Conversation agent returned intent error instead of generated content: %s",
            raw_text[:120],
        )
        return {
            "visual_card": {
                "title": "Daily Briefing",
                "subtitle": "",
                "bullet_points": [],
            },
            "narration_script": "",
        }

    clean_text = _clean_json_text(raw_text)
    try:
        parsed = json.loads(clean_text)
        if isinstance(parsed, dict) and "visual_card" in parsed:
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass

    # Avoid leaking broken JSON fragments if regex matched partial braces
    fallback_text = raw_text.strip()
    if fallback_text.startswith("{") or '"visual_card"' in fallback_text:
        fallback_text = ""

    # Fallback to structured wrapper if plain text returned
    return {
        "visual_card": {
            "title": "Daily Briefing",
            "subtitle": "",
            "bullet_points": [fallback_text] if fallback_text else [],
        },
        "narration_script": fallback_text,
    }


def _is_entry_disabled(entry: Any) -> bool:
    """Return True if config entry is marked as disabled."""
    disabled_by = getattr(entry, "disabled_by", None)
    if disabled_by is None:
        return False
    # Guard against MagicMock auto-attributes in tests
    if hasattr(disabled_by, "_mock_return_value"):
        return False
    return bool(disabled_by)


def _extract_entry_api_key(entry: Any) -> str | None:
    """Safely extract api_key from active config entry options or data."""
    if _is_entry_disabled(entry):
        return None

    data = getattr(entry, "data", None)
    options = getattr(entry, "options", None)
    data_dict = data if isinstance(data, Mapping) else {}
    options_dict = options if isinstance(options, Mapping) else {}

    key = options_dict.get("api_key") or data_dict.get("api_key")
    if isinstance(key, str):
        clean_key = key.strip()
        if clean_key:
            return clean_key
    return None


async def async_discover_ai_credentials(  # noqa: PLR0912
    hass: HomeAssistant,
) -> dict[str, Any]:
    """
    Auto-detect AI credentials from existing config entries.

    Priority hierarchy:
    1. Explicit integration options from cast_studio.
    2. Auto-detected google_generative_ai active entry.
    3. Fallback to native Home Assistant conversation agent.
    """
    if not hasattr(hass, "config_entries") or not hasattr(
        hass.config_entries, "async_entries"
    ):
        return {"platform": "conversation_agent", "api_key": None}

    # 1. Check explicit cast_studio integration entries
    with suppress(AttributeError, TypeError, KeyError):
        explicit_entries = hass.config_entries.async_entries(DOMAIN)
        for entry in explicit_entries:
            if _is_entry_disabled(entry):
                continue
            if getattr(entry, "domain", DOMAIN) == DOMAIN:
                api_key = _extract_entry_api_key(entry)
                if api_key:
                    return {"platform": "gemini", "api_key": api_key}

    # 2. Check google_generative_ai and google_generative_ai_conversation config entries
    for domain in ("google_generative_ai", "google_generative_ai_conversation"):
        with suppress(AttributeError, TypeError, KeyError):
            genai_entries = hass.config_entries.async_entries(domain)
            for entry in genai_entries:
                if _is_entry_disabled(entry):
                    continue
                api_key = _extract_entry_api_key(entry)
                if api_key:
                    return {"platform": "gemini", "api_key": api_key}

    # Fallback across all entries if mock or system didn't support domain filtering
    with suppress(AttributeError, TypeError, KeyError):
        all_entries = hass.config_entries.async_entries()
        for entry in all_entries:
            if _is_entry_disabled(entry):
                continue
            if getattr(entry, "domain", None) in (
                "google_generative_ai",
                "google_generative_ai_conversation",
            ):
                api_key = _extract_entry_api_key(entry)
                if api_key:
                    return {"platform": "gemini", "api_key": api_key}

    # 3. Fallback to native conversation agent
    return {"platform": "conversation_agent", "api_key": None}


async def async_generate_step_content(
    hass: HomeAssistant,
    step_config: dict[str, Any],
    recipient: dict[str, Any] | None = None,
    ai_creds: dict[str, Any] | None = None,
    creds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate structured step content separating visual card and narration script."""
    rec = recipient or {}
    prompt = _build_prompt(step_config, rec)

    effective_creds = creds if creds is not None else (ai_creds or {})
    platform = effective_creds.get("platform", "conversation_agent")
    api_key = effective_creds.get("api_key")
    agent_id = effective_creds.get("agent_id") or step_config.get("conversation_agent")

    if platform == "gemini" and api_key:
        try:
            raw_content = await _query_gemini_structured(
                hass=hass,
                prompt=prompt,
                api_key=api_key,
            )
        except (AIClientError, aiohttp.ClientError, TimeoutError) as err:
            LOGGER.warning(
                "Gemini structured content generation failed (%s), "
                "falling back to conversation agent",
                err,
            )
            raw_content = await _query_conversation_agent_structured(
                hass=hass,
                prompt=prompt,
                agent_id=agent_id,
            )
    else:
        raw_content = await _query_conversation_agent_structured(
            hass=hass,
            prompt=prompt,
            agent_id=agent_id,
        )

    is_first = step_config.get("is_first_step")
    return _enforce_recipient_separation(raw_content, rec, is_first=is_first)
