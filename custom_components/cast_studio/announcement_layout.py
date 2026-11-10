"""AI-assisted layout planner for template announcements."""

from __future__ import annotations

import base64
import json
import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .ai_client import _clean_json_text, async_discover_ai_credentials
from .ai_health import async_report_ai_failure, async_report_ai_success
from .const import CONF_TEXT_MODEL, DEFAULT_TEXT_MODEL, LOGGER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

ENTITY_REGEX = re.compile(r"\b([a-z_]+)\.([a-z0-9_]+)\b")


def extract_referenced_entities(prompt: str, hass: HomeAssistant) -> list[str]:
    """Find all entity IDs referenced in prompt that exist in Home Assistant."""
    matches = ENTITY_REGEX.findall(prompt)
    found: list[str] = []
    for domain, obj in matches:
        entity_id = f"{domain}.{obj}"
        if hass.states.get(entity_id) is not None and entity_id not in found:
            found.append(entity_id)
    return found


BOX_COORDS = 4


def _build_layout_prompt(
    user_prompt: str,
    entity_snapshots: dict[str, dict[str, Any]],
) -> str:
    """Format instructions for Gemini to plan the slide typography and placement."""
    entities_context = json.dumps(entity_snapshots, indent=2, default=str)
    return (
        "You are an expert graphic designer for smart display slides "
        "(1024x600 resolution, 16:9 aspect ratio).\n"
        "Inspect the provided background image and the user's instructions.\n\n"
        f"USER INSTRUCTIONS: {user_prompt}\n\n"
        "REFERENCED HOME ASSISTANT ENTITIES AND CURRENT VALUES:\n"
        f"{entities_context}\n\n"
        "GOAL:\n"
        "Design a high-contrast, visually balanced typography layout positioned "
        "in the open/uncluttered space of the background image.\n"
        "Return ONLY a JSON object matching this schema:\n"
        "{\n"
        '  "elements": [\n'
        "    {\n"
        '      "role": "badge" | "title" | "subtitle" | "footer" | "custom",\n'
        '      "template": "Literal string OR Home Assistant Jinja2 template '
        "(e.g. {{ state_attr('sensor.x', 'attr') }} or {{ states('sensor.x') }})\",\n"
        '      "box": [x, y, width, height],\n'
        '      "font_size": integer (between 18 and 64),\n'
        '      "font_weight": "bold" | "regular" | "light",\n'
        '      "color": "#hexcolor" (must have high contrast with background),\n'
        '      "align": "left" | "center" | "right",\n'
        '      "style": "normal" | "italic",\n'
        '      "badge": boolean (true only for small category/status chips)\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "RULES:\n"
        "1. All coordinates [x, y, width, height] must be within the 1024x600 canvas.\n"
        "2. Do not overlap key visual elements of the background image "
        "(e.g. logos, figures, banners).\n"
        "3. For dynamic entity values, write valid Home Assistant Jinja2 expressions "
        "using exact attribute names from the context above.\n"
        "4. Keep visual hierarchy clean: Title (largest, bold), "
        "Subtitle (secondary), Meta/Schedule (tertiary).\n"
    )


async def _call_ai_multimodal_json(
    hass: HomeAssistant,
    prompt: str,
    image_mime: str,
    image_bytes: bytes,
    ai_creds: dict[str, Any],
) -> dict[str, Any]:
    """Send multimodal prompt with background image to Gemini and parse JSON."""
    api_key = ai_creds.get("api_key")
    if not api_key:
        msg = "No API key configured for AI layout planner"
        raise HomeAssistantError(msg)

    model = ai_creds.get(CONF_TEXT_MODEL, DEFAULT_TEXT_MODEL)
    session = async_get_clientsession(hass)
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        f"?key={api_key}"
    )

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inlineData": {
                            "mimeType": image_mime,
                            "data": image_b64,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
        },
    }

    try:
        async with session.post(
            url, json=payload, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            if resp.status != HTTPStatus.OK:
                err_body = await resp.text()
                async_report_ai_failure(hass, "Gemini", "layout", resp.status, err_body)
                msg = f"Gemini API returned status {resp.status}: {err_body}"
                raise HomeAssistantError(msg)  # noqa: TRY301
            data = await resp.json()
            async_report_ai_success(hass, "layout")

        candidate = data["candidates"][0]
        part = candidate["content"]["parts"][0]
        raw_text = part["text"]
        clean_text = _clean_json_text(raw_text)
        return json.loads(clean_text, strict=False)  # type: ignore[no-any-return]

    except Exception as err:
        LOGGER.warning("Gemini layout generation failed: %s", err)
        if isinstance(err, HomeAssistantError):
            raise
        msg = f"Failed to generate layout: {err}"
        raise HomeAssistantError(msg) from err


async def async_plan_announcement_layout(  # noqa: PLR0913
    hass: HomeAssistant,
    image_bytes: bytes,
    image_mime: str,
    prompt: str,
    entity_ids: list[str] | None = None,
    ai_creds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inspect background image and entity attributes to generate layout spec."""
    if not ai_creds or not ai_creds.get("api_key"):
        discovered = await async_discover_ai_credentials(hass)
        ai_creds = {**(ai_creds or {}), **discovered}

    if not ai_creds.get("api_key"):
        msg = "No Gemini API key found for layout generation"
        raise HomeAssistantError(msg)

    if entity_ids is None:
        entity_ids = extract_referenced_entities(prompt, hass)

    entity_snapshots: dict[str, dict[str, Any]] = {}
    for eid in entity_ids:
        state = hass.states.get(eid)
        if state is not None:
            entity_snapshots[eid] = {
                "state": state.state,
                "attributes": dict(state.attributes),
            }

    system_prompt = _build_layout_prompt(prompt, entity_snapshots)
    layout = await _call_ai_multimodal_json(
        hass, system_prompt, image_mime, image_bytes, ai_creds
    )

    if (
        not isinstance(layout, dict)
        or "elements" not in layout
        or not isinstance(layout["elements"], list)
    ):
        msg = "AI layout generator did not return valid elements structure"
        raise HomeAssistantError(msg)

    # Sanitize each element
    clean_elements: list[dict[str, Any]] = []
    for elem in layout["elements"]:
        if not isinstance(elem, dict) or "template" not in elem:
            continue
        box = elem.get("box") or [50, 50, 400, 100]
        if not isinstance(box, list) or len(box) < BOX_COORDS:
            box = [50, 50, 400, 100]
        clean_elements.append(
            {
                "role": elem.get("role", "custom"),
                "template": str(elem.get("template") or ""),
                "box": [int(box[0]), int(box[1]), int(box[2]), int(box[3])],
                "font_size": int(elem.get("font_size") or 32),
                "font_weight": str(elem.get("font_weight") or "regular"),
                "color": str(elem.get("color") or "#ffffff"),
                "align": str(elem.get("align") or "left"),
                "style": str(elem.get("style") or "normal"),
                "badge": bool(elem.get("badge", False)),
            }
        )

    return {"elements": clean_elements}
