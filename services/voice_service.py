"""
FundzAiBot — Voice message transcription service.

Uses Gemini 1.5 Flash multimodal audio understanding to transcribe:
  - Voice messages (OGG/Opus — Telegram default)
  - Audio files (MP3, WAV, M4A, OGG)

Requires GEMINI_API_KEY in Railway environment variables.
Falls back gracefully with an actionable error message if unavailable.

All functions are SYNCHRONOUS — call via run_in_executor from async handlers.
"""

import base64
import requests

from config.settings import GEMINI_API_KEY, AI_TIMEOUT
from utils.logger import get_logger

log = get_logger(__name__)

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_VISION_MODEL = "gemini-1.5-flash"

# Telegram voice messages are OGG/Opus; audio files vary
_MIME_MAP = {
    "oga":  "audio/ogg",
    "ogg":  "audio/ogg",
    "mp3":  "audio/mpeg",
    "wav":  "audio/wav",
    "m4a":  "audio/mp4",
    "flac": "audio/flac",
    "aac":  "audio/aac",
}


def mime_for_extension(ext: str) -> str:
    """Return the correct MIME type for a file extension."""
    return _MIME_MAP.get(ext.lower().lstrip("."), "audio/ogg")


def transcribe_voice(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    """
    Transcribe voice/audio bytes using Gemini 1.5 Flash.
    Returns the transcription string, or an error string starting with ⚠️.

    Args:
        audio_bytes: Raw audio bytes from Telegram's get_file() download
        mime_type:   MIME type — default "audio/ogg" (Telegram voice messages)

    Returns:
        str: Transcription text, or ⚠️-prefixed error message
    """
    if not GEMINI_API_KEY:
        return (
            "⚠️ <b>Voice transcription requires GEMINI_API_KEY.</b>\n"
            "Add it to your Railway environment variables to enable this feature."
        )

    if not audio_bytes:
        return "⚠️ Empty audio received — please try again."

    url = f"{_GEMINI_BASE}/{_VISION_MODEL}:generateContent?key={GEMINI_API_KEY}"

    try:
        payload = {
            "contents": [{
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64.b64encode(audio_bytes).decode("utf-8"),
                        }
                    },
                    {
                        "text": (
                            "Please transcribe this voice message verbatim and accurately. "
                            "Return ONLY the transcription — no labels, no commentary, no formatting. "
                            "If the audio is unclear or silent, say: [inaudible]"
                        )
                    },
                ]
            }],
            "generationConfig": {
                "maxOutputTokens": 1500,
                "temperature":     0.05,
            },
        }

        resp = requests.post(url, json=payload, timeout=(10, AI_TIMEOUT))

        if resp.status_code == 400:
            body = resp.text[:200]
            log.warning("Gemini voice 400: %s", body)
            return "⚠️ Could not process this audio format. Try a voice message instead."

        if resp.status_code == 403:
            log.warning("Gemini voice 403 — API key restricted")
            return "⚠️ GEMINI_API_KEY is invalid or restricted. Check Railway env vars."

        if resp.status_code == 429:
            log.warning("Gemini voice 429 — quota exceeded")
            return "⚠️ Gemini quota exceeded. Try again in a moment."

        resp.raise_for_status()
        data       = resp.json()
        candidates = data.get("candidates", [])

        if not candidates:
            finish = data.get("promptFeedback", {}).get("blockReason", "unknown")
            log.warning("Gemini voice: no candidates, blockReason=%s", finish)
            return "⚠️ Transcription blocked (content safety). Please try a different message."

        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            return "⚠️ Gemini returned an empty transcription."

        text = (parts[0].get("text") or "").strip()
        if not text:
            return "⚠️ Could not understand the audio. Please speak clearly and try again."

        log.info("Voice transcribed: mime=%s chars=%d", mime_type, len(text))
        return text

    except requests.Timeout:
        log.warning("Gemini voice: request timed out after %ds", AI_TIMEOUT)
        return "⚠️ Voice transcription timed out. Please try again."

    except requests.ConnectionError as exc:
        log.warning("Gemini voice: connection error — %s", str(exc)[:80])
        return "⚠️ Could not reach Gemini. Check Railway network connectivity."

    except Exception as exc:
        log.error("Gemini voice: unexpected error — %s", exc, exc_info=True)
        return f"⚠️ Transcription failed: {str(exc)[:100]}"
