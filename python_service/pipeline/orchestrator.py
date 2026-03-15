"""
Pipeline orchestrator.

Executes all steps for a video_job in the correct order.
Each step is idempotent: if it has already completed (status=DONE in job_steps)
it is skipped without any side effects.

Steps:
  INIT            – create directory, save job metadata
  SCRIPT_GEN      – call LLM to generate script
  VALIDATE_SCRIPT – validate duration, forbidden terms, structure
  TTS             – generate voice.wav
  ASR             – generate captions.srt (ASR or fallback word-timing)
  ASSET_SELECT    – choose local b-roll/music assets
  RENDER          – FFmpeg final.mp4
  POST_PACK       – write post_pack.txt, save metadata
  PUBLISH         – post to YouTube / Instagram / TikTok (if any enabled)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from utils.db import (
    get_completed_steps,
    get_conn,
    get_job,
    log_publish,
    mark_step_done,
    mark_step_failed,
    mark_step_started,
    save_asset,
    update_job_status,
)
from utils.logger import JobLogger
from utils.validators import validate_script
from pipeline.script_gen import generate_script
from pipeline.tts import generate_voice, get_audio_duration
from pipeline.asr import generate_srt
from pipeline.assets import select_broll, select_music
from pipeline.renderer import render_video

logger = logging.getLogger(__name__)

DATA_ROOT   = Path(os.environ.get("DATA_ROOT", "/data"))
VIDEOS_ROOT = DATA_ROOT / "videos"

# Statuses that allow the pipeline to (re-)run
RUNNABLE_STATUSES = (
    "PENDING",
    "PROCESSING",
    "READY_FOR_MANUAL_POST",   # retry publishing
    "PUBLISH_FAILED",          # retry publishing
)

STEPS = [
    "INIT",
    "SCRIPT_GEN",
    "VALIDATE_SCRIPT",
    "TTS",
    "ASR",
    "ASSET_SELECT",
    "RENDER",
    "POST_PACK",
    "PUBLISH",
]

# Silent (quote) pipeline — skips LLM script, TTS and ASR entirely
STEPS_SILENT = [
    "INIT",
    "ASSET_SELECT",
    "RENDER",
    "POST_PACK",
    "PUBLISH",
]


def _is_silent(job: dict) -> bool:
    """Return True when the job is a silent quote video (no voice/subtitles)."""
    return bool((job.get("extra_params") or {}).get("silent"))


# ─────────────────────────────────────────────────────────────────────────────
# Context
# ─────────────────────────────────────────────────────────────────────────────

def _make_context(job: dict) -> dict[str, Any]:
    job_id   = str(job["id"])
    job_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    job_dir  = VIDEOS_ROOT / job_date / job_id
    ctx: dict = {
        "job_id":          job_id,
        "job":             job,
        "job_dir":         job_dir,
        "script":          None,
        "voice_path":      None,
        "srt_path":        None,
        "broll":           [],
        "music_path":      None,
        "final_path":      None,
        "duration":        float(job.get("duration_target_seconds", 30)),
        "final_status":    "READY_FOR_MANUAL_POST",
        "publish_results": [],
    }

    # Pre-populate minimal script for silent quote jobs
    if _is_silent(job):
        phrase = job.get("main_subject", "")
        ctx["script"] = {
            "phrase":    phrase,
            "title":     phrase,
            "hook":      phrase,
            "voiceover": "",
            "keywords":  [w.lower() for w in phrase.split() if len(w) > 2],
            "scenes":    [],
            "hashtags":  [
                "#motivation", "#mindset", "#focus", "#discipline",
                "#success", "#neverquit", "#winning", "#hustle",
            ],
            "disclaimer": "",
        }

    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# Individual steps
# ─────────────────────────────────────────────────────────────────────────────

def _step_init(ctx: dict) -> None:
    job_dir: Path = ctx["job_dir"]
    job_dir.mkdir(parents=True, exist_ok=True)
    meta_path = job_dir / "job_meta.json"
    meta_path.write_text(
        json.dumps(
            {k: str(v) if not isinstance(v, (str, int, float, bool, list, dict, type(None))) else v
             for k, v in ctx["job"].items()},
            indent=2, default=str,
        ),
        encoding="utf-8",
    )


def _step_script_gen(ctx: dict) -> None:
    job: dict = ctx["job"]
    script = generate_script(
        topic           = job["main_subject"],
        niche           = job.get("niche", "finance"),
        language        = job.get("language", "en_US"),
        duration_target = int(job.get("duration_target_seconds", 30)),
        style           = job.get("style", "commentary"),
        extra_params    = job.get("extra_params") or {},
    )
    ctx["script"] = script
    script_path = ctx["job_dir"] / "script.json"
    script_path.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    update_job_status(ctx["job_id"], "PROCESSING", script_json=script)


def _step_validate_script(ctx: dict) -> None:
    errors = validate_script(
        ctx["script"],
        duration_target=int(ctx["job"].get("duration_target_seconds", 30)),
    )
    if errors:
        raise ValueError("Script validation failed:\n" + "\n".join(f"  - {e}" for e in errors))


def _step_tts(ctx: dict) -> None:
    voice_path = ctx["job_dir"] / "voice.wav"
    generate_voice(ctx["script"]["voiceover"], voice_path)
    ctx["voice_path"] = voice_path
    save_asset(ctx["job_id"], "voice", str(voice_path), file_size=voice_path.stat().st_size)


def _step_asr(ctx: dict) -> None:
    srt_path  = ctx["job_dir"] / "captions.srt"
    voice_dur = get_audio_duration(ctx["voice_path"])
    if voice_dur > 0:
        ctx["duration"] = voice_dur
    generate_srt(
        wav_path       = ctx["voice_path"],
        output_path    = srt_path,
        fallback_text  = ctx["script"]["voiceover"],
        audio_duration = ctx["duration"],
    )
    ctx["srt_path"] = srt_path
    save_asset(ctx["job_id"], "srt", str(srt_path), file_size=srt_path.stat().st_size)


def _step_asset_select(ctx: dict) -> None:
    script   = ctx["script"]
    job      = ctx["job"]
    keywords = script.get("keywords", []) + [job.get("niche", "finance")]
    scenes   = script.get("scenes", [])
    broll    = select_broll(
        keywords       = keywords,
        assets_profile = job.get("assets_profile") or job.get("niche", "finance"),
        num_scenes     = max(len(scenes), 3),
    )
    music_profile = (job.get("extra_params") or {}).get("music_profile")
    music = select_music(keywords, music_profile=music_profile)
    ctx["broll"]        = broll
    ctx["music_path"]   = music
    ctx["music_start"]  = 0.0
    if music and _is_silent(job):
        from pipeline.assets import find_best_segment
        ctx["music_start"] = find_best_segment(music, ctx["duration"])
    for asset in broll:
        save_asset(ctx["job_id"], asset.kind, str(asset.path))
    if music:
        save_asset(ctx["job_id"], "music", str(music))


def _step_render(ctx: dict) -> None:
    from pipeline.assets import mark_broll_used
    from pipeline.renderer import render_quote_video

    if _is_silent(ctx["job"]):
        final_path = render_quote_video(
            job_dir        = ctx["job_dir"],
            phrase         = ctx["script"]["phrase"],
            broll_assets   = ctx["broll"],
            music_path     = ctx["music_path"],
            total_duration = ctx["duration"],
            music_start    = ctx.get("music_start", 0.0),
        )
    else:
        final_path = render_video(
            job_dir        = ctx["job_dir"],
            voice_path     = ctx["voice_path"],
            srt_path       = ctx["srt_path"],
            broll_assets   = ctx["broll"],
            music_path     = ctx["music_path"],
            total_duration = ctx["duration"],
            music_start    = ctx.get("music_start", 0.0),
        )
    ctx["final_path"] = final_path
    save_asset(
        ctx["job_id"], "final_video", str(final_path),
        file_size=final_path.stat().st_size,
        duration_s=ctx["duration"],
    )

    # Move used b-roll to _used/ so the same clips aren't reused next time
    mark_broll_used(ctx["broll"])


def _step_post_pack(ctx: dict) -> None:
    from pipeline.hashtag_booster import boost_for_all_platforms

    script  = ctx["script"]
    job     = ctx["job"]
    job_dir = ctx["job_dir"]
    silent  = _is_silent(job)

    boosted = boost_for_all_platforms(
        script.get("hashtags", []),
        niche=job.get("niche", "finance"),
    )
    ctx["boosted_hashtags"] = boosted

    ig_hashtags      = boosted.get("instagram", boosted.get("default", script.get("hashtags", [])))
    hashtags_display = " ".join(ig_hashtags)

    if silent:
        phrase  = script.get("phrase", job.get("main_subject", ""))
        caption = f"{phrase}\n\n{hashtags_display}"
        post_pack = (
            f"=== POST PACK ===\n"
            f"Job ID : {ctx['job_id']}\n"
            f"Phrase : {phrase}\n"
            f"Niche  : {job.get('niche', 'mindset')}\n"
            f"Lang   : {job.get('language', 'en_US')}\n\n"
            f"--- CAPTION ---\n{caption}\n\n"
            f"--- HASHTAGS (Instagram/30) ---\n{' '.join(boosted.get('instagram', []))}\n\n"
            f"--- HASHTAGS (YouTube/15) ---\n{' '.join(boosted.get('youtube', []))}\n\n"
            f"--- HASHTAGS (TikTok/20) ---\n{' '.join(boosted.get('tiktok', []))}\n\n"
            f"--- FILES ---\n"
            f"Video  : {ctx.get('final_path', 'N/A')}\n"
        )
    else:
        caption = (
            f"{script.get('title', '')}\n\n"
            f"{script.get('hook', '')}\n\n"
            f"{hashtags_display}\n\n"
            f"---\n"
            f"{script.get('disclaimer', 'This video is for educational purposes only.')}"
        )
        post_pack = (
            f"=== POST PACK ===\n"
            f"Job ID : {ctx['job_id']}\n"
            f"Topic  : {job.get('main_subject', '')}\n"
            f"Niche  : {job.get('niche', 'finance')}\n"
            f"Lang   : {job.get('language', 'en_US')}\n\n"
            f"--- TITLE ---\n{script.get('title', '')}\n\n"
            f"--- CAPTION ---\n{caption}\n\n"
            f"--- HASHTAGS (Instagram/30) ---\n{' '.join(boosted.get('instagram', []))}\n\n"
            f"--- HASHTAGS (YouTube/15) ---\n{' '.join(boosted.get('youtube', []))}\n\n"
            f"--- HASHTAGS (TikTok/20) ---\n{' '.join(boosted.get('tiktok', []))}\n\n"
            f"--- KEYWORDS ---\n{', '.join(script.get('keywords', []))}\n\n"
            f"--- DISCLAIMER ---\n{script.get('disclaimer', '')}\n\n"
            f"--- FILES ---\n"
            f"Video  : {ctx.get('final_path', 'N/A')}\n"
            f"SRT    : {ctx.get('srt_path', 'N/A')}\n"
            f"Script : {job_dir / 'script.json'}\n"
        )

    pack_path = job_dir / "post_pack.txt"
    pack_path.write_text(post_pack, encoding="utf-8")
    save_asset(ctx["job_id"], "post_pack", str(pack_path), file_size=pack_path.stat().st_size)

    # Save output paths. Final status will be set by PUBLISH step (or by run_pipeline).
    update_job_status(
        ctx["job_id"],
        "PROCESSING",
        output_dir       = str(job_dir),
        final_video_path = str(ctx.get("final_path", "")),
        post_pack_path   = str(pack_path),
    )


def _step_publish(ctx: dict) -> None:
    from pipeline.publishers.manager import any_enabled, publish_all

    if not any_enabled():
        logger.info("No publishing platforms enabled — marking READY_FOR_MANUAL_POST")
        ctx["final_status"] = "READY_FOR_MANUAL_POST"
        return

    script   = ctx["script"]
    job      = ctx["job"]
    title    = script.get("title", job.get("main_subject", "Finance Tips"))
    # Use boosted hashtags if available (set by POST_PACK step), fall back to raw LLM hashtags
    boosted_hashtags = ctx.get("boosted_hashtags") or {}
    hook     = script.get("hook", "")
    voiceover_snippet = script.get("voiceover", "")[:500]
    disclaimer = script.get(
        "disclaimer",
        "This video is for educational purposes only and does not constitute financial advice.",
    )
    caption = f"{hook}\n\n{voiceover_snippet}\n\n{disclaimer}"

    update_job_status(ctx["job_id"], "PUBLISHING")

    results = publish_all(
        job              = job,
        final_video_path = ctx["final_path"],
        title            = title,
        caption          = caption,
        hashtags         = boosted_hashtags if boosted_hashtags else script.get("hashtags", []),
    )
    ctx["publish_results"] = results

    # Persist each result to DB
    _save_publish_results(ctx["job_id"], results)

    # Determine final status
    successes = [r for r in results if r.get("status") == "SUCCESS"]
    failures  = [r for r in results if r.get("status") == "FAILED"]

    # ── Per-platform log → publish_logs table ─────────────────────────────────
    for r in results:
        platform = r.get("platform", "unknown")
        if r.get("status") == "SUCCESS":
            log_publish(
                ctx["job_id"], platform, "SUCCESS",
                f"Published successfully → {r.get('platform_url')}",
                level="INFO",
                details={"url": r.get("platform_url"), "post_id": r.get("platform_post_id")},
            )
            logger.info("[PUBLISH] ✓ %s → %s", platform.upper(), r.get("platform_url"))
        else:
            log_publish(
                ctx["job_id"], platform, "FAILED",
                r.get("error_message", "Unknown error"),
                level="ERROR",
                details={"error": r.get("error_message"), "detail": r.get("error_detail", "")},
            )
            logger.error("[PUBLISH] ✗ %s — %s", platform.upper(), r.get("error_message"))

    # ── Summary banner ────────────────────────────────────────────────────────
    sep = "─" * 50
    logger.info(sep)
    logger.info("PUBLISH SUMMARY  job=%s", ctx["job_id"])
    logger.info("  Platforms attempted : %d", len(results))
    logger.info("  Succeeded           : %d  %s", len(successes),
                [r.get("platform") for r in successes])
    logger.info("  Failed              : %d  %s", len(failures),
                [r.get("platform") for r in failures])
    if failures:
        for r in failures:
            logger.error("  [%s] error: %s", r.get("platform", "?").upper(),
                         r.get("error_message"))
    logger.info(sep)

    if successes and not failures:
        ctx["final_status"] = "PUBLISHED"
    elif successes and failures:
        ctx["final_status"] = "PUBLISHED"   # partial success still counts
    else:
        ctx["final_status"] = "PUBLISH_FAILED"


def _save_publish_results(job_id: str, results: list[dict]) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            for r in results:
                cur.execute(
                    """
                    INSERT INTO publish_results
                        (job_id, platform, status, platform_post_id, platform_url,
                         error_message, published_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (job_id, platform) DO UPDATE SET
                        status           = EXCLUDED.status,
                        platform_post_id = EXCLUDED.platform_post_id,
                        platform_url     = EXCLUDED.platform_url,
                        error_message    = EXCLUDED.error_message,
                        published_at     = EXCLUDED.published_at
                    """,
                    (
                        job_id,
                        r.get("platform"),
                        r.get("status", "FAILED"),
                        r.get("platform_post_id"),
                        r.get("platform_url"),
                        r.get("error_message"),
                        datetime.now(timezone.utc) if r.get("status") == "SUCCESS" else None,
                    ),
                )
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Step registry
# ─────────────────────────────────────────────────────────────────────────────

STEP_FNS: dict[str, Any] = {
    "INIT":            _step_init,
    "SCRIPT_GEN":      _step_script_gen,
    "VALIDATE_SCRIPT": _step_validate_script,
    "TTS":             _step_tts,
    "ASR":             _step_asr,
    "ASSET_SELECT":    _step_asset_select,
    "RENDER":          _step_render,
    "POST_PACK":       _step_post_pack,
    "PUBLISH":         _step_publish,
}


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(job_id: str) -> dict[str, Any]:
    """
    Execute the full pipeline for job_id.
    Safe to call multiple times — each step is idempotent.
    """
    job = get_job(job_id)
    if not job:
        raise ValueError(f"Job not found: {job_id}")

    if job["status"] not in RUNNABLE_STATUSES:
        return {
            "status":  job["status"],
            "message": f"Job in terminal status={job['status']}; skipping.",
        }

    completed = get_completed_steps(job_id)
    ctx = _make_context(job)
    _reload_context(ctx, completed)

    steps = STEPS_SILENT if _is_silent(job) else STEPS
    for step in steps:
        jl = JobLogger(job_id, step)

        if step in completed:
            jl.info(f"Step {step} already completed — skipping")
            continue

        jl.info(f"Starting step {step}")
        mark_step_started(job_id, step)

        try:
            STEP_FNS[step](ctx)
            mark_step_done(job_id, step)
            jl.info(f"Step {step} done")
        except Exception as exc:
            mark_step_failed(job_id, step, str(exc))
            jl.error(f"Step {step} failed: {exc}", {"error": str(exc)})

            # Check if retries exhausted → dead letter
            refreshed_job = get_job(job_id)
            if refreshed_job and refreshed_job.get("retry_count", 0) >= refreshed_job.get("max_retries", 3):
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT move_to_dead_letter(%s, %s)", (job_id, str(exc)))
                    conn.commit()
            else:
                update_job_status(job_id, "FAILED", error_message=str(exc))
            raise

    # Set final status determined by the PUBLISH step
    final_status = ctx.get("final_status", "READY_FOR_MANUAL_POST")
    update_job_status(job_id, final_status)

    return {
        "status":          final_status,
        "job_id":          job_id,
        "output_dir":      str(ctx["job_dir"]),
        "final_video":     str(ctx.get("final_path", "")),
        "post_pack":       str(ctx["job_dir"] / "post_pack.txt"),
        "publish_results": ctx.get("publish_results", []),
    }


def _reload_context(ctx: dict, completed: set[str]) -> None:
    """Re-populate context from disk for steps already done in a prior run."""
    job_dir: Path = ctx["job_dir"]

    if "SCRIPT_GEN" in completed:
        p = job_dir / "script.json"
        if p.exists():
            ctx["script"] = json.loads(p.read_text(encoding="utf-8"))

    if "TTS" in completed:
        p = job_dir / "voice.wav"
        if p.exists():
            ctx["voice_path"] = p
            ctx["duration"]   = get_audio_duration(p) or ctx["duration"]

    if "ASR" in completed:
        p = job_dir / "captions.srt"
        if p.exists():
            ctx["srt_path"] = p

    if "RENDER" in completed:
        p = job_dir / "final.mp4"
        if p.exists():
            ctx["final_path"] = p
