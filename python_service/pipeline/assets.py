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

# Subdirectory inside each broll profile where used files are stored
USED_DIR_NAME = "_used"


class Asset(NamedTuple):
    path: Path
    kind: str   # "video" | "image" | "music"
    score: int  # keyword match score


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _scan_dir(directory: Path, extensions: set[str]) -> list[Path]:
    """Scan directory for files with the given extensions, excluding _used/ subfolder."""
    if not directory.exists():
        return []
    used_dir = directory / USED_DIR_NAME
    return [
        p for p in directory.rglob("*")
        if p.is_file()
        and p.suffix.lower() in extensions
        and used_dir not in p.parents  # skip files inside _used/
    ]


def _rotate_used_back(directory: Path, extensions: set[str]) -> None:
    """
    Move all files from <directory>/_used/ back to <directory>.
    Called automatically when the active pool is exhausted so the
    pipeline never stalls.
    """
    used_dir = directory / USED_DIR_NAME
    if not used_dir.exists():
        return
    files = [p for p in used_dir.iterdir() if p.is_file() and p.suffix.lower() in extensions]
    if not files:
        return
    logger.info(
        "B-roll pool exhausted in '%s' — rotating %d file(s) back from _used/",
        directory.name, len(files),
    )
    for f in files:
        dest = directory / f.name
        # Avoid name collision with a counter suffix
        if dest.exists():
            dest = directory / f"{f.stem}_r{dest.stat().st_ino}{f.suffix}"
        f.rename(dest)


def mark_broll_used(assets: list["Asset"]) -> None:
    """
    Move each b-roll asset file (video/image) to a _used/ subfolder beside it.
    This prevents the same clip from being reused in future videos.
    Music files are intentionally NOT moved (rotation by random is sufficient).
    """
    for asset in assets:
        if asset.kind not in ("video", "image"):
            continue
        src: Path = asset.path
        if not src.exists():
            continue
        used_dir = src.parent / USED_DIR_NAME
        used_dir.mkdir(exist_ok=True)
        dest = used_dir / src.name
        # Handle unlikely name collision
        if dest.exists():
            dest = used_dir / f"{src.stem}_{src.stat().st_ino}{src.suffix}"
        try:
            src.rename(dest)
            logger.info("Marked used: %s → _used/%s", src.name, dest.name)
        except Exception as exc:
            logger.warning("Could not move '%s' to _used/: %s", src.name, exc)


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

    # Auto-rotate: if the active pool is empty but _used/ has files, bring them back
    if not video_files and not image_files:
        _rotate_used_back(profile_dir, VIDEO_EXTS | IMAGE_EXTS)
        video_files = _scan_dir(profile_dir, VIDEO_EXTS)
        image_files = _scan_dir(profile_dir, IMAGE_EXTS)

    # Fallback to default if profile is still empty after rotation
    if not video_files and not image_files:
        logger.warning(
            "No assets in profile '%s', falling back to 'default'", assets_profile
        )
        video_files = _scan_dir(default_dir, VIDEO_EXTS)
        image_files = _scan_dir(default_dir, IMAGE_EXTS)

        # Auto-rotate default pool too if needed
        if not video_files and not image_files:
            _rotate_used_back(default_dir, VIDEO_EXTS | IMAGE_EXTS)
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


def find_best_segment(music_path: Path, duration: float) -> float:
    """
    Analyse a music file and return the start offset (seconds) of the most
    energetic segment that fits within `duration` seconds.

    Snaps the result to the nearest beat for a natural entry point.
    Falls back to 0.0 if librosa is unavailable or analysis fails.
    """
    try:
        import numpy as np
        import librosa

        y, sr = librosa.load(str(music_path), mono=True)
        total_dur = len(y) / sr

        # Music shorter than 1.2× the needed duration → start from beginning
        if total_dur <= duration * 1.2:
            return 0.0

        hop_length   = 512
        rms          = librosa.feature.rms(y=y, hop_length=hop_length)[0]
        times        = librosa.frames_to_time(range(len(rms)), sr=sr, hop_length=hop_length)
        window_frames = int(duration * sr / hop_length)
        max_start_idx = len(rms) - window_frames - 1

        if max_start_idx <= 0:
            return 0.0

        energies  = [float(np.mean(rms[i : i + window_frames])) for i in range(max_start_idx)]
        best_idx  = int(np.argmax(energies))
        best_time = float(times[best_idx])

        # Snap to nearest beat
        _, beats = librosa.beat.beat_track(y=y, sr=sr)
        if len(beats) > 0:
            beat_times = librosa.frames_to_time(beats, sr=sr)
            beat_idx   = int(np.argmin(np.abs(beat_times - best_time)))
            best_time  = float(beat_times[beat_idx])

        logger.info("Best music segment: %.1fs / %.1fs total", best_time, total_dur)
        return best_time

    except Exception as exc:
        logger.warning("Music analysis failed, starting from 0s: %s", exc)
        return 0.0


def select_music(keywords: list[str] | None = None, music_profile: str | None = None) -> Path | None:
    """
    Select a background music file.
    Looks in /data/assets/music/<music_profile>/ first (if provided), then falls back
    to /data/assets/music/.
    Randomizes selection for variety; keyword scoring is optional.
    """
    candidates: list[Path] = []
    if music_profile:
        candidates = _scan_dir(MUSIC_ROOT / music_profile, MUSIC_EXTS)
        if candidates:
            logger.info("Using music from profile '%s' (%d files)", music_profile, len(candidates))
    if not candidates:
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
