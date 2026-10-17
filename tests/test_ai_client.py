"""Tests for zero-credential AI discovery and structured content generation."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.automation_generator.ai_client import (
    _query_conversation_agent_structured,
    _query_gemini_structured,
    async_discover_ai_credentials,
    async_generate_step_content,
)
from custom_components.automation_generator.const import DOMAIN


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
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}
    step_config = {"name": "greeting", "type": "greeting"}

    mock_response = {
        "visual_card": {
            "title": "Good Morning, Javan!",
            "subtitle": "Saturday, October 17",
        },
        "narration_script": "Good morning, Jay-ven! Welcome to your briefing.",
    }

    with patch(
        "custom_components.automation_generator.ai_client._query_gemini_structured",
        new=AsyncMock(return_value=mock_response),
    ):
        content = await async_generate_step_content(
            hass, step_config, recipient, {"platform": "gemini", "api_key": "fake"}
        )
        assert content["visual_card"]["title"] == "Good Morning, Javan!"
        assert "Jay-ven" not in content["visual_card"]["title"]
        assert "Jay-ven" in content["narration_script"]


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
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}
    step_config = {"name": "weather", "type": "weather"}

    leaked_response = {
        "visual_card": {
            "title": "Weather for Jay-ven",
            "subtitle": "Forecast for Jay-ven's Day",
            "bullet_points": ["Sunny for Jay-ven", "High 72°F"],
        },
        "narration_script": "Good morning, Jay-ven! Here is your weather.",
    }

    with patch(
        "custom_components.automation_generator.ai_client._query_gemini_structured",
        new=AsyncMock(return_value=leaked_response),
    ):
        content = await async_generate_step_content(
            hass, step_config, recipient, {"platform": "gemini", "api_key": "key"}
        )
        assert content["visual_card"]["title"] == "Weather for Javan"
        assert content["visual_card"]["subtitle"] == "Forecast for Javan's Day"
        assert content["visual_card"]["bullet_points"][0] == "Sunny for Javan"
        assert "Jay-ven" not in json.dumps(content["visual_card"])
        assert "Jay-ven" in content["narration_script"]


@pytest.mark.asyncio
async def test_content_generation_enforces_phonetic_in_narration() -> None:
    """Test sanitizer injects phonetic name into narration if AI used real name."""
    hass = MagicMock()
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}
    step_config = {"name": "calendar", "type": "calendar"}

    missed_phonetic_response = {
        "visual_card": {
            "title": "Agenda for Javan",
            "subtitle": "Today's Schedule",
            "bullet_points": ["10:00 AM Meeting"],
        },
        "narration_script": "Hello Javan! You have 1 event today.",
    }

    with patch(
        "custom_components.automation_generator.ai_client._query_gemini_structured",
        new=AsyncMock(return_value=missed_phonetic_response),
    ):
        content = await async_generate_step_content(
            hass, step_config, recipient, {"platform": "gemini", "api_key": "key"}
        )
        assert "Jay-ven" in content["narration_script"]
        assert "Javan" not in content["narration_script"]
        assert "Javan" in content["visual_card"]["title"]


@pytest.mark.asyncio
async def test_content_generation_routes_to_conversation_agent() -> None:
    """Test content generation routes to conversation agent when configured."""
    hass = MagicMock()
    recipient = {"name": "Javan", "phonetic_name": "Jay-ven"}
    step_config = {"name": "daily_text", "type": "daily_text"}

    mock_response = {
        "visual_card": {
            "title": "Daily Text for Javan",
            "subtitle": "Saturday, October 17",
            "bullet_points": ["Proverbs 3:5"],
        },
        "narration_script": "Jay-ven, trust in Jehovah with all your heart.",
    }

    with patch(
        "custom_components.automation_generator.ai_client._query_conversation_agent_structured",
        new=AsyncMock(return_value=mock_response),
    ) as mock_agent:
        content = await async_generate_step_content(
            hass,
            step_config,
            recipient,
            {"platform": "conversation_agent", "api_key": None, "agent_id": "agent_1"},
        )
        assert content["visual_card"]["title"] == "Daily Text for Javan"
        assert "Jay-ven" not in content["visual_card"]["title"]
        assert "Jay-ven" in content["narration_script"]
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
        "custom_components.automation_generator.ai_client.async_get_clientsession",
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
    from custom_components.automation_generator.ai_client import _clean_json_text

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

    from custom_components.automation_generator.ai_client import AIClientError

    hass = MagicMock()
    mock_session = MagicMock()
    mock_session.post.side_effect = aiohttp.ClientError("Connection reset")

    with (
        patch(
            "custom_components.automation_generator.ai_client.async_get_clientsession",
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
