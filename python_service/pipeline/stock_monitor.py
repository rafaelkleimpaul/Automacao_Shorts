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


# ─────────────────────────────────────────────────────────────────────────────
# B-roll _used/ purge
# ─────────────────────────────────────────────────────────────────────────────

def purge_used_broll(dry_run: bool = False) -> dict:
    """
    Scan every _used/ subfolder inside BROLL_ROOT and:
      - DELETE  the file if its original path exists in the assets DB table
               (confirmed used in at least one finished job)
      - RESTORE the file to its parent folder if NOT found in the DB
               (moved by mistake or the job was never completed)

    The DB stores the ORIGINAL path (before the file was moved to _used/),
    so we reconstruct it as:  <profile_dir>/<filename>

    Args:
        dry_run: If True, only logs what would happen — no files are touched.

    Returns:
        Summary dict with counts of deleted / restored / errors.
    """
    from utils.db import get_conn

    deleted   = 0
    restored  = 0
    errors    = 0
    deleted_bytes = 0

    if not BROLL_ROOT.exists():
        return {"deleted": 0, "restored": 0, "errors": 0, "freed_bytes": 0}

    for profile_dir in BROLL_ROOT.iterdir():
        if not profile_dir.is_dir() or profile_dir.name.startswith("_"):
            continue

        used_dir = profile_dir / "_used"
        if not used_dir.exists():
            continue

        files = [
            p for p in used_dir.iterdir()
            if p.is_file() and p.suffix.lower() in (VIDEO_EXTS | IMAGE_EXTS)
        ]

        for f in files:
            # Reconstruct the original path (before move to _used/)
            original_path = str(profile_dir / f.name)

            try:
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT COUNT(*) FROM assets WHERE file_path = %s",
                            (original_path,),
                        )
                        count = cur.fetchone()[0]
            except Exception as exc:
                logger.warning("DB check failed for '%s': %s", f.name, exc)
                errors += 1
                continue

            if count > 0:
                # Confirmed used in DB → delete
                size = f.stat().st_size
                logger.info(
                    "Purge%s: DELETE '%s' (used %d time(s) in DB)",
                    " [dry]" if dry_run else "", f.name, count,
                )
                if not dry_run:
                    try:
                        f.unlink()
                        deleted += 1
                        deleted_bytes += size
                    except Exception as exc:
                        logger.warning("Could not delete '%s': %s", f, exc)
                        errors += 1
                else:
                    deleted += 1
                    deleted_bytes += size
            else:
                # NOT in DB → restore to active pool
                dest = profile_dir / f.name
                if dest.exists():
                    dest = profile_dir / f"{f.stem}_restored{f.suffix}"
                logger.info(
                    "Purge%s: RESTORE '%s' → '%s' (not found in DB)",
                    " [dry]" if dry_run else "", f.name, dest.name,
                )
                if not dry_run:
                    try:
                        f.rename(dest)
                        restored += 1
                    except Exception as exc:
                        logger.warning("Could not restore '%s': %s", f, exc)
                        errors += 1
                else:
                    restored += 1

    return {
        "dry_run":     dry_run,
        "deleted":     deleted,
        "restored":    restored,
        "errors":      errors,
        "freed_bytes": deleted_bytes,
        "freed_mb":    round(deleted_bytes / (1024 * 1024), 1),
    }


def request_purge_approval(base_url: str) -> dict:
    """
    Run a detailed dry-run, build a full report and send it to Telegram with
    Approve / Cancel inline keyboard buttons.

    The approve button calls  POST <base_url>/purge_used_broll/confirm?token=<token>
    The cancel  button calls  POST <base_url>/purge_used_broll/cancel?token=<token>

    Args:
        base_url: Public base URL of this FastAPI service (e.g. http://1.2.3.4:8000).
                  Used to generate the inline-keyboard button URLs.

    Returns:
        The dry-run summary dict plus the generated approval token.
    """
    import uuid
    from datetime import datetime, timezone, timedelta
    from utils.telegram import send_message_with_buttons
    from utils.db import get_conn

    # ── Collect detailed file info ────────────────────────────────────────────
    to_delete:  list[dict] = []   # {profile, name, size_mb, used_count}
    to_restore: list[dict] = []   # {profile, name}

    if BROLL_ROOT.exists():
        for profile_dir in sorted(BROLL_ROOT.iterdir()):
            if not profile_dir.is_dir() or profile_dir.name.startswith("_"):
                continue
            used_dir = profile_dir / "_used"
            if not used_dir.exists():
                continue

            for f in sorted(used_dir.iterdir()):
                if not f.is_file() or f.suffix.lower() not in (VIDEO_EXTS | IMAGE_EXTS):
                    continue
                original_path = str(profile_dir / f.name)
                try:
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT COUNT(*) FROM assets WHERE file_path = %s",
                                (original_path,),
                            )
                            count = cur.fetchone()[0]
                except Exception as exc:
                    logger.warning("DB check failed for '%s': %s", f.name, exc)
                    continue

                size_mb = round(f.stat().st_size / (1024 * 1024), 1)
                if count > 0:
                    to_delete.append({"profile": profile_dir.name, "name": f.name, "size_mb": size_mb, "used_count": count})
                else:
                    to_restore.append({"profile": profile_dir.name, "name": f.name, "size_mb": size_mb})

    # ── Generate approval token ───────────────────────────────────────────────
    token     = uuid.uuid4().hex
    expires   = datetime.now(timezone.utc) + timedelta(hours=2)
    approvals = _load_approvals()
    approvals[token] = {
        "expires_at": expires,
        "to_delete":  to_delete,
        "to_restore": to_restore,
    }
    _save_approvals(approvals)

    # ── Build Telegram message ────────────────────────────────────────────────
    total_delete_mb = sum(f["size_mb"] for f in to_delete)
    size_str = f"{total_delete_mb / 1024:.2f} GB" if total_delete_mb >= 1024 else f"{total_delete_mb:.1f} MB"
    now_str  = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    lines = [
        "🗑️ <b>Solicitação de Expurgo de B-Roll</b>",
        f"📅 {now_str}  |  ⏱ Expira em 2h\n",
    ]

    _MAX_PREVIEW = 8  # max files listed per section to stay within Telegram's 4096 char limit

    if to_delete:
        lines.append(f"✅ <b>Para DELETAR ({len(to_delete)} arquivo(s) — {size_str}):</b>")
        for item in to_delete[:_MAX_PREVIEW]:
            lines.append(f"  [{item['profile']}] {item['name']} ({item['size_mb']} MB, usado {item['used_count']}×)")
        if len(to_delete) > _MAX_PREVIEW:
            lines.append(f"  <i>... e mais {len(to_delete) - _MAX_PREVIEW} arquivo(s)</i>")
    else:
        lines.append("✅ Nenhum arquivo confirmado para deleção.")

    lines.append("")

    if to_restore:
        lines.append(f"♻️ <b>Para RESTAURAR ao pool ativo ({len(to_restore)} arquivo(s)):</b>")
        for item in to_restore[:_MAX_PREVIEW]:
            lines.append(f"  [{item['profile']}] {item['name']} ({item['size_mb']} MB)")
        if len(to_restore) > _MAX_PREVIEW:
            lines.append(f"  <i>... e mais {len(to_restore) - _MAX_PREVIEW} arquivo(s)</i>")
    else:
        lines.append("♻️ Nenhum arquivo para restaurar.")

    lines.append("\n<i>Escolha uma ação abaixo:</i>")

    base_url = base_url.rstrip("/")
    buttons = [[
        {"text": "✅ Aprovar e executar", "url": f"{base_url}/purge_used_broll/confirm?token={token}"},
        {"text": "❌ Cancelar",           "url": f"{base_url}/purge_used_broll/cancel?token={token}"},
    ]]

    send_message_with_buttons("\n".join(lines), buttons)
    logger.info(
        "Purge approval request sent (token=%s, delete=%d, restore=%d, expires=%s)",
        token, len(to_delete), len(to_restore), expires.isoformat(),
    )

    return {
        "token":           token,
        "expires_at":      expires.isoformat(),
        "files_to_delete": len(to_delete),
        "files_to_restore": len(to_restore),
        "total_mb":        round(total_delete_mb, 1),
    }


# Pending approvals persisted to disk so tokens survive container restarts
_APPROVALS_FILE = DATA_ROOT / "purge_approvals.json"


def _load_approvals() -> dict:
    try:
        if _APPROVALS_FILE.exists():
            import json as _json
            data = _json.loads(_APPROVALS_FILE.read_text())
            # Convert expires_at strings back to datetime
            for v in data.values():
                if isinstance(v.get("expires_at"), str):
                    v["expires_at"] = datetime.fromisoformat(v["expires_at"])
            return data
    except Exception as exc:
        logger.warning("Could not load approvals file: %s", exc)
    return {}


def _save_approvals(approvals: dict) -> None:
    try:
        import json as _json
        serialisable = {}
        for k, v in approvals.items():
            entry = dict(v)
            if isinstance(entry.get("expires_at"), datetime):
                entry["expires_at"] = entry["expires_at"].isoformat()
            serialisable[k] = entry
        _APPROVALS_FILE.write_text(_json.dumps(serialisable))
    except Exception as exc:
        logger.warning("Could not save approvals file: %s", exc)


def confirm_purge(token: str) -> dict:
    """
    Execute the purge that was previewed and approved via Telegram button.
    Validates that the token exists and has not expired.
    """
    from datetime import datetime, timezone
    from utils.db import get_conn

    approvals = _load_approvals()
    pending = approvals.pop(token, None)
    if not pending:
        return {"error": "Token inválido ou já utilizado."}
    if datetime.now(timezone.utc) > pending["expires_at"]:
        _save_approvals(approvals)
        return {"error": "Token expirado. Gere um novo pedido."}
    _save_approvals(approvals)

    deleted       = 0
    restored      = 0
    errors        = 0
    deleted_bytes = 0

    for item in pending["to_delete"]:
        f = BROLL_ROOT / item["profile"] / "_used" / item["name"]
        try:
            size = f.stat().st_size
            f.unlink()
            deleted       += 1
            deleted_bytes += size
            logger.info("Purge confirmed: deleted '%s'", f)
        except Exception as exc:
            logger.warning("Could not delete '%s': %s", f, exc)
            errors += 1

    for item in pending["to_restore"]:
        src  = BROLL_ROOT / item["profile"] / "_used" / item["name"]
        dest = BROLL_ROOT / item["profile"] / item["name"]
        if dest.exists():
            dest = BROLL_ROOT / item["profile"] / f"{src.stem}_restored{src.suffix}"
        try:
            src.rename(dest)
            restored += 1
            logger.info("Purge confirmed: restored '%s' → '%s'", src.name, dest.name)
        except Exception as exc:
            logger.warning("Could not restore '%s': %s", src, exc)
            errors += 1

    freed_mb = round(deleted_bytes / (1024 * 1024), 1)
    size_str = f"{freed_mb / 1024:.2f} GB" if freed_mb >= 1024 else f"{freed_mb:.1f} MB"

    lines = ["✅ <b>Expurgo executado com sucesso!</b>\n"]
    if deleted:
        lines.append(f"🗑️ Deletados : <b>{deleted}</b> ({size_str})")
    if restored:
        lines.append(f"♻️ Restaurados: <b>{restored}</b>")
    if errors:
        lines.append(f"⚠️ Erros     : {errors}")

    send_message("\n".join(lines))

    return {"deleted": deleted, "restored": restored, "errors": errors, "freed_mb": freed_mb}


def cancel_purge(token: str) -> dict:
    """Cancel a pending purge approval."""
    approvals = _load_approvals()
    removed = approvals.pop(token, None)
    if removed:
        _save_approvals(approvals)
        send_message("❌ <b>Expurgo cancelado.</b>\nNenhum arquivo foi alterado.")
        return {"status": "cancelled"}
    return {"status": "token_not_found"}


def purge_used_broll_and_notify(dry_run: bool = False) -> dict:
    """Run purge_used_broll and send Telegram summary."""
    logger.info("Starting b-roll _used/ purge (dry_run=%s)", dry_run)
    result = purge_used_broll(dry_run=dry_run)

    dry_tag  = " <i>(dry run)</i>" if dry_run else ""
    freed_mb = result["freed_mb"]
    size_str = f"{freed_mb / 1024:.2f} GB" if freed_mb >= 1024 else f"{freed_mb:.1f} MB"

    if result["deleted"] == 0 and result["restored"] == 0:
        send_message(
            f"🗑️ <b>Expurgo b-roll{dry_tag}</b>\n"
            f"Nenhum arquivo na pasta <code>_used/</code> para processar."
        )
    else:
        lines = [f"🗑️ <b>Expurgo b-roll concluído{dry_tag}</b>\n"]
        if result["deleted"]:
            lines.append(f"✅ Deletados (confirmados no DB) : <b>{result['deleted']}</b> ({size_str})")
        if result["restored"]:
            lines.append(f"♻️ Restaurados (não estavam no DB): <b>{result['restored']}</b>")
        if result["errors"]:
            lines.append(f"⚠️ Erros : {result['errors']}")
        send_message("\n".join(lines))

    return result


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
