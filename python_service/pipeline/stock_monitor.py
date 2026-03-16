"""
Asset stock monitor + server health checks.

- Asset stock: counts b-roll/music per profile, alerts on low stock
- Stuck jobs: alerts when a job stays in PROCESSING > STUCK_JOB_MINUTES
- Health check: disk usage + container reachability
- Daily report: full snapshot sent every morning
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timezone, timedelta
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

# Alert when a job stays in PROCESSING longer than this
STUCK_JOB_MINUTES   = 30

# Alert when data partition usage exceeds this percentage
DISK_ALERT_PERCENT  = 80


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


def check_stuck_jobs() -> None:
    """
    Alert if any job has been stuck in PROCESSING for more than STUCK_JOB_MINUTES.
    Call from the daily health check endpoint.
    """
    try:
        from utils.db import get_conn
        import psycopg2.extras
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=STUCK_JOB_MINUTES)
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, main_subject, niche, started_at
                    FROM   video_jobs
                    WHERE  status = 'PROCESSING'
                    AND    started_at < %s
                    """,
                    (cutoff,),
                )
                stuck = cur.fetchall()
        for job in stuck:
            elapsed = int((datetime.now(timezone.utc) - job["started_at"].replace(tzinfo=timezone.utc)).total_seconds() / 60)
            logger.warning("Stuck job detected: %s (%d min)", job["id"], elapsed)
            send_message(
                f"🔴 <b>Job travado — {job.get('niche', 'N/A')}</b>\n"
                f"📌 {job.get('main_subject', 'N/A')}\n"
                f"Job ID: <code>{job['id']}</code>\n"
                f"⏱ Em processamento há <b>{elapsed} min</b>"
            )
    except Exception as exc:
        logger.warning("check_stuck_jobs failed: %s", exc)


def build_health_report() -> str:
    """Build a server health report: disk usage + stuck jobs count."""
    lines: list[str] = [f"🟢 <b>Pipeline Health</b>\n"]

    # Disk usage
    try:
        total, used, free = shutil.disk_usage(str(DATA_ROOT))
        used_pct = used / total * 100
        used_gb  = used  / (1024 ** 3)
        total_gb = total / (1024 ** 3)
        disk_icon = "🔴" if used_pct >= DISK_ALERT_PERCENT else ("🟡" if used_pct >= 60 else "🟢")
        lines.append(f"{disk_icon} Disco: {used_gb:.1f} GB / {total_gb:.1f} GB ({used_pct:.0f}%)")
    except Exception:
        lines.append("❓ Disco: não foi possível verificar")

    # Stuck jobs
    try:
        from utils.db import get_conn
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=STUCK_JOB_MINUTES)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM video_jobs WHERE status='PROCESSING' AND started_at < %s",
                    (cutoff,),
                )
                stuck_count = cur.fetchone()[0]
        stuck_icon = "🔴" if stuck_count > 0 else "✅"
        lines.append(f"{stuck_icon} Jobs travados: {stuck_count}")
    except Exception:
        lines.append("❓ Jobs travados: não foi possível verificar")

    # Videos generated today
    try:
        from utils.db import get_conn
        today = datetime.now(timezone.utc).date()
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM video_jobs WHERE status IN ('READY_FOR_MANUAL_POST','PUBLISHED') AND DATE(completed_at) = %s",
                    (today,),
                )
                today_count = cur.fetchone()[0]
        lines.append(f"🎬 Vídeos gerados hoje: {today_count}")
    except Exception:
        pass

    return "\n".join(lines)


def send_health_report() -> None:
    """Send server health report + check for stuck jobs via Telegram."""
    logger.info("Sending health report")
    check_stuck_jobs()
    send_message(build_health_report())


def send_daily_full_report() -> None:
    """Send both stock report and health report in one go."""
    send_daily_report()
    send_message(build_health_report())


# ─────────────────────────────────────────────────────────────────────────────
# Cleanup
# ─────────────────────────────────────────────────────────────────────────────

# Files worth keeping even after cleanup (small, useful for audits)
_KEEP_EXTENSIONS = {".json", ".txt", ".srt", ".csv"}

# Statuses considered "done" — safe to clean up files
_TERMINAL_STATUSES = ("PUBLISHED", "READY_FOR_MANUAL_POST", "FAILED", "DEAD_LETTER")


def run_cleanup(older_than_days: int = 30, dry_run: bool = False) -> dict:
    """
    Delete heavy media files (mp4, wav, aac, ass) from job output directories
    for jobs older than `older_than_days` days in a terminal status.

    Metadata files (json, txt, srt) are preserved for audit purposes.
    DB records are never touched.

    Returns a summary dict with counts and bytes freed.
    """
    import psycopg2.extras
    from utils.db import get_conn

    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, main_subject, niche, status, output_dir, completed_at
                    FROM   video_jobs
                    WHERE  status = ANY(%s)
                    AND    completed_at < %s
                    AND    output_dir IS NOT NULL
                    AND    output_dir != ''
                    ORDER  BY completed_at ASC
                    """,
                    (list(_TERMINAL_STATUSES), cutoff),
                )
                jobs = [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("Cleanup DB query failed: %s", exc)
        return {"error": str(exc)}

    total_freed  = 0
    cleaned_jobs = 0
    skipped_jobs = 0
    errors       = 0

    for job in jobs:
        out_dir = Path(job["output_dir"])
        if not out_dir.exists():
            skipped_jobs += 1
            continue

        job_freed = 0
        for f in out_dir.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() in _KEEP_EXTENSIONS:
                continue  # keep metadata
            try:
                size = f.stat().st_size
                if not dry_run:
                    f.unlink()
                job_freed    += size
                total_freed  += size
                logger.debug("Cleanup%s: %s (%.1f MB)", " [dry]" if dry_run else "", f.name, size / 1024 / 1024)
            except Exception as exc:
                logger.warning("Could not delete %s: %s", f, exc)
                errors += 1

        if job_freed > 0:
            cleaned_jobs += 1
            logger.info(
                "Cleaned job %s (%s — %s): freed %.1f MB%s",
                job["id"], job["niche"], job["main_subject"][:40],
                job_freed / 1024 / 1024,
                " [dry run]" if dry_run else "",
            )

    freed_mb = total_freed / (1024 * 1024)
    freed_gb = total_freed / (1024 ** 3)

    return {
        "dry_run":     dry_run,
        "jobs_found":  len(jobs),
        "cleaned":     cleaned_jobs,
        "skipped":     skipped_jobs,
        "errors":      errors,
        "freed_bytes": total_freed,
        "freed_mb":    round(freed_mb, 1),
    }


def cleanup_and_notify(older_than_days: int = 30, dry_run: bool = False) -> dict:
    """Run cleanup and send Telegram summary."""
    logger.info("Starting cleanup (older_than_days=%d, dry_run=%s)", older_than_days, dry_run)
    result = run_cleanup(older_than_days=older_than_days, dry_run=dry_run)

    if "error" in result:
        send_message(f"❌ <b>Cleanup falhou</b>\n<code>{result['error']}</code>")
        return result

    freed_mb = result["freed_mb"]
    freed_gb = freed_mb / 1024

    size_str = f"{freed_gb:.2f} GB" if freed_gb >= 1 else f"{freed_mb:.1f} MB"
    dry_tag  = " <i>(dry run)</i>" if dry_run else ""

    if result["cleaned"] == 0:
        send_message(
            f"🧹 <b>Cleanup concluído{dry_tag}</b>\n"
            f"Nenhum arquivo para remover (critério: &gt; {older_than_days} dias)."
        )
    else:
        send_message(
            f"🧹 <b>Cleanup concluído{dry_tag}</b>\n\n"
            f"📁 Jobs processados : {result['jobs_found']}\n"
            f"✅ Jobs limpos       : {result['cleaned']}\n"
            f"💾 Espaço liberado  : <b>{size_str}</b>\n"
            f"⚠️ Erros            : {result['errors']}\n\n"
            f"Arquivos preservados: .json, .txt, .srt"
        )

    return result
