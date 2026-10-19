"""Tests for pluggable TTS client with per-action voice and personality conditioning."""

from __future__ import annotations

import asyncio
import base64
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.cast_studio.tts_client import (
    TTSClientError,
    _synthesize_gemini_tts,
    _synthesize_ha_tts,
    _synthesize_openai_tts,
    async_synthesize_speech,
    map_voice_for_openai,
)


@pytest.mark.asyncio
async def test_synthesize_speech_gemini_voices(tmp_path: Path) -> None:
    """Test Gemini TTS invocation with host voice and personality conditioning (verbatim brief test)."""
    hass = MagicMock()
    output_wav = str(tmp_path / "slide_01.wav")
    creds = {"platform": "gemini", "api_key": "test_key"}

    with patch(
        "custom_components.cast_studio.tts_client._synthesize_gemini_tts",
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
            session=ANY,
        )


@pytest.mark.asyncio
async def test_synthesize_speech_gemini_enceladus_voice(tmp_path: Path) -> None:
    """Test Gemini TTS invocation with secondary scripture voice and life coach personality."""
    hass = MagicMock()
    output_wav = str(tmp_path / "slide_02.wav")
    creds = {"platform": "gemini", "api_key": "test_gemini_key"}

    with patch(
        "custom_components.cast_studio.tts_client._synthesize_gemini_tts",
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
            session=ANY,
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
async def test_gemini_raw_pcm_wrapped_in_riff_header(tmp_path: Path) -> None:
    """Test Gemini raw 16-bit PCM audio is automatically wrapped in a valid RIFF WAVE header."""
    output_wav = str(tmp_path / "gemini_pcm.wav")
    # Raw PCM bytes without RIFF header (24000Hz, 16-bit mono = 2 bytes per sample)
    raw_pcm_samples = b"\x00\x00\x10\x00\x20\x00\x30\x00" * 3000  # 24000 bytes = 0.5s
    b64_pcm = base64.b64encode(raw_pcm_samples).decode("ascii")

    mock_resp_json = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "audio/pcm;rate=24000",
                                "data": b64_pcm,
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
        script="Testing raw PCM wrapping",
        voice="Pulcherrima",
        personality="news_anchor",
        output_wav_path=output_wav,
        api_key="api_key_123",
        session=mock_session,
    )

    assert result == output_wav
    read_bytes = await asyncio.to_thread((tmp_path / "gemini_pcm.wav").read_bytes)
    assert read_bytes.startswith(b"RIFF")
    assert b"WAVE" in read_bytes

    # Verify standard wave module can read audio params cleanly
    def _read_wav_params(wav_path: str) -> tuple[int, int, int]:
        with wave.open(wav_path, "rb") as wf:
            return wf.getnchannels(), wf.getsampwidth(), wf.getframerate()

    nchannels, sampwidth, framerate = await asyncio.to_thread(
        _read_wav_params, output_wav
    )
    expected_channels = 1
    expected_sampwidth = 2
    expected_framerate = 24000
    assert nchannels == expected_channels
    assert sampwidth == expected_sampwidth
    assert framerate == expected_framerate


@pytest.mark.asyncio
async def test_gemini_response_scans_multiple_parts(tmp_path: Path) -> None:
    """Test Gemini response parser iterates across content parts to find inlineData."""
    output_wav = str(tmp_path / "multipart_gemini.wav")
    fake_wav = b"RIFFfake_gemini_multipart_audio"
    b64_audio = base64.b64encode(fake_wav).decode("ascii")

    mock_resp_json = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "Thought: speaking clearly with news anchor cadence."},
                        {
                            "inlineData": {
                                "mimeType": "audio/wav",
                                "data": b64_audio,
                            }
                        },
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
        script="Scanning parts test",
        voice="Pulcherrima",
        personality="news_anchor",
        output_wav_path=output_wav,
        api_key="key",
        session=mock_session,
    )

    assert result == output_wav
    read_bytes = await asyncio.to_thread((tmp_path / "multipart_gemini.wav").read_bytes)
    assert read_bytes == fake_wav


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
            "custom_components.cast_studio.tts_client._synthesize_gemini_tts",
            side_effect=TTSClientError("Network error"),
        ),
        patch(
            "custom_components.cast_studio.tts_client._synthesize_ha_tts",
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
        "custom_components.cast_studio.tts_client._synthesize_openai_tts",
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
            "Good day",
            "nova",
            "news_anchor",
            output_wav,
            "sk-test123",
            session=ANY,
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
            "Scripture reading",
            "onyx",
            "life_coach",
            output_wav,
            "sk-test123",
            session=ANY,
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
        "custom_components.cast_studio.tts_client._synthesize_ha_tts",
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
async def test_synthesize_ha_tts_media_source_call(tmp_path: Path) -> None:
    """Test _synthesize_ha_tts retrieves audio bytes via Home Assistant TTS media source."""
    hass = MagicMock()
    output_wav = str(tmp_path / "ha_direct.wav")
    fake_wav_bytes = (
        b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
        b"\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    )

    with (
        patch(
            "homeassistant.components.tts.generate_media_source_id",
            return_value="media-source://tts/tts.piper?message=Good+evening",
        ) as mock_gen_id,
        patch(
            "homeassistant.components.tts.async_get_media_source_audio",
            new=AsyncMock(return_value=("wav", fake_wav_bytes)),
        ) as mock_get_audio,
    ):
        result = await _synthesize_ha_tts(
            hass,
            "Good evening, family",
            "Enceladus",
            "calm_reassuring",
            output_wav,
            target_engine="tts.piper",
        )

        assert result == output_wav
        mock_gen_id.assert_called_once_with(
            hass,
            "Good evening, family",
            engine="tts.piper",
            options={"voice": "Enceladus"},
        )
        mock_get_audio.assert_awaited_once_with(
            hass,
            "media-source://tts/tts.piper?message=Good+evening",
        )
        content = await asyncio.to_thread((tmp_path / "ha_direct.wav").read_bytes)
        assert content == fake_wav_bytes


@pytest.mark.asyncio
async def test_synthesize_ha_tts_converts_mp3_to_wav(tmp_path: Path) -> None:
    """Test _synthesize_ha_tts converts non-WAV media formats via ffmpeg."""
    hass = MagicMock()
    output_wav = str(tmp_path / "ha_converted.wav")
    fake_mp3_bytes = b"ID3\x03\x00\x00\x00\x00\x00\x00"

    async def fake_ffmpeg(_hass_arg: Any, cmd: Sequence[str]) -> bool:
        Path(cmd[-1]).write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10")
        return True

    with (
        patch(
            "homeassistant.components.tts.generate_media_source_id",
            return_value="media-source://tts/tts.google_ai_tts?message=Hello",
        ),
        patch(
            "homeassistant.components.tts.async_get_media_source_audio",
            new=AsyncMock(return_value=("mp3", fake_mp3_bytes)),
        ),
        patch(
            "custom_components.cast_studio.ffmpeg_runner.async_run_ffmpeg",
            side_effect=fake_ffmpeg,
        ) as mock_ffmpeg,
    ):
        result = await _synthesize_ha_tts(
            hass,
            "Hello",
            "Pulcherrima",
            "news_anchor",
            output_wav,
            target_engine="tts.google_ai_tts",
        )

        assert result == output_wav
        assert mock_ffmpeg.called
        content = await asyncio.to_thread((tmp_path / "ha_converted.wav").read_bytes)
        assert content.startswith(b"RIFF")


@pytest.mark.asyncio
async def test_synthesize_ha_tts_catches_home_assistant_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Test _synthesize_ha_tts catches HomeAssistantError, logs error, and falls back to silent wav."""
    hass = MagicMock()
    hass.services.async_call = AsyncMock(
        side_effect=HomeAssistantError("Service tts.speak not found")
    )
    output_wav = str(tmp_path / "ha_error.wav")

    result = await _synthesize_ha_tts(
        hass=hass,
        script="Error fallback test",
        voice="Pulcherrima",
        personality="calm",
        output_wav_path=output_wav,
        target_engine="tts.invalid",
    )

    assert result == output_wav
    content = await asyncio.to_thread((tmp_path / "ha_error.wav").read_bytes)
    assert content.startswith(b"RIFF")
    assert b"WAVE" in content
    assert (
        f"All TTS engines failed for {output_wav}; writing silent fallback WAV"
        in caplog.text
    )


@pytest.mark.asyncio
async def test_async_synthesize_speech_handles_none_creds(tmp_path: Path) -> None:
    """Test async_synthesize_speech safely handles creds=None without error."""
    hass = MagicMock()
    output_wav = str(tmp_path / "none_creds.wav")

    with patch(
        "custom_components.cast_studio.tts_client._synthesize_ha_tts",
        new=AsyncMock(return_value=output_wav),
    ) as mock_ha:
        result = await async_synthesize_speech(
            hass=hass,
            script="None creds test",
            voice="Pulcherrima",
            personality="news_anchor",
            output_wav_path=output_wav,
            creds=None,  # type: ignore[arg-type]
        )
        assert result == output_wav
        mock_ha.assert_awaited_once_with(
            hass,
            "None creds test",
            "Pulcherrima",
            "news_anchor",
            output_wav,
            target_engine=None,
        )


@pytest.mark.asyncio
async def test_synthesize_speech_defaults(tmp_path: Path) -> None:
    """Test default voice and personality are applied when omitted."""
    hass = MagicMock()
    output_wav = str(tmp_path / "defaults.wav")
    creds = {"platform": "gemini", "api_key": "k"}

    with patch(
        "custom_components.cast_studio.tts_client._synthesize_gemini_tts",
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
            session=ANY,
        )


@pytest.mark.asyncio
async def test_synthesize_speech_reuses_clientsession(tmp_path: Path) -> None:
    """Verify that async_synthesize_speech reuses async_get_clientsession(hass)."""
    hass = MagicMock()
    output_wav = str(tmp_path / "session_reuse.wav")
    creds = {"platform": "gemini", "api_key": "k"}
    mock_session = MagicMock()

    with (
        patch(
            "custom_components.cast_studio.tts_client.async_get_clientsession",
            return_value=mock_session,
        ) as mock_get_session,
        patch(
            "custom_components.cast_studio.tts_client._synthesize_gemini_tts",
            new=AsyncMock(return_value=output_wav),
        ) as mock_gemini,
    ):
        result = await async_synthesize_speech(
            hass=hass,
            script="Session test",
            voice="Pulcherrima",
            personality="news_anchor",
            output_wav_path=output_wav,
            creds=creds,
        )
        assert result == output_wav
        mock_get_session.assert_called_once_with(hass)
        mock_gemini.assert_awaited_once_with(
            "Session test",
            "Pulcherrima",
            "news_anchor",
            output_wav,
            "k",
            session=mock_session,
        )


@pytest.mark.asyncio
async def test_synthesize_gemini_tts_structured_director_prompt(tmp_path: Path) -> None:
    """Test Gemini TTS uses structured # AUDIO PROFILE and #### TRANSCRIPT blocks."""
    output_wav = str(tmp_path / "structured_gemini.wav")
    b64_audio = base64.b64encode(b"RIFFfake_wav_bytes").decode("ascii")
    mock_resp_json = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"inlineData": {"mimeType": "audio/wav", "data": b64_audio}}
                    ]
                }
            }
        ]
    }
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=mock_resp_json)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_session = MagicMock()
    mock_session.post.return_value = mock_ctx

    await _synthesize_gemini_tts(
        script="Good morning, Jay-ven! It is 68 degrees and sunny.",
        voice="Pulcherrima",
        personality="news_anchor",
        output_wav_path=output_wav,
        api_key="test_key",
        session=mock_session,
    )

    payload = mock_session.post.call_args[1]["json"]
    prompt_sent = payload["contents"][0]["parts"][0]["text"]
    assert "# AUDIO PROFILE:" in prompt_sent
    assert "news_anchor" in prompt_sent
    assert (
        "#### TRANSCRIPT\nGood morning, Jay-ven! It is 68 degrees and sunny."
        in prompt_sent
    )
    assert "Please speak the following narration text" not in prompt_sent


@pytest.mark.asyncio
async def test_synthesize_ha_tts_voice_options_rejected_fallback(
    tmp_path: Path,
) -> None:
    """Test _synthesize_ha_tts falls back to calling generate_media_source_id without options when rejected."""
    hass = MagicMock()
    output_wav = str(tmp_path / "ha_voice_fallback.wav")
    fake_wav_bytes = (
        b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
        b"\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    )

    def side_effect_gen_id(
        _hass_arg: Any,
        _msg: str,
        engine: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> str:
        if options is not None:
            msg = f"Invalid options supported: {options}"
            raise HomeAssistantError(msg)
        return f"media-source://tts/{engine}?message=Fallback"

    with (
        patch(
            "homeassistant.components.tts.generate_media_source_id",
            side_effect=side_effect_gen_id,
        ) as mock_gen_id,
        patch(
            "homeassistant.components.tts.async_get_media_source_audio",
            new=AsyncMock(return_value=("wav", fake_wav_bytes)),
        ) as mock_get_audio,
    ):
        result = await _synthesize_ha_tts(
            hass,
            "Fallback test",
            "Pulcherrima",
            "news_anchor",
            output_wav,
            target_engine="tts.legacy_engine",
        )

        assert result == output_wav
        expected_calls = 2
        assert mock_gen_id.call_count == expected_calls
        mock_gen_id.assert_any_call(
            hass,
            "Fallback test",
            engine="tts.legacy_engine",
            options={"voice": "Pulcherrima"},
        )
        mock_gen_id.assert_called_with(
            hass,
            "Fallback test",
            engine="tts.legacy_engine",
        )
        mock_get_audio.assert_awaited_once_with(
            hass,
            "media-source://tts/tts.legacy_engine?message=Fallback",
        )
        content = await asyncio.to_thread(
            (tmp_path / "ha_voice_fallback.wav").read_bytes
        )
        assert content == fake_wav_bytes
