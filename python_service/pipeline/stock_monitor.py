"""
Asset stock monitor.

Counts available b-roll and music files per profile and sends
Telegram notifications for low stock or daily reports.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from utils.telegram import send_message

logger = logging.getLogger(__name__)

DATA_ROOT  = Path(os.environ.get("DATA_ROOT", "/data"))
BROLL_ROOT = DATA_ROOT / "assets" / "broll"
MUSIC_ROOT = DATA_ROOT / "assets" / "music"

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MUSIC_EXTS = {".mp3", ".wav", ".ogg", ".aac", ".m4a"}

# Alert when available assets fall below this number
LOW_BROLL_THRESHOLD = 10
LOW_MUSIC_THRESHOLD = 3


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _count_files(directory: Path, extensions: set[str], skip_used: bool = True) -> int:
    if not directory.exists():
        return 0
    used_dir = directory / "_used"
    return sum(
        1 for p in directory.rglob("*")
        if p.is_file()
        and p.suffix.lower() in extensions
        and (not skip_used or used_dir not in p.parents)
    )


def _count_used(directory: Path, extensions: set[str]) -> int:
    used_dir = directory / "_used"
    if not used_dir.exists():
        return 0
    return sum(
        1 for p in used_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in extensions
    )


def _broll_profiles() -> list[str]:
    if not BROLL_ROOT.exists():
        return []
    return sorted(d.name for d in BROLL_ROOT.iterdir() if d.is_dir() and not d.name.startswith("_"))


def _music_profiles() -> list[str]:
    if not MUSIC_ROOT.exists():
        return []
    return sorted(d.name for d in MUSIC_ROOT.iterdir() if d.is_dir() and not d.name.startswith("_"))


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────

def build_stock_report() -> str:
    """Build a formatted HTML report of all available assets."""
    lines: list[str] = ["📦 <b>Asset Report</b>\n"]
    any_low = False

    # ── B-Roll ────────────────────────────────────────────────────────────────
    lines.append("🎬 <b>B-Roll disponível:</b>")
    for profile in _broll_profiles():
        d       = BROLL_ROOT / profile
        videos  = _count_files(d, VIDEO_EXTS)
        images  = _count_files(d, IMAGE_EXTS)
        used    = _count_used(d, VIDEO_EXTS | IMAGE_EXTS)
        total   = videos + images
        warn    = " ⚠️" if total < LOW_BROLL_THRESHOLD else ""
        if warn:
            any_low = True
        lines.append(
            f"  <b>{profile}:</b> {videos} vídeos, {images} imagens"
            f"  (usado: {used}){warn}"
        )

    if not _broll_profiles():
        lines.append("  (nenhuma pasta encontrada)")

    # ── Music ─────────────────────────────────────────────────────────────────
    lines.append("\n🎵 <b>Músicas disponíveis:</b>")

    root_music = _count_files(MUSIC_ROOT, MUSIC_EXTS)
    if root_music:
        lines.append(f"  (raiz): {root_music} faixas")

    for profile in _music_profiles():
        count = _count_files(MUSIC_ROOT / profile, MUSIC_EXTS)
        warn  = " ⚠️" if count < LOW_MUSIC_THRESHOLD else ""
        if warn:
            any_low = True
        lines.append(f"  <b>{profile}:</b> {count} faixas{warn}")

    if not _music_profiles() and not root_music:
        lines.append("  (nenhuma pasta encontrada)")

    if any_low:
        lines.append(
            f"\n⚠️ Atenção: uma ou mais pastas estão com poucos assets!"
            f"\nThresholds: b-roll &lt; {LOW_BROLL_THRESHOLD} | música &lt; {LOW_MUSIC_THRESHOLD}"
        )

    return "\n".join(lines)


def send_daily_report() -> None:
    """Send the full stock report via Telegram."""
    logger.info("Sending daily asset stock report")
    send_message(build_stock_report())


def check_and_alert_low_stock(profile: str) -> None:
    """
    Check b-roll stock for a profile after asset selection.
    Sends a Telegram alert if below LOW_BROLL_THRESHOLD.
    """
    d      = BROLL_ROOT / profile
    videos = _count_files(d, VIDEO_EXTS)
    images = _count_files(d, IMAGE_EXTS)
    total  = videos + images

    if total < LOW_BROLL_THRESHOLD:
        logger.warning("Low b-roll stock for profile '%s': %d assets remaining", profile, total)
        send_message(
            f"⚠️ <b>Estoque baixo — {profile}</b>\n"
            f"Apenas <b>{total}</b> assets restantes "
            f"(vídeos: {videos}, imagens: {images}).\n"
            f"Adicione novos arquivos em:\n"
            f"<code>/data/assets/broll/{profile}/</code>"
        )
