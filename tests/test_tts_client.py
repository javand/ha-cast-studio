"""Tests for pluggable TTS client with per-action voice and personality conditioning."""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.automation_generator.tts_client import (
    TTSClientError,
    _synthesize_gemini_tts,
    _synthesize_ha_tts,
    _synthesize_openai_tts,
    async_synthesize_speech,
    map_voice_for_openai,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_synthesize_speech_gemini_voices(tmp_path: Path) -> None:
    """Test Gemini TTS invocation with host voice and personality conditioning (verbatim brief test)."""
    hass = MagicMock()
    output_wav = str(tmp_path / "slide_01.wav")
    creds = {"platform": "gemini", "api_key": "test_key"}

    with patch(
        "custom_components.automation_generator.tts_client._synthesize_gemini_tts",
        new=AsyncMock(return_value=output_wav),
    ) as mock_tts:
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
            "Good morning, Jay-ven!",
            "Pulcherrima",
            "news_anchor",
            output_wav,
            "test_key",
        )


@pytest.mark.asyncio
async def test_synthesize_speech_gemini_enceladus_voice(tmp_path: Path) -> None:
    """Test Gemini TTS invocation with secondary scripture voice and life coach personality."""
    hass = MagicMock()
    output_wav = str(tmp_path / "slide_02.wav")
    creds = {"platform": "gemini", "api_key": "test_gemini_key"}

    with patch(
        "custom_components.automation_generator.tts_client._synthesize_gemini_tts",
        new=AsyncMock(return_value=output_wav),
    ) as mock_tts:
        result = await async_synthesize_speech(
            hass=hass,
            script="Trust in Jehovah with all your heart.",
            voice="Enceladus",
            personality="life_coach",
            output_wav_path=output_wav,
            creds=creds,
        )
        assert result == output_wav
        mock_tts.assert_awaited_once_with(
            "Trust in Jehovah with all your heart.",
            "Enceladus",
            "life_coach",
            output_wav,
            "test_gemini_key",
        )


@pytest.mark.asyncio
async def test_synthesize_gemini_tts_direct_api(tmp_path: Path) -> None:
    """Test direct REST invocation of Gemini TTS decoding base64 audio to file."""
    output_wav = str(tmp_path / "direct_gemini.wav")
    fake_audio_bytes = b"RIFFfake_gemini_audio_content_data"
    b64_audio = base64.b64encode(fake_audio_bytes).decode("ascii")

    mock_resp_json = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "audio/wav",
                                "data": b64_audio,
                            }
                        }
                    ]
                }
            }
        ]
    }

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=mock_resp_json)

    mock_context = MagicMock()
    mock_context.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_context.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.post.return_value = mock_context

    result = await _synthesize_gemini_tts(
        script="Hello world",
        voice="Pulcherrima",
        personality="news_anchor",
        output_wav_path=output_wav,
        api_key="api_key_123",
        session=mock_session,
    )

    assert result == output_wav
    read_bytes = await asyncio.to_thread((tmp_path / "direct_gemini.wav").read_bytes)
    assert read_bytes == fake_audio_bytes

    # Verify payload contains personality conditioning and voice
    assert mock_session.post.call_count == 1
    call_args = mock_session.post.call_args
    assert "api_key_123" in call_args[0][0]
    payload = call_args[1]["json"]
    prompt_sent = payload["contents"][0]["parts"][0]["text"]
    assert "news_anchor" in prompt_sent
    assert "Hello world" in prompt_sent
    assert (
        payload["generationConfig"]["speechConfig"]["voiceConfig"][
            "prebuiltVoiceConfig"
        ]["voiceName"]
        == "Pulcherrima"
    )


@pytest.mark.asyncio
async def test_synthesize_gemini_tts_api_error(tmp_path: Path) -> None:
    """Test Gemini TTS raises TTSClientError on non-200 API response."""
    output_wav = str(tmp_path / "error.wav")
    mock_resp = AsyncMock()
    mock_resp.status = 400
    mock_resp.text = AsyncMock(return_value="Invalid API key")

    mock_context = MagicMock()
    mock_context.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_context.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.post.return_value = mock_context

    with pytest.raises(TTSClientError, match="Gemini TTS API returned status 400"):
        await _synthesize_gemini_tts(
            script="Failing test",
            voice="Pulcherrima",
            personality="calm",
            output_wav_path=output_wav,
            api_key="bad_key",
            session=mock_session,
        )


@pytest.mark.asyncio
async def test_synthesize_speech_gemini_error_falls_back_to_ha(tmp_path: Path) -> None:
    """Test that if Gemini TTS fails with error, it falls back to Home Assistant TTS."""
    hass = MagicMock()
    output_wav = str(tmp_path / "fallback.wav")
    creds = {"platform": "gemini", "api_key": "failing_key"}

    with (
        patch(
            "custom_components.automation_generator.tts_client._synthesize_gemini_tts",
            side_effect=TTSClientError("Network error"),
        ),
        patch(
            "custom_components.automation_generator.tts_client._synthesize_ha_tts",
            new=AsyncMock(return_value=output_wav),
        ) as mock_ha,
    ):
        result = await async_synthesize_speech(
            hass=hass,
            script="Testing fallback",
            voice="Pulcherrima",
            personality="calm",
            output_wav_path=output_wav,
            creds=creds,
        )
        assert result == output_wav
        mock_ha.assert_awaited_once_with(
            hass,
            "Testing fallback",
            "Pulcherrima",
            "calm",
            output_wav,
            target_engine=None,
        )


@pytest.mark.asyncio
async def test_openai_voice_mapping_dispatch(tmp_path: Path) -> None:
    """Test OpenAI voice mapping Pulcherrima -> nova and Enceladus -> onyx."""
    hass = MagicMock()
    output_wav = str(tmp_path / "openai_out.wav")
    creds = {"platform": "openai", "api_key": "sk-test123"}

    with patch(
        "custom_components.automation_generator.tts_client._synthesize_openai_tts",
        new=AsyncMock(return_value=output_wav),
    ) as mock_openai:
        # Pulcherrima -> nova
        res1 = await async_synthesize_speech(
            hass=hass,
            script="Good day",
            voice="Pulcherrima",
            personality="news_anchor",
            output_wav_path=output_wav,
            creds=creds,
        )
        assert res1 == output_wav
        mock_openai.assert_awaited_with(
            "Good day", "nova", "news_anchor", output_wav, "sk-test123"
        )

        # Enceladus -> onyx
        res2 = await async_synthesize_speech(
            hass=hass,
            script="Scripture reading",
            voice="Enceladus",
            personality="life_coach",
            output_wav_path=output_wav,
            creds=creds,
        )
        assert res2 == output_wav
        mock_openai.assert_awaited_with(
            "Scripture reading", "onyx", "life_coach", output_wav, "sk-test123"
        )


def test_map_voice_for_openai() -> None:
    """Test voice mapping helper directly."""
    assert map_voice_for_openai("Pulcherrima") == "nova"
    assert map_voice_for_openai("Enceladus") == "onyx"
    assert map_voice_for_openai("alloy") == "alloy"
    assert map_voice_for_openai("UnknownVoice") == "nova"


@pytest.mark.asyncio
async def test_synthesize_openai_tts_direct_api(tmp_path: Path) -> None:
    """Test direct REST invocation of OpenAI TTS writing raw bytes to file."""
    output_wav = str(tmp_path / "openai_direct.wav")
    raw_audio = b"RIFFfake_openai_raw_wav_bytes"

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.read = AsyncMock(return_value=raw_audio)

    mock_context = MagicMock()
    mock_context.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_context.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.post.return_value = mock_context

    result = await _synthesize_openai_tts(
        script="OpenAI spoken text",
        voice="nova",
        personality="conversational",
        output_wav_path=output_wav,
        api_key="sk-abc",
        session=mock_session,
    )

    assert result == output_wav
    read_bytes = await asyncio.to_thread((tmp_path / "openai_direct.wav").read_bytes)
    assert read_bytes == raw_audio

    mock_session.post.assert_called_once()
    call_args = mock_session.post.call_args
    assert call_args[0][0] == "https://api.openai.com/v1/audio/speech"
    assert call_args[1]["headers"]["Authorization"] == "Bearer sk-abc"
    assert call_args[1]["json"]["voice"] == "nova"
    assert call_args[1]["json"]["input"] == "OpenAI spoken text"


@pytest.mark.asyncio
async def test_synthesize_openai_tts_api_error(tmp_path: Path) -> None:
    """Test OpenAI TTS raises TTSClientError on error response."""
    output_wav = str(tmp_path / "openai_err.wav")
    mock_resp = AsyncMock()
    mock_resp.status = 401
    mock_resp.text = AsyncMock(return_value="Unauthorized")

    mock_context = MagicMock()
    mock_context.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_context.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.post.return_value = mock_context

    with pytest.raises(TTSClientError, match="OpenAI TTS API returned status 401"):
        await _synthesize_openai_tts(
            script="Test",
            voice="nova",
            personality="calm",
            output_wav_path=output_wav,
            api_key="sk-bad",
            session=mock_session,
        )


@pytest.mark.asyncio
async def test_synthesize_speech_conversation_agent_fallback_to_ha(
    tmp_path: Path,
) -> None:
    """Test fallback when platform is conversation_agent or no api_key."""
    hass = MagicMock()
    output_wav = str(tmp_path / "ha_out.wav")
    creds = {"platform": "conversation_agent", "api_key": None}

    with patch(
        "custom_components.automation_generator.tts_client._synthesize_ha_tts",
        new=AsyncMock(return_value=output_wav),
    ) as mock_ha:
        result = await async_synthesize_speech(
            hass=hass,
            script="Hello from conversation agent fallback",
            voice="Pulcherrima",
            personality="news_anchor",
            output_wav_path=output_wav,
            creds=creds,
        )
        assert result == output_wav
        mock_ha.assert_awaited_once_with(
            hass,
            "Hello from conversation agent fallback",
            "Pulcherrima",
            "news_anchor",
            output_wav,
            target_engine=None,
        )


@pytest.mark.asyncio
async def test_synthesize_ha_tts_direct_service_call(tmp_path: Path) -> None:
    """Test _synthesize_ha_tts calls Home Assistant tts.speak and generates output wav."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    output_wav = str(tmp_path / "ha_direct.wav")

    result = await _synthesize_ha_tts(
        hass=hass,
        script="Good evening, family",
        voice="Enceladus",
        personality="calm_reassuring",
        output_wav_path=output_wav,
        target_engine="tts.piper",
    )

    assert result == output_wav
    content = await asyncio.to_thread((tmp_path / "ha_direct.wav").read_bytes)
    assert content.startswith(b"RIFF")
    assert b"WAVE" in content

    hass.services.async_call.assert_awaited_once()
    call_args = hass.services.async_call.call_args
    assert call_args[0][0] == "tts"
    assert call_args[0][1] == "speak"
    data = call_args[0][2]
    assert data["message"] == "Good evening, family"
    assert data["target"]["entity_id"] == "tts.piper"
    assert data["options"]["voice"] == "Enceladus"
    assert data["options"]["personality"] == "calm_reassuring"


@pytest.mark.asyncio
async def test_synthesize_speech_defaults(tmp_path: Path) -> None:
    """Test default voice and personality are applied when omitted."""
    hass = MagicMock()
    output_wav = str(tmp_path / "defaults.wav")
    creds = {"platform": "gemini", "api_key": "k"}

    with patch(
        "custom_components.automation_generator.tts_client._synthesize_gemini_tts",
        new=AsyncMock(return_value=output_wav),
    ) as mock_tts:
        result = await async_synthesize_speech(
            hass=hass,
            script="Default testing",
            voice="",
            personality="",
            output_wav_path=output_wav,
            creds=creds,
        )
        assert result == output_wav
        mock_tts.assert_awaited_once_with(
            "Default testing",
            "Pulcherrima",
            "news_anchor",
            output_wav,
            "k",
        )
