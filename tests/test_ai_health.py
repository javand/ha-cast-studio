"""Tests for AI provider account-problem tracking."""

from __future__ import annotations

import json
from datetime import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import issue_registry as ir

from custom_components.cast_studio.ai_client import (
    AIClientError,
    _query_gemini_structured,
)
from custom_components.cast_studio.ai_health import (
    ISSUE_ID,
    async_report_ai_failure,
    async_report_ai_success,
    get_ai_account_issue,
)
from custom_components.cast_studio.const import DOMAIN
from custom_components.cast_studio.people import Person, Routine
from custom_components.cast_studio.sensor import CastStudioBriefingSensor
from custom_components.cast_studio.tts_client import (
    TTSClientError,
    async_synthesize_speech,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

BILLING_BODY = json.dumps(
    {
        "error": {
            "code": 402,
            "message": "Your prepayment credits are depleted.",
            "status": "RESOURCE_EXHAUSTED",
        }
    }
)


def _issue(hass: HomeAssistant) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)


async def test_billing_failure_sets_attribute_text_and_repair(
    hass: HomeAssistant,
) -> None:
    """A 402 names the provider, problem, service and Google's own message."""
    async_report_ai_failure(hass, "Gemini", "text", 402, BILLING_BODY)

    assert get_ai_account_issue(hass) == (
        "Gemini billing problem (text): Your prepayment credits are depleted."
    )
    issue = _issue(hass)
    assert issue is not None
    assert issue.translation_key == "billing"
    assert issue.severity == ir.IssueSeverity.ERROR
    assert issue.translation_placeholders["message"] == (
        "Your prepayment credits are depleted."
    )


async def test_request_level_errors_are_not_account_problems(
    hass: HomeAssistant,
) -> None:
    """A 400 or 500 is the request's fault or a blip, not the account's."""
    async_report_ai_failure(hass, "Gemini", "text", 400, "bad request")
    async_report_ai_failure(hass, "Gemini", "text", 500, "oops")
    async_report_ai_failure(hass, "Gemini", "text", None, "")

    assert get_ai_account_issue(hass) is None
    assert _issue(hass) is None


async def test_success_clears_only_its_own_service(hass: HomeAssistant) -> None:
    """An image quota stays open while text succeeds; clearing both drops it."""
    async_report_ai_failure(hass, "Gemini", "text", 402, BILLING_BODY)
    async_report_ai_failure(hass, "Gemini", "image", 429, "quota")

    async_report_ai_success(hass, "text")
    assert get_ai_account_issue(hass) == "Gemini quota exceeded (image): quota"
    assert _issue(hass) is not None

    async_report_ai_success(hass, "image")
    assert get_ai_account_issue(hass) is None
    assert _issue(hass) is None


async def test_success_with_no_open_problem_is_a_no_op(hass: HomeAssistant) -> None:
    """Healthy calls on a healthy account touch nothing."""
    async_report_ai_success(hass, "voice")
    assert get_ai_account_issue(hass) is None


async def test_gemini_text_402_is_reported(hass: HomeAssistant) -> None:
    """The structured text call records the rejection before raising."""
    mock_resp = AsyncMock()
    mock_resp.status = 402
    mock_resp.text = AsyncMock(return_value=BILLING_BODY)
    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.return_value = mock_resp

    with (
        patch(
            "custom_components.cast_studio.ai_client.async_get_clientsession",
            return_value=mock_session,
        ),
        pytest.raises(AIClientError),
    ):
        await _query_gemini_structured(hass, "prompt", "key")

    assert get_ai_account_issue(hass) == (
        "Gemini billing problem (text): Your prepayment credits are depleted."
    )


async def test_gemini_voice_402_is_reported_then_cleared(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """A voice rejection falls back to HA TTS and opens the problem."""
    output_wav = str(tmp_path / "out.wav")
    creds = {"platform": "gemini", "api_key": "key"}
    kwargs = {
        "hass": hass,
        "script": "Hello",
        "voice": "Pulcherrima",
        "personality": "calm",
        "output_wav_path": output_wav,
        "creds": creds,
    }

    with (
        patch(
            "custom_components.cast_studio.tts_client._synthesize_gemini_tts",
            side_effect=TTSClientError("402", status=402, body=BILLING_BODY),
        ),
        patch(
            "custom_components.cast_studio.tts_client._synthesize_ha_tts",
            new=AsyncMock(return_value=output_wav),
        ),
    ):
        await async_synthesize_speech(**kwargs)
    assert get_ai_account_issue(hass).startswith("Gemini billing problem (voice)")

    with patch(
        "custom_components.cast_studio.tts_client._synthesize_gemini_tts",
        new=AsyncMock(return_value=output_wav),
    ):
        await async_synthesize_speech(**kwargs)
    assert get_ai_account_issue(hass) is None


async def test_sensor_attribute_shows_the_problem(hass: HomeAssistant) -> None:
    """Every CastStudio sensor carries the problem in ai_account_issue."""
    person = Person(
        person_id="p",
        name="Alex",
        phonetic_name="Alex",
        media_player=None,
        output_format="audio",
        commute_destination_zone=None,
        transportation_mode="driving",
        reset_time=time(3, 0),
        max_age_minutes=0,
    )
    routine = Routine(
        routine_id="r",
        person_id="p",
        name="Morning",
        segments=(),
        dashboard_path=None,
        dashboard_view=None,
        dashboard_display=None,
    )
    sensor = CastStudioBriefingSensor(hass, person, routine)
    assert sensor.extra_state_attributes["ai_account_issue"] is None

    async_report_ai_failure(hass, "Gemini", "image", 401, "bad key")
    assert sensor.extra_state_attributes["ai_account_issue"] == (
        "Gemini API key rejected (image): bad key"
    )
