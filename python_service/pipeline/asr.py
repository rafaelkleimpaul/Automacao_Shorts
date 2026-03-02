"""
Automatic Speech Recognition → SRT captions.

Primary path  : POST voice.wav to ASR endpoint → receive SRT text
Fallback path : Generate SRT from voiceover script using word-timing estimation
                (used when ASR endpoint is unavailable or fails)

ASR endpoint contract (see README):
  Request: multipart/form-data  { "audio": <wav file>, "language": "en_US" }
      OR   JSON                 { "audio_base64": "...", "language": "en_US" }
  Response JSON:
    { "srt": "1\n00:00:00,000 --> 00:00:02,500\nHello world\n\n2\n..." }
    OR
    { "text": "full transcript text" }   (will be converted to SRT)
"""

from __future__ import annotations

import base64
import logging
import os
import textwrap
from pathlib import Path
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

ASR_ENDPOINT: str = os.environ.get("ASR_ENDPOINT", "http://host.docker.internal:9000/transcribe")
TIMEOUT:      int = int(os.environ.get("ASR_TIMEOUT", "120"))


# ─────────────────────────────────────────────────────────────────────────────
# SRT helpers
# ─────────────────────────────────────────────────────────────────────────────

def _seconds_to_srt_time(seconds: float) -> str:
    hours   = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs    = int(seconds % 60)
    millis  = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def text_to_srt(text: str, total_duration: float, max_chars_per_line: int = 42) -> str:
    """
    Convert plain text to SRT by distributing words evenly over total_duration.
    Each subtitle block is max_chars_per_line characters wide.
    """
    words = text.split()
    if not words:
        return ""

    wps = len(words) / max(total_duration, 1)   # words per second
    srt_lines: list[str] = []
    index = 1
    current_time = 0.0

    # Group words into ~6-word chunks for subtitle blocks
    CHUNK_SIZE = 6
    chunks = [words[i : i + CHUNK_SIZE] for i in range(0, len(words), CHUNK_SIZE)]

    for chunk in chunks:
        chunk_text = " ".join(chunk)
        chunk_duration = len(chunk) / wps
        start = current_time
        end   = current_time + chunk_duration
        current_time = end

        # Wrap long lines
        wrapped = textwrap.fill(chunk_text, width=max_chars_per_line)

        srt_lines.append(
            f"{index}\n"
            f"{_seconds_to_srt_time(start)} --> {_seconds_to_srt_time(end)}\n"
            f"{wrapped}\n"
        )
        index += 1

    return "\n".join(srt_lines)


def plain_text_to_srt(raw_text: str, total_duration: float) -> str:
    """Alias for text_to_srt."""
    return text_to_srt(raw_text, total_duration)


# ─────────────────────────────────────────────────────────────────────────────
# ASR call
# ─────────────────────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=2, max=8), reraise=True)
def _call_asr(wav_path: Path) -> Optional[str]:
    """
    Call ASR endpoint. Returns SRT string or None on failure.
    Tries multipart upload first, then base64 JSON.
    """
    logger.info("Calling ASR at %s for %s", ASR_ENDPOINT, wav_path.name)

    with httpx.Client(timeout=TIMEOUT) as client:
        # Try multipart form upload
        with open(wav_path, "rb") as f:
            resp = client.post(
                ASR_ENDPOINT,
                files={"audio": (wav_path.name, f, "audio/wav")},
                data={"language": "en_US"},
            )

        if resp.status_code >= 400:
            # Fallback: base64 JSON
            audio_b64 = base64.b64encode(wav_path.read_bytes()).decode()
            resp = client.post(
                ASR_ENDPOINT,
                json={"audio_base64": audio_b64, "language": "en_US"},
            )
        resp.raise_for_status()
        data = resp.json()

    # Parse response
    if "srt" in data:
        return data["srt"]
    if "text" in data:
        return None   # caller will use fallback SRT generation with this text
    logger.warning("ASR response has no 'srt' or 'text' key: %s", list(data.keys()))
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────

def generate_srt(
    wav_path: Path,
    output_path: Path,
    fallback_text: str,
    audio_duration: float,
) -> Path:
    """
    Generate SRT captions:
      1. Try ASR endpoint
      2. Fall back to word-timing estimation from fallback_text

    Returns the output_path to the saved .srt file.
    """
    srt_content: Optional[str] = None

    try:
        srt_content = _call_asr(wav_path)
    except Exception as exc:
        logger.warning("ASR endpoint failed (%s), using fallback SRT generation", exc)

    if not srt_content:
        logger.info("Generating estimated SRT from voiceover text (%d chars)", len(fallback_text))
        srt_content = text_to_srt(fallback_text, audio_duration)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(srt_content, encoding="utf-8")
    logger.info("SRT saved: %s", output_path)
    return output_path
