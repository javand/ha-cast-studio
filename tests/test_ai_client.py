"""Tests for zero-credential AI discovery and structured content generation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.cast_studio.ai_client import (
    AIClientError,
    _build_prompt,
    _enforce_recipient_separation,
    _query_conversation_agent_structured,
    _query_gemini_structured,
    async_discover_ai_credentials,
    async_generate_step_content,
)
from custom_components.cast_studio.const import DOMAIN


@pytest.mark.asyncio
async def test_auto_detect_google_genai_credentials() -> None:
    """Test auto-discovery of credentials from existing google_generative_ai config entry."""
    hass = MagicMock()
    mock_entry = MagicMock()
    mock_entry.domain = "google_generative_ai"
    mock_entry.data = {"api_key": "discovered_secret_key"}
    hass.config_entries.async_entries.return_value = [mock_entry]

    creds = await async_discover_ai_credentials(hass)
    assert creds["platform"] == "gemini"
    assert creds["api_key"] == "discovered_secret_key"


@pytest.mark.asyncio
async def test_auto_detect_google_genai_conversation_credentials() -> None:
    """Test auto-discovery of credentials from existing google_generative_ai_conversation config entry."""
    hass = MagicMock()
    mock_entry = MagicMock()
    mock_entry.domain = "google_generative_ai_conversation"
    mock_entry.data = {"api_key": "discovered_conversation_key"}

    def mock_async_entries(domain: str | None = None) -> list[MagicMock]:
        if domain in ("google_generative_ai_conversation", None):
            return [mock_entry]
        return []

    hass.config_entries.async_entries.side_effect = mock_async_entries

    creds = await async_discover_ai_credentials(hass)
    assert creds["platform"] == "gemini"
    assert creds["api_key"] == "discovered_conversation_key"


@pytest.mark.asyncio
async def test_fallback_to_conversation_agent_when_no_key() -> None:
    """Test fallback to native conversation agent when no key is found."""
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = []

    creds = await async_discover_ai_credentials(hass)
    assert creds["platform"] == "conversation_agent"
    assert creds["api_key"] is None


@pytest.mark.asyncio
async def test_content_generation_separates_real_name_and_phonetic() -> None:
    """Test content generation enforces real name on visual card and phonetic in narration."""
    hass = MagicMock()
    recipient = {"name": "Alex", "phonetic_name": "Al-ex"}
    step_config = {"name": "greeting", "type": "greeting"}

    mock_response = {
        "visual_card": {
            "title": "Good Morning, Alex!",
            "subtitle": "Saturday, October 17",
        },
        "narration_script": "Good morning, Al-ex! Welcome to your briefing.",
    }

    with patch(
        "custom_components.cast_studio.ai_client._query_gemini_structured",
        new=AsyncMock(return_value=mock_response),
    ):
        content = await async_generate_step_content(
            hass, step_config, recipient, {"platform": "gemini", "api_key": "fake"}
        )
        assert content["visual_card"]["title"] == "Good Morning, Alex!"
        assert "Al-ex" not in content["visual_card"]["title"]
        assert "Al-ex" in content["narration_script"]


@pytest.mark.asyncio
async def test_auto_detect_explicit_domain_credentials() -> None:
    """Test explicit integration entry credentials take precedence."""
    hass = MagicMock()
    explicit_entry = MagicMock()
    explicit_entry.domain = DOMAIN
    explicit_entry.data = {"api_key": "explicit_key"}

    genai_entry = MagicMock()
    genai_entry.domain = "google_generative_ai"
    genai_entry.data = {"api_key": "discovered_key"}

    # When async_entries is called for DOMAIN vs google_generative_ai
    def mock_async_entries(domain: str | None = None) -> list[MagicMock]:
        if domain == DOMAIN:
            return [explicit_entry]
        if domain == "google_generative_ai":
            return [genai_entry]
        return [explicit_entry, genai_entry]

    hass.config_entries.async_entries.side_effect = mock_async_entries

    creds = await async_discover_ai_credentials(hass)
    assert creds["platform"] == "gemini"
    assert creds["api_key"] == "explicit_key"


@pytest.mark.asyncio
async def test_auto_detect_empty_or_whitespace_key_falls_back() -> None:
    """Test empty or whitespace API key in google_generative_ai falls back."""
    hass = MagicMock()
    mock_entry = MagicMock()
    mock_entry.domain = "google_generative_ai"
    mock_entry.data = {"api_key": "   "}
    hass.config_entries.async_entries.return_value = [mock_entry]

    creds = await async_discover_ai_credentials(hass)
    assert creds["platform"] == "conversation_agent"
    assert creds["api_key"] is None


@pytest.mark.asyncio
async def test_content_generation_sanitizes_phonetic_leak_in_visual_card() -> None:
    """Test sanitizer strips phonetic name if AI leaks it into visual card fields."""
    hass = MagicMock()
    recipient = {"name": "Alex", "phonetic_name": "Al-ex"}
    step_config = {"name": "weather", "type": "weather"}

    leaked_response = {
        "visual_card": {
            "title": "Weather for Al-ex",
            "subtitle": "Forecast for Al-ex's Day",
            "bullet_points": ["Sunny for Al-ex", "High 72°F"],
        },
        "narration_script": "Good morning, Al-ex! Here is your weather.",
    }

    with patch(
        "custom_components.cast_studio.ai_client._query_gemini_structured",
        new=AsyncMock(return_value=leaked_response),
    ):
        content = await async_generate_step_content(
            hass, step_config, recipient, {"platform": "gemini", "api_key": "key"}
        )
        assert content["visual_card"]["title"] == "Weather for Alex"
        assert content["visual_card"]["subtitle"] == "Forecast for Alex's Day"
        assert content["visual_card"]["bullet_points"][0] == "Sunny for Alex"
        assert "Al-ex" not in json.dumps(content["visual_card"])
        assert "Al-ex" in content["narration_script"]


@pytest.mark.asyncio
async def test_content_generation_enforces_phonetic_in_narration() -> None:
    """Test sanitizer injects phonetic name into narration if AI used real name."""
    hass = MagicMock()
    recipient = {"name": "Alex", "phonetic_name": "Al-ex"}
    step_config = {"name": "calendar", "type": "calendar"}

    missed_phonetic_response = {
        "visual_card": {
            "title": "Agenda for Alex",
            "subtitle": "Today's Schedule",
            "bullet_points": ["10:00 AM Meeting"],
        },
        "narration_script": "Hello Alex! You have 1 event today.",
    }

    with patch(
        "custom_components.cast_studio.ai_client._query_gemini_structured",
        new=AsyncMock(return_value=missed_phonetic_response),
    ):
        content = await async_generate_step_content(
            hass, step_config, recipient, {"platform": "gemini", "api_key": "key"}
        )
        assert "Al-ex" in content["narration_script"]
        assert "Alex" not in content["narration_script"]
        assert "Alex" in content["visual_card"]["title"]


@pytest.mark.asyncio
async def test_content_generation_routes_to_conversation_agent() -> None:
    """Test content generation routes to conversation agent when configured."""
    hass = MagicMock()
    recipient = {"name": "Alex", "phonetic_name": "Al-ex"}
    step_config = {"name": "daily_text", "type": "daily_text"}

    mock_response = {
        "visual_card": {
            "title": "Daily Text for Alex",
            "subtitle": "Saturday, October 17",
            "bullet_points": ["Proverbs 3:5"],
        },
        "narration_script": "Al-ex, trust in Jehovah with all your heart.",
    }

    with patch(
        "custom_components.cast_studio.ai_client._query_conversation_agent_structured",
        new=AsyncMock(return_value=mock_response),
    ) as mock_agent:
        content = await async_generate_step_content(
            hass,
            step_config,
            recipient,
            {"platform": "conversation_agent", "api_key": None, "agent_id": "agent_1"},
        )
        assert content["visual_card"]["title"] == "Daily Text for Alex"
        assert "Al-ex" not in content["visual_card"]["title"]
        assert "Al-ex" in content["narration_script"]
        mock_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_query_gemini_structured_api_call() -> None:
    """Test _query_gemini_structured parses JSON payload from Gemini response."""
    hass = MagicMock()
    mock_session = MagicMock()

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(
        return_value={
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "visual_card": {
                                            "title": "Gemini Card",
                                            "subtitle": "Gemini Subtitle",
                                            "bullet_points": ["Point 1"],
                                        },
                                        "narration_script": "Gemini Script",
                                    }
                                )
                            }
                        ]
                    }
                }
            ]
        }
    )

    # Mock session.post context manager
    mock_session.post.return_value.__aenter__.return_value = mock_resp

    with patch(
        "custom_components.cast_studio.ai_client.async_get_clientsession",
        return_value=mock_session,
    ):
        result = await _query_gemini_structured(hass, "Test Prompt", "api_key_123")
        assert result["visual_card"]["title"] == "Gemini Card"
        assert result["narration_script"] == "Gemini Script"


@pytest.mark.asyncio
async def test_query_conversation_agent_structured_service_call() -> None:
    """Test _query_conversation_agent_structured invokes conversation.process."""
    hass = MagicMock()
    response_payload = {
        "response": {
            "speech": {
                "plain": {
                    "speech": json.dumps(
                        {
                            "visual_card": {
                                "title": "Conv Card",
                                "subtitle": "Conv Subtitle",
                                "bullet_points": ["Conv Point"],
                            },
                            "narration_script": "Conv Narration",
                        }
                    )
                }
            }
        }
    }
    hass.services.async_call = AsyncMock(return_value=response_payload)

    result = await _query_conversation_agent_structured(
        hass, "Test Prompt", agent_id="agent_ha"
    )
    assert result["visual_card"]["title"] == "Conv Card"
    assert result["narration_script"] == "Conv Narration"
    hass.services.async_call.assert_awaited_once_with(
        "conversation",
        "process",
        {"text": "Test Prompt", "agent_id": "agent_ha"},
        blocking=True,
        return_response=True,
    )


def test_clean_json_text_with_conversational_preamble_and_postamble() -> None:
    """Test _clean_json_text extracts JSON even with surrounding conversational text."""
    from custom_components.cast_studio.ai_client import _clean_json_text

    raw_response = (
        "Here is your routine content in JSON format:\n"
        "```json\n"
        '{\n  "visual_card": {"title": "Good Morning"},\n  "narration_script": "Wake up"\n}\n'
        "```\n"
        "I hope you have a wonderful day!"
    )
    cleaned = _clean_json_text(raw_response)
    data = json.loads(cleaned)
    assert data["visual_card"]["title"] == "Good Morning"
    assert data["narration_script"] == "Wake up"


@pytest.mark.asyncio
async def test_query_gemini_structured_aiohttp_client_error() -> None:
    """Test _query_gemini_structured wraps aiohttp.ClientError into AIClientError."""
    import aiohttp

    from custom_components.cast_studio.ai_client import AIClientError

    hass = MagicMock()
    mock_session = MagicMock()
    mock_session.post.side_effect = aiohttp.ClientError("Connection reset")

    with (
        patch(
            "custom_components.cast_studio.ai_client.async_get_clientsession",
            return_value=mock_session,
        ),
        pytest.raises(
            AIClientError, match="Network error while communicating with Gemini API"
        ),
    ):
        await _query_gemini_structured(hass, "Prompt", "key")


@pytest.mark.asyncio
async def test_auto_detect_skips_disabled_config_entries() -> None:
    """Test async_discover_ai_credentials ignores config entries that are disabled."""
    hass = MagicMock()
    disabled_entry = MagicMock()
    disabled_entry.domain = "google_generative_ai"
    disabled_entry.data = {"api_key": "disabled_secret_key"}
    disabled_entry.disabled_by = "user"
    hass.config_entries.async_entries.return_value = [disabled_entry]

    creds = await async_discover_ai_credentials(hass)
    assert creds["platform"] == "conversation_agent"
    assert creds["api_key"] is None


@pytest.mark.asyncio
async def test_generate_step_content_fallback_to_conversation_on_gemini_error() -> None:
    """Test fallback from Gemini to conversation agent when Gemini raises AIClientError."""
    hass = MagicMock()
    ai_creds = {
        "platform": "gemini",
        "api_key": "bad_key",
        "agent_id": "conversation.mock",
    }
    step_config = {"name": "greeting"}
    recipient = {"name": "Alex", "phonetic_name": "Al-ex"}

    fallback_response = {
        "visual_card": {"title": "Good Morning", "subtitle": "", "bullet_points": []},
        "narration_script": "Good morning Al-ex",
    }

    with (
        patch(
            "custom_components.cast_studio.ai_client._query_gemini_structured",
            side_effect=AIClientError("API quota exceeded"),
        ) as mock_gemini,
        patch(
            "custom_components.cast_studio.ai_client._query_conversation_agent_structured",
            new=AsyncMock(return_value=fallback_response),
        ) as mock_conv,
    ):
        result = await async_generate_step_content(
            hass, step_config, recipient, ai_creds
        )
        assert mock_gemini.called
        assert mock_conv.called
        assert result["visual_card"]["title"] == "Good Morning"
        assert result["narration_script"] == "Good morning Al-ex"


@pytest.mark.asyncio
async def test_generate_step_content_fallback_to_conversation_on_network_error() -> (
    None
):
    """Test fallback from Gemini to conversation agent on aiohttp ClientError."""
    hass = MagicMock()
    ai_creds = {"platform": "gemini", "api_key": "test_key"}
    step_config = {"name": "weather"}
    recipient = {"name": "Alex", "phonetic_name": "Al-ex"}

    fallback_response = {
        "visual_card": {
            "title": "Weather",
            "subtitle": "Today",
            "bullet_points": ["Sunny"],
        },
        "narration_script": "Today is sunny",
    }

    with (
        patch(
            "custom_components.cast_studio.ai_client._query_gemini_structured",
            side_effect=aiohttp.ClientError("Connection refused"),
        ),
        patch(
            "custom_components.cast_studio.ai_client._query_conversation_agent_structured",
            new=AsyncMock(return_value=fallback_response),
        ),
    ):
        result = await async_generate_step_content(
            hass, step_config, recipient, ai_creds
        )
        assert result["visual_card"]["title"] == "Weather"


def test_enforce_recipient_separation_case_insensitive() -> None:
    """Test case-insensitive replacement of recipient real name in narration script."""
    recipient = {"name": "Alex", "phonetic_name": "Al-ex"}
    raw_content = {
        "visual_card": {
            "title": "Welcome Al-ex",
            "subtitle": "",
            "bullet_points": ["Hello AL-EX"],
        },
        "narration_script": "Good morning alex, it is great to see you ALEX!",
    }

    enforced = _enforce_recipient_separation(raw_content, recipient)

    # In visual card: phonetic name should be replaced by real name
    assert enforced["visual_card"]["title"] == "Welcome Alex"
    assert "Alex" in enforced["visual_card"]["bullet_points"][0]

    # In narration script: real name in any case should be replaced by phonetic name
    assert (
        enforced["narration_script"]
        == "Good morning Al-ex, it is great to see you Al-ex!"
    )


def test_build_prompt_scripture_includes_uplifting_thought() -> None:
    """Test scripture step prompt instructs AI to conclude with an uplifting thought."""
    step = {"name": "Scripture", "type": "scripture"}
    prompt = _build_prompt(step, {"name": "Alex"})
    assert "uplifting" in prompt.lower() or "encouraging" in prompt.lower()
    assert "4. SCRIPTURE NARRATION" in prompt

    # Non-scripture step should NOT contain scripture narration instruction
    weather_prompt = _build_prompt(
        {"name": "weather", "type": "weather"}, {"name": "Alex"}
    )
    assert "4. SCRIPTURE NARRATION" not in weather_prompt


@pytest.mark.asyncio
async def test_auto_detect_mappingproxy_config_entry() -> None:
    """Test auto-discovery handles MappingProxyType used by real Home Assistant ConfigEntry."""
    from types import MappingProxyType

    hass = MagicMock()
    mock_entry = MagicMock()
    mock_entry.domain = "google_generative_ai_conversation"
    mock_entry.data = MappingProxyType({"api_key": "mappingproxy_secret_key"})
    mock_entry.options = MappingProxyType({})
    hass.config_entries.async_entries.return_value = [mock_entry]

    creds = await async_discover_ai_credentials(hass)
    assert creds["platform"] == "gemini"
    assert creds["api_key"] == "mappingproxy_secret_key"


@pytest.mark.asyncio
async def test_query_conversation_agent_ignores_intent_error_response() -> None:
    """Test _query_conversation_agent_structured suppresses intent errors and echoed JSON prompts."""
    hass = MagicMock()
    error_payload = {
        "response": {
            "response_type": "error",
            "data": {"code": "no_valid_targets"},
            "speech": {
                "plain": {
                    "speech": (
                        "Sorry, I am not aware of any device called You are generating content "
                        '{ "visual card" { "title" "<Short title for screen display>" } '
                    )
                }
            },
        }
    }
    hass.services.async_call = AsyncMock(return_value=error_payload)

    result = await _query_conversation_agent_structured(hass, "Test Prompt")
    assert result["narration_script"] == ""
    assert result["visual_card"]["bullet_points"] == []


def test_build_prompt_includes_current_local_date_and_step_guidelines() -> None:
    """Verify _build_prompt injects Current Local Date and step-specific narrative guidelines."""
    commute_prompt = _build_prompt(
        {"name": "commute", "type": "commute"}, {"name": "Alex"}
    )
    assert "Current Local Date:" in commute_prompt
    assert "faster" in commute_prompt.lower()

    weather_prompt = _build_prompt(
        {"name": "weather", "type": "weather"}, {"name": "Alex"}
    )
    assert "Current Local Date:" in weather_prompt
    assert "high" in weather_prompt.lower() or "forecast" in weather_prompt.lower()

    fun_fact_prompt = _build_prompt(
        {"name": "fun_fact", "type": "fun_fact"}, {"name": "Alex"}
    )
    assert "fun fact" in fun_fact_prompt.lower()

    national_day_prompt = _build_prompt(
        {"name": "national_day", "type": "national_day"}, {"name": "Alex"}
    )
    assert "National Day" in national_day_prompt


def test_build_prompt_greeting_vs_intermediate_directives() -> None:
    """Verify greeting prompt welcomes user while intermediate steps forbid repeating good morning."""
    greeting_prompt = _build_prompt(
        {"name": "greeting", "type": "greeting", "is_first_step": True},
        {"name": "Alex", "phonetic_name": "Al-ex"},
    )
    assert "GREETING DIRECTIVE" in greeting_prompt
    assert "Welcome the recipient warmly" in greeting_prompt

    weather_prompt = _build_prompt(
        {"name": "weather", "type": "weather", "is_first_step": False},
        {"name": "Alex", "phonetic_name": "Al-ex"},
    )
    assert "RE-GREETING DIRECTIVE" in weather_prompt
    assert "DO NOT say 'Good morning'" in weather_prompt
    assert "Jump directly into narrating" in weather_prompt


@pytest.mark.asyncio
async def test_async_generate_ai_image_success(tmp_path: Path) -> None:
    """Verify async_generate_ai_image decodes base64 inlineData and writes image."""
    import base64

    from custom_components.cast_studio.ai_client import async_generate_ai_image

    fake_png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    )
    b64_data = base64.b64encode(fake_png_bytes).decode("ascii")

    mock_resp = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"inlineData": {"mimeType": "image/png", "data": b64_data}}
                    ]
                }
            }
        ]
    }

    mock_hass = MagicMock()
    target_path = str(tmp_path / "ai_generated.png")

    with patch(
        "custom_components.cast_studio.ai_client._query_gemini_image_api",
        new=AsyncMock(return_value=mock_resp),
    ):
        result = await async_generate_ai_image(
            mock_hass,
            "A steaming cup of coffee",
            target_path,
            ai_creds={"platform": "gemini", "api_key": "fake_key"},
        )
        assert result == target_path
        assert Path(target_path).is_file()
        assert Path(target_path).read_bytes() == fake_png_bytes


@pytest.mark.asyncio
async def test_query_gemini_image_api_retries_on_rate_limit_429() -> None:
    """Test that _query_gemini_image_api retries when encountering HTTP 429."""
    from custom_components.cast_studio.ai_client import _query_gemini_image_api

    mock_hass = MagicMock()
    mock_resp_429 = MagicMock()
    mock_resp_429.status = 429

    mock_resp_200 = MagicMock()
    mock_resp_200.status = 200
    mock_resp_200.json = AsyncMock(
        return_value={"candidates": [{"content": {"parts": []}}]}
    )

    mock_session = MagicMock()
    # First call returns 429 context manager, second call returns 200 context manager
    cm_429 = MagicMock()
    cm_429.__aenter__ = AsyncMock(return_value=mock_resp_429)
    cm_429.__aexit__ = AsyncMock(return_value=None)

    cm_200 = MagicMock()
    cm_200.__aenter__ = AsyncMock(return_value=mock_resp_200)
    cm_200.__aexit__ = AsyncMock(return_value=None)

    mock_session.post.side_effect = [cm_429, cm_200]

    with (
        patch(
            "custom_components.cast_studio.ai_client.async_get_clientsession",
            return_value=mock_session,
        ),
        patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
    ):
        result = await _query_gemini_image_api(mock_hass, "A test prompt", "fake_key")
        assert result == {"candidates": [{"content": {"parts": []}}]}
        assert mock_session.post.call_count == 2
        mock_sleep.assert_called_once()


def test_build_prompt_intermediate_steps_forbid_repeating_recipient_name() -> None:
    """Verify intermediate step prompt strictly forbids naming recipient on card or in speech."""
    weather_prompt = _build_prompt(
        {"name": "weather", "type": "weather", "is_first_step": False},
        {"name": "Alex", "phonetic_name": "Al-ex"},
    )
    assert "DO NOT include the recipient's name" in weather_prompt
    assert "DO NOT speak the recipient's name" in weather_prompt


def test_enforce_recipient_separation_simplifies_intermediate_titles_and_narration() -> (
    None
):
    """Verify intermediate step sanitizer simplifies titles and strips repeated re-greetings."""
    from custom_components.cast_studio.ai_client import _enforce_recipient_separation

    recipient = {"name": "Alex", "phonetic_name": "Al-ex"}
    raw_content = {
        "visual_card": {
            "title": "Weather for Alex",
            "subtitle": "Forecast for Alex's Day",
            "bullet_points": ["Sunny skies ahead"],
        },
        "narration_script": "Good morning, Al-ex! Sunny skies ahead with highs near 75.",
    }

    result = _enforce_recipient_separation(raw_content, recipient, is_first=False)
    assert result["visual_card"]["title"] == "Weather"
    assert "Alex" not in result["visual_card"]["title"]
    assert "Alex" not in result["visual_card"]["subtitle"]
    assert not result["narration_script"].lower().startswith("good morning")
    assert "Al-ex" not in result["narration_script"]
    assert result["narration_script"].startswith("Sunny skies ahead")


def test_build_prompt_agenda_guidelines() -> None:
    """Verify _build_prompt includes combined agenda narration and 2-column card instructions."""
    from custom_components.cast_studio.ai_client import _build_prompt

    prompt = _build_prompt(
        {"name": "agenda", "type": "agenda"},
        {"name": "Alex", "phonetic_name": "Al-ex"},
    )
    assert "AGENDA NARRATION & CARD" in prompt
    assert "agenda_split" in prompt
    assert "calendar_items" in prompt
    assert "todo_items" in prompt


def test_enforce_recipient_separation_preserves_custom_card_fields() -> None:
    """Verify _enforce_recipient_separation preserves layout, calendar_items, and todo_items."""
    from custom_components.cast_studio.ai_client import _enforce_recipient_separation

    recipient = {"name": "Alex", "phonetic_name": "Al-ex"}
    raw_content = {
        "visual_card": {
            "title": "Today's Agenda",
            "subtitle": "Thursday",
            "layout": "agenda_split",
            "calendar_items": ["09:00 AM — Standup"],
            "todo_items": ["Water plants"],
            "bullet_points": ["Combined item"],
        },
        "narration_script": "You have Standup at 9:00 AM.",
    }
    result = _enforce_recipient_separation(raw_content, recipient)
    card = result["visual_card"]
    assert card["layout"] == "agenda_split"
    assert card["calendar_items"] == ["09:00 AM — Standup"]
    assert card["todo_items"] == ["Water plants"]


def test_build_prompt_national_day_and_fun_fact_guidelines() -> None:
    """Verify _build_prompt distinguishes national_day and fun_fact guidelines."""
    from custom_components.cast_studio.ai_client import _build_prompt

    nd_prompt = _build_prompt(
        {"name": "national_day", "type": "national_day"},
        {"name": "Alex", "phonetic_name": "Al-ex"},
    )
    assert "NATIONAL DAY NARRATION" in nd_prompt
    assert "Holiday Name" in nd_prompt

    ff_prompt = _build_prompt(
        {"name": "fun_fact", "type": "fun_fact"},
        {"name": "Alex", "phonetic_name": "Al-ex"},
    )
    assert "FUN FACT NARRATION" in ff_prompt
    assert "Trivia Topic" in ff_prompt
