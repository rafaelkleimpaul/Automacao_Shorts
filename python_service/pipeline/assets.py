"""
Local asset selection.
Scans /data/assets/broll/<profile>/ for video/image files and
selects assets that best match the scene keywords from the script.

NO downloads. All assets must be pre-placed by the user in the assets folder.
"""

from __future__ import annotations

import logging
import os
import random
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

DATA_ROOT:    Path = Path(os.environ.get("DATA_ROOT", "/data"))
ASSETS_ROOT:  Path = DATA_ROOT / "assets"
BROLL_ROOT:   Path = ASSETS_ROOT / "broll"
MUSIC_ROOT:   Path = ASSETS_ROOT / "music"

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MUSIC_EXTS = {".mp3", ".wav", ".ogg", ".aac", ".m4a"}


class Asset(NamedTuple):
    path: Path
    kind: str   # "video" | "image" | "music"
    score: int  # keyword match score


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _scan_dir(directory: Path, extensions: set[str]) -> list[Path]:
    if not directory.exists():
        return []
    return [
        p for p in directory.rglob("*")
        if p.is_file() and p.suffix.lower() in extensions
    ]


def _score_asset(path: Path, keywords: list[str]) -> int:
    """Score an asset file based on how many keywords appear in its path."""
    path_str = str(path).lower()
    score = sum(1 for kw in keywords if kw.lower() in path_str)
    return score


def _select_by_keywords(
    candidates: list[Path],
    keywords: list[str],
    n: int,
    kind: str,
) -> list[Asset]:
    scored = [(p, _score_asset(p, keywords)) for p in candidates]
    # Sort by score desc, then shuffle within equal scores for variety
    scored.sort(key=lambda x: (-x[1], random.random()))
    selected = scored[:n]
    return [Asset(path=p, kind=kind, score=s) for p, s in selected]


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────

def select_broll(
    keywords: list[str],
    assets_profile: str,
    num_scenes: int,
) -> list[Asset]:
    """
    Select b-roll assets (video preferred, images as fallback) from:
      /data/assets/broll/<assets_profile>/
      /data/assets/broll/default/          (fallback)

    Returns one asset per scene, up to num_scenes assets.
    """
    profile_dir = BROLL_ROOT / assets_profile
    default_dir = BROLL_ROOT / "default"

    video_files  = _scan_dir(profile_dir, VIDEO_EXTS)
    image_files  = _scan_dir(profile_dir, IMAGE_EXTS)

    # Fallback to default if profile is empty
    if not video_files and not image_files:
        logger.warning(
            "No assets in profile '%s', falling back to 'default'", assets_profile
        )
        video_files = _scan_dir(default_dir, VIDEO_EXTS)
        image_files = _scan_dir(default_dir, IMAGE_EXTS)

    if not video_files and not image_files:
        logger.error(
            "No b-roll assets found in %s or %s. "
            "Place .mp4/.jpg files in /data/assets/broll/%s/ or /data/assets/broll/default/",
            profile_dir, default_dir, assets_profile,
        )
        return []

    # Prefer videos; fill remaining with images
    need = num_scenes
    videos  = _select_by_keywords(video_files, keywords, need, "video")
    remaining = need - len(videos)
    images  = _select_by_keywords(image_files, keywords, remaining, "image") if remaining > 0 else []

    assets = videos + images
    logger.info(
        "Selected %d b-roll assets (%d video, %d image) for %d scenes",
        len(assets), len(videos), len(images), num_scenes,
    )
    return assets


def select_music(keywords: list[str] | None = None) -> Path | None:
    """
    Select a background music file from /data/assets/music/.
    Randomizes selection for variety; keyword scoring is optional.
    """
    candidates = _scan_dir(MUSIC_ROOT, MUSIC_EXTS)
    if not candidates:
        logger.warning(
            "No music files found in %s. "
            "Place .mp3/.wav files there for background music.", MUSIC_ROOT
        )
        return None

    if keywords:
        scored = [(p, _score_asset(p, keywords)) for p in candidates]
        scored.sort(key=lambda x: (-x[1], random.random()))
        chosen = scored[0][0]
    else:
        chosen = random.choice(candidates)

    logger.info("Selected music: %s", chosen)
    return chosen
