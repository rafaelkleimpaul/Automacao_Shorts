"""
Text-to-Speech via a pluggable local TTS service.

Supported endpoint types:
  - Coqui TTS REST:       POST /api/tts  → returns audio/wav bytes
  - Piper / custom REST:  POST /synthesize → JSON with audio_base64 OR raw bytes
  - Kokoro / any REST:    POST /tts → same detection logic

Contract (see README):
  Request JSON:
    { "text": "...", "language": "en_US", "voice": "...", "speed": 1.0 }
  Response:
    Content-Type: audio/wav → raw WAV bytes
    OR
    Content-Type: application/json → { "audio_base64": "...", "format": "wav" }
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

TTS_ENDPOINT: str = os.environ.get("TTS_ENDPOINT", "http://host.docker.internal:8880/v1/audio/speech")
TTS_VOICE:    str = os.environ.get("TTS_VOICE", "af_heart")
TTS_SPEED:  float = float(os.environ.get("TTS_SPEED", "1.0"))
TIMEOUT:      int = int(os.environ.get("TTS_TIMEOUT", "120"))


def _build_payload(text: str) -> dict:
    """Build the TTS request payload based on the endpoint type."""
    endpoint = TTS_ENDPOINT.lower()
    if "v1/audio/speech" in endpoint:
        # OpenAI-compatible TTS API (Kokoro, OpenAI, etc.)
        return {
            "model": "kokoro",
            "input": text,
            "voice": TTS_VOICE,
            "response_format": "wav",
            "speed": TTS_SPEED,
        }
    # Generic / Coqui / Piper-style
    return {
        "text": text,
        "language": "en_US",
        "voice": TTS_VOICE,
        "speed": TTS_SPEED,
    }


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
def generate_voice(text: str, output_path: Path) -> Path:
    """
    Send text to TTS endpoint and save resulting WAV to output_path.
    Returns the output_path on success.
    """
    payload = _build_payload(text)

    logger.info("Calling TTS at %s (voice=%s, chars=%d)", TTS_ENDPOINT, TTS_VOICE, len(text))

    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(TTS_ENDPOINT, json=payload)
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")

        if "audio" in content_type or "octet-stream" in content_type:
            # Raw audio bytes
            audio_bytes = resp.content
        else:
            # JSON with base64
            data = resp.json()
            b64 = data.get("audio_base64") or data.get("audio") or data.get("data")
            if not b64:
                raise ValueError(f"TTS response JSON has no audio field: {list(data.keys())}")
            audio_bytes = base64.b64decode(b64)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(audio_bytes)
    logger.info("TTS audio saved: %s (%d bytes)", output_path, len(audio_bytes))
    return output_path


def get_audio_duration(wav_path: Path) -> float:
    """Return duration of a WAV file in seconds using soundfile."""
    try:
        import soundfile as sf
        info = sf.info(str(wav_path))
        return info.duration
    except Exception:
        # Fallback: parse WAV header manually
        try:
            import wave
            with wave.open(str(wav_path), "rb") as w:
                return w.getnframes() / w.getframerate()
        except Exception as exc:
            logger.warning("Could not determine audio duration: %s", exc)
            return 0.0
