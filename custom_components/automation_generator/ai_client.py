"""AI Client with zero-credential discovery and structured content generation."""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import suppress
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class AIClientError(Exception):
    """Base exception for AI client operations."""


def _clean_json_text(raw_text: str) -> str:
    """Strip markdown code fence blocks if present."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return text


def _build_prompt(step_config: dict[str, Any], recipient: dict[str, Any]) -> str:
    """Construct prompt for structured content generation."""
    name = recipient.get("name", "User")
    phonetic_name = recipient.get("phonetic_name") or name
    step_name = step_config.get("name", "briefing")
    step_type = step_config.get("type", step_name)
    personality = (
        step_config.get("voice_personality")
        or step_config.get("personality")
        or "informative and clear"
    )
    context = step_config.get("context", "")

    return (
        "You are generating content for an automated smart home briefing routine.\n"
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
        "2. VISUAL CARD IDENTITY CONTRACT: In 'visual_card',\n"
        f"   always display the recipient's real name '{name}'.\n"
        f"   NEVER use the phonetic spelling '{phonetic_name}' in 'visual_card'.\n"
        "3. NARRATION IDENTITY CONTRACT: In 'narration_script', always use\n"
        f"   the phonetic spelling '{phonetic_name}' when speaking aloud for TTS.\n"
    )


def _enforce_recipient_separation(
    content: dict[str, Any], recipient: dict[str, Any]
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
        name_word_pattern = re.compile(rf"\b{re.escape(name)}\b")
        narration_script = name_word_pattern.sub(phonetic_name, narration_script)

    return {
        "visual_card": {
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

    raw_text = ""
    if isinstance(response, dict):
        speech = (
            response.get("response", {})
            .get("speech", {})
            .get("plain", {})
            .get("speech", "")
        )
        if speech:
            raw_text = speech
        elif "text" in response:
            raw_text = str(response["text"])
        elif "speech" in response:
            raw_text = str(response["speech"])
        else:
            raw_text = json.dumps(response)
    elif isinstance(response, str):
        raw_text = response

    clean_text = _clean_json_text(raw_text)
    try:
        parsed = json.loads(clean_text)
        if isinstance(parsed, dict) and "visual_card" in parsed:
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback to structured wrapper if plain text returned
    return {
        "visual_card": {
            "title": "Daily Briefing",
            "subtitle": "",
            "bullet_points": [clean_text] if clean_text else [],
        },
        "narration_script": clean_text,
    }


def _extract_entry_api_key(entry: Any) -> str | None:
    """Safely extract api_key from config entry options or data."""
    data = getattr(entry, "data", {})
    options = getattr(entry, "options", {})
    data_dict = data if isinstance(data, dict) else {}
    options_dict = options if isinstance(options, dict) else {}

    key = options_dict.get("api_key") or data_dict.get("api_key")
    if isinstance(key, str):
        clean_key = key.strip()
        if clean_key:
            return clean_key
    return None


async def async_discover_ai_credentials(hass: HomeAssistant) -> dict[str, Any]:
    """
    Auto-detect AI credentials from existing config entries.

    Priority hierarchy:
    1. Explicit integration options from automation_generator.
    2. Auto-detected google_generative_ai active entry.
    3. Fallback to native Home Assistant conversation agent.
    """
    if not hasattr(hass, "config_entries") or not hasattr(
        hass.config_entries, "async_entries"
    ):
        return {"platform": "conversation_agent", "api_key": None}

    # 1. Check explicit automation_generator integration entries
    with suppress(AttributeError, TypeError, KeyError):
        explicit_entries = hass.config_entries.async_entries(DOMAIN)
        for entry in explicit_entries:
            if getattr(entry, "domain", DOMAIN) == DOMAIN:
                api_key = _extract_entry_api_key(entry)
                if api_key:
                    return {"platform": "gemini", "api_key": api_key}

    # 2. Check google_generative_ai config entries
    with suppress(AttributeError, TypeError, KeyError):
        genai_entries = hass.config_entries.async_entries("google_generative_ai")
        for entry in genai_entries:
            api_key = _extract_entry_api_key(entry)
            if api_key:
                return {"platform": "gemini", "api_key": api_key}

    # Fallback across all entries if mock or system didn't support domain filtering
    with suppress(AttributeError, TypeError, KeyError):
        all_entries = hass.config_entries.async_entries()
        for entry in all_entries:
            if getattr(entry, "domain", None) == "google_generative_ai":
                api_key = _extract_entry_api_key(entry)
                if api_key:
                    return {"platform": "gemini", "api_key": api_key}

    # 3. Fallback to native conversation agent
    return {"platform": "conversation_agent", "api_key": None}


async def async_generate_step_content(
    hass: HomeAssistant,
    step_config: dict[str, Any],
    recipient: dict[str, Any],
    ai_creds: dict[str, Any],
) -> dict[str, Any]:
    """Generate structured step content separating visual card and narration script."""
    prompt = _build_prompt(step_config, recipient)

    platform = ai_creds.get("platform", "conversation_agent")
    api_key = ai_creds.get("api_key")

    if platform == "gemini" and api_key:
        raw_content = await _query_gemini_structured(
            hass=hass,
            prompt=prompt,
            api_key=api_key,
        )
    else:
        raw_content = await _query_conversation_agent_structured(
            hass=hass,
            prompt=prompt,
            agent_id=ai_creds.get("agent_id"),
        )

    return _enforce_recipient_separation(raw_content, recipient)
