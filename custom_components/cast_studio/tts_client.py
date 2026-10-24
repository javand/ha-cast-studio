"""Pluggable TTS client with per-action voice selection and personality conditioning."""

from __future__ import annotations

import asyncio
import base64
import re
import struct
from contextlib import suppress
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.components import tts as ha_tts
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import ffmpeg_runner
from .ai_health import async_report_ai_failure, async_report_ai_success
from .const import DEFAULT_HOST_VOICE, LOGGER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

DEFAULT_PERSONALITY = "news_anchor"
DEFAULT_GEMINI_SAMPLE_RATE = 24000

# Speech synthesis takes longer the longer the script: a 60-second scripture
# reading can need more than 30 s. Allow a base time plus time per word.
TTS_BASE_TIMEOUT_SECONDS = 30.0
TTS_SECONDS_PER_WORD = 0.3
TTS_MAX_TIMEOUT_SECONDS = 120.0


def tts_timeout(script: str) -> float:
    """Return how long to wait for a provider to voice ``script``."""
    words = len(script.split())
    return min(
        TTS_BASE_TIMEOUT_SECONDS + TTS_SECONDS_PER_WORD * words,
        TTS_MAX_TIMEOUT_SECONDS,
    )


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

PERSONALITY_PROMPTS: dict[str, str] = {
    "morning_host": (
        "# AUDIO PROFILE: Upbeat Morning Show Host (morning_host)\n"
        "## THE SCENE\n"
        "A bright, welcoming smart home morning broadcast studio.\n"
        "### DIRECTOR'S NOTES\n"
        "Style: Warm, energetic, articulate, and genuinely welcoming.\n"
        "Pacing: Natural, brisk morning cadence with a friendly smile in the voice."
    ),
    "news_anchor": (
        "# AUDIO PROFILE: Broadcast Meteorologist & Anchor (news_anchor)\n"
        "### DIRECTOR'S NOTES\n"
        "Style: Crisp, articulate, authoritative yet friendly morning broadcast.\n"
        "Pacing: Steady, rhythmic, emphasizing key numbers and temperatures."
    ),
    "cheerful": (
        "# AUDIO PROFILE: Warm Morning Companion (cheerful)\n"
        "### DIRECTOR'S NOTES\n"
        "Style: Upbeat, warm, genuinely welcoming, smiling tone.\n"
        "Pacing: Natural, clear, energetic morning cadence."
    ),
    "upbeat_energetic": (
        "# AUDIO PROFILE: Upbeat Morning Host (upbeat_energetic)\n"
        "### DIRECTOR'S NOTES\n"
        "Style: High-energy, enthusiastic, bright, motivating.\n"
        "Pacing: Lively and engaging."
    ),
    "conversational": (
        "# AUDIO PROFILE: Friendly Conversational Host (conversational)\n"
        "### DIRECTOR'S NOTES\n"
        "Style: Natural, relaxed, warm, relatable.\n"
        "Pacing: Easygoing conversational flow."
    ),
    "curious": (
        "# AUDIO PROFILE: Enthusiastic Storyteller (curious)\n"
        "### DIRECTOR'S NOTES\n"
        "Style: Fascinated, playful, engaging, conversational.\n"
        "Pacing: Dynamic with natural curiosity."
    ),
    "executive_assistant": (
        "# AUDIO PROFILE: Executive Chief of Staff (executive_assistant)\n"
        "### DIRECTOR'S NOTES\n"
        "Style: Organized, reassuring, sharp, focused, supportive.\n"
        "Pacing: Clear, structured delivery."
    ),
    "traffic_reporter": (
        "# AUDIO PROFILE: Live Traffic & Transit Correspondent (traffic_reporter)\n"
        "### DIRECTOR'S NOTES\n"
        "Style: Action-oriented, helpful, punchy, decisive.\n"
        "Pacing: Brisk and clear."
    ),
    "life_coach": (
        "# AUDIO PROFILE: Mindful Life Coach & Reader (life_coach)\n"
        "## THE SCENE\n"
        "A serene, sunlit study offering a grounded daily reflection.\n"
        "### DIRECTOR'S NOTES\n"
        "Style: Peaceful, grounded, warm, reflective, building to an uplifting, "
        "encouraging send-off.\n"
        "Pacing: Unhurried, deliberate, with gentle pauses between sentences."
    ),
    "contemplative": (
        "# AUDIO PROFILE: Calm Mindful Reader (contemplative)\n"
        "### DIRECTOR'S NOTES\n"
        "Style: Peaceful, grounded, warm, reflective, unhurried.\n"
        "Pacing: Slow, deliberate, with gentle pauses between sentences."
    ),
    "reverent": (
        "# AUDIO PROFILE: Reverent Scripture Reader (reverent)\n"
        "### DIRECTOR'S NOTES\n"
        "Style: Calm, respectful, warm, heartfelt.\n"
        "Pacing: Measured and clear."
    ),
    "warm_uplifting": (
        "# AUDIO PROFILE: Warm Uplifting Encourager (warm_uplifting)\n"
        "### DIRECTOR'S NOTES\n"
        "Style: Encouraging, compassionate, bright, reassuring.\n"
        "Pacing: Smooth and uplifting."
    ),
    "calm_reassuring": (
        "# AUDIO PROFILE: Calm Evening Companion (calm_reassuring)\n"
        "### DIRECTOR'S NOTES\n"
        "Style: Soft, reassuring, peaceful, and comforting.\n"
        "Pacing: Gentle, unhurried evening cadence."
    ),
    "soothing": (
        "# AUDIO PROFILE: Soothing Wind-Down Guide (soothing)\n"
        "### DIRECTOR'S NOTES\n"
        "Style: Tranquil, warm, serene, and deeply relaxing.\n"
        "Pacing: Slow, smooth, and restful."
    ),
    "meditative": (
        "# AUDIO PROFILE: Mindful Meditation Guide (meditative)\n"
        "### DIRECTOR'S NOTES\n"
        "Style: Centered, hushed, mindful, and peaceful.\n"
        "Pacing: Very unhurried with spacious pauses."
    ),
}


def register_personality_prompt(name: str, style_or_profile: str) -> None:
    """Register or override a custom TTS personality director profile."""
    clean_key = name.strip().lower().replace(" ", "_")
    if not clean_key:
        return
    if "# AUDIO PROFILE:" in style_or_profile:
        PERSONALITY_PROMPTS[clean_key] = style_or_profile.strip()
    else:
        PERSONALITY_PROMPTS[clean_key] = (
            f"# AUDIO PROFILE: Custom Persona ({clean_key})\n"
            "### DIRECTOR'S NOTES\n"
            f"{style_or_profile.strip()}"
        )


def _build_gemini_tts_prompt(script: str, personality: str) -> str:
    """Build structured Gemini 2.5 TTS director prompt with transcript delimiter."""
    lookup_key = personality.strip().lower().replace(" ", "_")
    profile = PERSONALITY_PROMPTS.get(personality) or PERSONALITY_PROMPTS.get(
        lookup_key,
        (
            f"# AUDIO PROFILE: Narrator ({personality})\n"
            "### DIRECTOR'S NOTES\n"
            f"Style: {personality}, natural, clear, and expressive."
        ),
    )
    return (
        f"{profile}\n\n"
        "Speak only the transcript below. "
        "Do not read any headers or instructions aloud.\n"
        f"#### TRANSCRIPT\n{script}"
    )


class TTSClientError(Exception):
    """Base exception for TTS client operations."""

    def __init__(self, msg: str, *, status: int | None = None, body: str = "") -> None:
        """Keep the provider's HTTP status and error body for account checks."""
        super().__init__(msg)
        self.status = status
        self.body = body


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
    model: str = "gemini-2.5-flash-preview-tts",
) -> str:
    """Synthesize speech using Gemini audio generation API with conditioning."""
    target_path = Path(output_wav_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        f"?key={api_key}"
    )
    prompt_text = _build_gemini_tts_prompt(script, personality)
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
            async with asyncio.timeout(tts_timeout(script)):
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
            raise TTSClientError(msg, status=status, body=err_body)

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
            async with asyncio.timeout(tts_timeout(script)):
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
            raise TTSClientError(msg, status=status, body=err_body)

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
    """Fallback speech synthesis using Home Assistant TTS media source."""
    del personality  # Reserved for future engine-specific options
    target_path = Path(output_wav_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if voice:
            try:
                media_source_id = ha_tts.generate_media_source_id(
                    hass,
                    script,
                    engine=target_engine,
                    options={"voice": voice},
                )
            except (HomeAssistantError, TypeError):
                media_source_id = ha_tts.generate_media_source_id(
                    hass,
                    script,
                    engine=target_engine,
                )
        else:
            media_source_id = ha_tts.generate_media_source_id(
                hass,
                script,
                engine=target_engine,
            )
        extension, data = await ha_tts.async_get_media_source_audio(
            hass, media_source_id
        )
        if data:
            if extension == "wav" or data.startswith(b"RIFF"):
                await asyncio.to_thread(target_path.write_bytes, data)
                return output_wav_path

            temp_file = target_path.with_suffix(f".{extension or 'tmp'}")
            await asyncio.to_thread(temp_file.write_bytes, data)

            ffmpeg_cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(temp_file),
                "-acodec",
                "pcm_s16le",
                "-ar",
                "24000",
                "-ac",
                "1",
                str(target_path),
            ]
            conv_ok = await ffmpeg_runner.async_run_ffmpeg(hass, ffmpeg_cmd)
            with suppress(OSError):
                await asyncio.to_thread(temp_file.unlink, missing_ok=True)
            if conv_ok and target_path.exists() and target_path.stat().st_size > 0:
                return output_wav_path
    except (HomeAssistantError, Exception) as err:  # noqa: BLE001
        LOGGER.warning(
            "Home Assistant TTS media synthesis encountered warning/error: %s",
            err,
        )

    def _is_empty_or_missing(p: Path) -> bool:
        return not p.exists() or p.stat().st_size == 0

    if await asyncio.to_thread(_is_empty_or_missing, target_path):
        LOGGER.error(
            "All TTS engines failed for %s; writing silent fallback WAV",
            output_wav_path,
        )
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
            wav_path = await _synthesize_gemini_tts(
                script,
                effective_voice,
                effective_personality,
                output_wav_path,
                api_key,
                session=session,
            )
        except TTSClientError as err:
            async_report_ai_failure(hass, "Gemini", "voice", err.status, err.body)
            LOGGER.warning(
                "Gemini TTS synthesis failed (%s), falling back to Home Assistant TTS",
                err,
            )
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            LOGGER.warning(
                "Gemini TTS synthesis failed (%s), falling back to Home Assistant TTS",
                err,
            )
        else:
            async_report_ai_success(hass, "voice")
            return wav_path

    elif platform == "openai" and api_key:
        try:
            openai_voice = map_voice_for_openai(effective_voice)
            wav_path = await _synthesize_openai_tts(
                script,
                openai_voice,
                effective_personality,
                output_wav_path,
                api_key,
                session=session,
            )
        except TTSClientError as err:
            async_report_ai_failure(hass, "OpenAI", "voice", err.status, err.body)
            LOGGER.warning(
                "OpenAI TTS synthesis failed (%s), falling back to Home Assistant TTS",
                err,
            )
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            LOGGER.warning(
                "OpenAI TTS synthesis failed (%s), falling back to Home Assistant TTS",
                err,
            )
        else:
            async_report_ai_success(hass, "voice")
            return wav_path

    return await _synthesize_ha_tts(
        hass,
        script,
        effective_voice,
        effective_personality,
        output_wav_path,
        target_engine=safe_creds.get("tts_engine"),
    )
