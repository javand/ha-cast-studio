"""Pluggable TTS client with per-action voice selection and personality conditioning."""

from __future__ import annotations

import asyncio
import base64
import re
import struct
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DEFAULT_HOST_VOICE, LOGGER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

DEFAULT_PERSONALITY = "news_anchor"
DEFAULT_GEMINI_SAMPLE_RATE = 24000

OPENAI_VOICE_MAP: dict[str, str] = {
    "Pulcherrima": "nova",
    "Enceladus": "onyx",
}

OPENAI_KNOWN_VOICES: set[str] = {
    "alloy",
    "echo",
    "fable",
    "onyx",
    "nova",
    "shimmer",
}


class TTSClientError(Exception):
    """Base exception for TTS client operations."""


def map_voice_for_openai(voice: str) -> str:
    """Map routine voice name to OpenAI-compatible voice identifier."""
    if voice in OPENAI_VOICE_MAP:
        return OPENAI_VOICE_MAP[voice]
    if voice.lower() in OPENAI_KNOWN_VOICES:
        return voice.lower()
    return "nova"


def _generate_silent_wav(
    duration_seconds: float = 1.0,
    sample_rate: int = 16000,
) -> bytes:
    """Generate minimal valid PCM 16-bit mono WAV bytes for fallback playback."""
    num_samples = int(duration_seconds * sample_rate)
    num_channels = 1
    bytes_per_sample = 2
    byte_rate = sample_rate * num_channels * bytes_per_sample
    block_align = num_channels * bytes_per_sample
    data_size = num_samples * block_align
    chunk_size = 36 + data_size

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        chunk_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        16,
        b"data",
        data_size,
    )
    return header + (b"\x00" * data_size)


def _wrap_pcm_in_wav(
    pcm_data: bytes,
    sample_rate: int = DEFAULT_GEMINI_SAMPLE_RATE,
    num_channels: int = 1,
) -> bytes:
    """Wrap raw 16-bit PCM audio in standard RIFF WAVE container."""
    bytes_per_sample = 2
    byte_rate = sample_rate * num_channels * bytes_per_sample
    block_align = num_channels * bytes_per_sample
    data_size = len(pcm_data)
    chunk_size = 36 + data_size

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        chunk_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        16,
        b"data",
        data_size,
    )
    return header + pcm_data


def _extract_gemini_audio(data: dict[str, Any] | None) -> tuple[bytes, str]:
    """Extract audio bytes and mime type from Gemini API response data."""
    if not data:
        msg = "No audio content returned from Gemini TTS API"
        raise TTSClientError(msg)

    try:
        if "candidates" in data:
            for candidate in data.get("candidates", []):
                content = candidate.get("content", {})
                for part in content.get("parts", []):
                    if isinstance(part, dict) and "inlineData" in part:
                        inline_data = part["inlineData"]
                        if "data" in inline_data:
                            audio_bytes = base64.b64decode(inline_data["data"])
                            mime_type = str(inline_data.get("mimeType", ""))
                            return audio_bytes, mime_type
        if "audioContent" in data:
            return base64.b64decode(data["audioContent"]), ""
    except (KeyError, IndexError, TypeError, ValueError) as err:
        msg = f"Failed to extract audio from Gemini TTS response: {err}"
        raise TTSClientError(msg) from err

    msg = "No audio content returned from Gemini TTS API"
    raise TTSClientError(msg)


async def _synthesize_gemini_tts(  # noqa: PLR0913
    script: str,
    voice: str,
    personality: str,
    output_wav_path: str,
    api_key: str,
    session: aiohttp.ClientSession | None = None,
    model: str = "gemini-2.0-flash",
) -> str:
    """Synthesize speech using Gemini audio generation API with conditioning."""
    target_path = Path(output_wav_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        f"?key={api_key}"
    )
    prompt_text = (
        f"Please speak the following narration text with a {personality} "
        f"personality and style.\n\n{script}"
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt_text},
                ],
            },
        ],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice,
                    },
                },
            },
        },
    }

    async def _execute(sess: aiohttp.ClientSession) -> str:
        try:
            async with asyncio.timeout(30):
                async with sess.post(url, json=payload) as resp:
                    status = resp.status
                    if status != HTTPStatus.OK:
                        err_body = await resp.text()
                        data = None
                    else:
                        err_body = ""
                        data = await resp.json()
        except TimeoutError as err:
            msg = "Timeout communicating with Gemini TTS API"
            raise TTSClientError(msg) from err
        except aiohttp.ClientError as err:
            msg = f"Network error communicating with Gemini TTS API: {err}"
            raise TTSClientError(msg) from err
        except Exception as err:
            msg = f"Unexpected error calling Gemini TTS API: {err}"
            raise TTSClientError(msg) from err

        if status != HTTPStatus.OK:
            msg = f"Gemini TTS API returned status {status}: {err_body}"
            raise TTSClientError(msg)

        audio_bytes, mime_type = _extract_gemini_audio(data)

        if not audio_bytes.startswith(b"RIFF"):
            sample_rate = DEFAULT_GEMINI_SAMPLE_RATE
            if "rate=" in mime_type:
                match = re.search(r"rate=(\d+)", mime_type)
                if match:
                    sample_rate = int(match.group(1))
            audio_bytes = _wrap_pcm_in_wav(audio_bytes, sample_rate=sample_rate)

        await asyncio.to_thread(target_path.write_bytes, audio_bytes)
        return output_wav_path

    if session is not None:
        return await _execute(session)
    async with aiohttp.ClientSession() as new_session:
        return await _execute(new_session)


async def _synthesize_openai_tts(  # noqa: PLR0913
    script: str,
    voice: str,
    personality: str,
    output_wav_path: str,
    api_key: str,
    session: aiohttp.ClientSession | None = None,
    model: str = "tts-1",
) -> str:
    """Synthesize speech using OpenAI audio speech API."""
    del personality  # Personality style is handled via LLM generation conditioning
    target_path = Path(output_wav_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    url = "https://api.openai.com/v1/audio/speech"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "input": script,
        "voice": voice,
        "response_format": "wav",
    }

    async def _execute(sess: aiohttp.ClientSession) -> str:
        try:
            async with asyncio.timeout(30):
                async with sess.post(url, headers=headers, json=payload) as resp:
                    status = resp.status
                    if status != HTTPStatus.OK:
                        err_body = await resp.text()
                        audio_bytes = None
                    else:
                        err_body = ""
                        audio_bytes = await resp.read()
        except TimeoutError as err:
            msg = "Timeout communicating with OpenAI TTS API"
            raise TTSClientError(msg) from err
        except aiohttp.ClientError as err:
            msg = f"Network error communicating with OpenAI TTS API: {err}"
            raise TTSClientError(msg) from err
        except Exception as err:
            msg = f"Unexpected error calling OpenAI TTS API: {err}"
            raise TTSClientError(msg) from err

        if status != HTTPStatus.OK:
            msg = f"OpenAI TTS API returned status {status}: {err_body}"
            raise TTSClientError(msg)

        if not audio_bytes:
            msg = "No audio content returned from OpenAI TTS API"
            raise TTSClientError(msg)

        await asyncio.to_thread(target_path.write_bytes, audio_bytes)
        return output_wav_path

    if session is not None:
        return await _execute(session)
    async with aiohttp.ClientSession() as new_session:
        return await _execute(new_session)


async def _synthesize_ha_tts(  # noqa: PLR0913
    hass: HomeAssistant,
    script: str,
    voice: str,
    personality: str,
    output_wav_path: str,
    target_engine: str | None = None,
) -> str:
    """Fallback speech synthesis using Home Assistant tts.speak service."""
    target_path = Path(output_wav_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    service_data: dict[str, Any] = {
        "message": script,
    }
    options: dict[str, Any] = {}
    if voice:
        options["voice"] = voice
    if personality:
        options["personality"] = personality
    if options:
        service_data["options"] = options

    call_kwargs: dict[str, Any] = {
        "blocking": True,
    }
    if target_engine:
        call_kwargs["target"] = {"entity_id": target_engine}

    try:
        await hass.services.async_call(
            "tts",
            "speak",
            service_data,
            **call_kwargs,
        )
    except (HomeAssistantError, Exception) as err:  # noqa: BLE001
        LOGGER.warning(
            "Home Assistant tts.speak call encountered warning/error: %s",
            err,
        )

    if not target_path.exists() or target_path.stat().st_size == 0:
        await asyncio.to_thread(target_path.write_bytes, _generate_silent_wav())

    return output_wav_path


async def async_synthesize_speech(  # noqa: PLR0913
    hass: HomeAssistant,
    script: str,
    voice: str,
    personality: str,
    output_wav_path: str,
    creds: dict[str, Any] | None = None,
) -> str:
    """Synthesize speech with per-action voice and personality conditioning."""
    safe_creds = creds or {}
    effective_voice = voice.strip() if voice else DEFAULT_HOST_VOICE
    effective_personality = personality.strip() if personality else DEFAULT_PERSONALITY

    platform = safe_creds.get("platform", "conversation_agent")
    api_key = safe_creds.get("api_key")
    session = async_get_clientsession(hass)

    if platform == "gemini" and api_key:
        try:
            return await _synthesize_gemini_tts(
                script,
                effective_voice,
                effective_personality,
                output_wav_path,
                api_key,
                session=session,
            )
        except (TTSClientError, TimeoutError, aiohttp.ClientError, OSError) as err:
            LOGGER.warning(
                "Gemini TTS synthesis failed (%s), falling back to Home Assistant TTS",
                err,
            )

    elif platform == "openai" and api_key:
        try:
            openai_voice = map_voice_for_openai(effective_voice)
            return await _synthesize_openai_tts(
                script,
                openai_voice,
                effective_personality,
                output_wav_path,
                api_key,
                session=session,
            )
        except (TTSClientError, TimeoutError, aiohttp.ClientError, OSError) as err:
            LOGGER.warning(
                "OpenAI TTS synthesis failed (%s), falling back to Home Assistant TTS",
                err,
            )

    return await _synthesize_ha_tts(
        hass,
        script,
        effective_voice,
        effective_personality,
        output_wav_path,
        target_engine=safe_creds.get("tts_engine"),
    )
