"""
Content validation utilities.
All checks are purely local (no network calls).
"""

from __future__ import annotations

import re
from typing import Any

# Words/phrases that must not appear in generated scripts.
# Expand as needed for compliance.
FORBIDDEN_TERMS: list[str] = [
    "guaranteed returns",
    "risk-free",
    "get rich quick",
    "you will profit",
    "i guarantee",
    "buy now",
    "invest in this",
    "financial advice",
    "my recommendation is",
    "you should buy",
    "you should sell",
    "call me",
    "dm me",
    "wire me",
]

# Average speaking rate: ~150 words per minute for slow, ~180 for normal voice-over
WORDS_PER_SECOND_LOW  = 2.0   # 120 wpm  → very slow
WORDS_PER_SECOND_HIGH = 3.5   # 210 wpm  → fast


def estimate_duration(text: str, wps: float = 2.5) -> float:
    """Estimate voice-over duration in seconds from word count."""
    words = len(text.split())
    return words / wps


def validate_script(script: dict[str, Any], duration_target: int) -> list[str]:
    """
    Validate a generated script dict.
    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    # Required top-level keys
    required_keys = {"title", "hook", "voiceover", "scenes", "on_screen_captions", "hashtags", "disclaimer"}
    missing = required_keys - script.keys()
    if missing:
        errors.append(f"Missing keys in script: {missing}")
        return errors  # can't continue validation

    # Voiceover duration estimate
    voiceover_text: str = script.get("voiceover", "")
    estimated_s = estimate_duration(voiceover_text)
    tolerance = max(10, duration_target * 0.35)   # ±35% or at least ±10s

    if estimated_s < (duration_target - tolerance):
        errors.append(
            f"Voiceover too short: estimated {estimated_s:.1f}s, "
            f"target {duration_target}s (min {duration_target - tolerance:.1f}s)"
        )
    if estimated_s > (duration_target + tolerance):
        errors.append(
            f"Voiceover too long: estimated {estimated_s:.1f}s, "
            f"target {duration_target}s (max {duration_target + tolerance:.1f}s)"
        )

    # Forbidden terms (case-insensitive)
    full_text = " ".join([
        script.get("title", ""),
        script.get("hook", ""),
        voiceover_text,
        " ".join(script.get("on_screen_captions", [])),
    ]).lower()

    for term in FORBIDDEN_TERMS:
        if term in full_text:
            errors.append(f"Forbidden term found: '{term}'")

    # Scenes
    scenes = script.get("scenes", [])
    if not (2 <= len(scenes) <= 8):
        errors.append(f"Expected 2–8 scenes, got {len(scenes)}")
    for i, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            errors.append(f"Scene {i} must be a dict")
            continue
        if "description" not in scene:
            errors.append(f"Scene {i} missing 'description'")

    # Hashtags
    hashtags = script.get("hashtags", [])
    if not (3 <= len(hashtags) <= 30):
        errors.append(f"Expected 3–30 hashtags, got {len(hashtags)}")

    # Keywords (for asset mapping)
    keywords = script.get("keywords", [])
    if not keywords:
        errors.append("No keywords provided for asset mapping")

    # Disclaimer must mention educational
    disclaimer: str = script.get("disclaimer", "").lower()
    if "educational" not in disclaimer and "informational" not in disclaimer:
        errors.append("Disclaimer must contain 'educational' or 'informational'")

    return errors


def sanitize_filename(name: str, max_length: int = 80) -> str:
    """Convert a string to a safe filename."""
    name = re.sub(r"[^\w\s\-]", "", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name[:max_length].lower()
