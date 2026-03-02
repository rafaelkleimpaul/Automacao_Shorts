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
  POST_PACK       – write post_pack.txt + mark job READY_FOR_MANUAL_POST
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
    get_job,
    log_step,
    mark_step_done,
    mark_step_failed,
    mark_step_started,
    save_asset,
    update_job_status,
)
from utils.logger import JobLogger
from utils.validators import validate_script, sanitize_filename
from pipeline.script_gen import generate_script
from pipeline.tts import generate_voice, get_audio_duration
from pipeline.asr import generate_srt
from pipeline.assets import select_broll, select_music
from pipeline.renderer import render_video

logger = logging.getLogger(__name__)

DATA_ROOT   = Path(os.environ.get("DATA_ROOT", "/data"))
VIDEOS_ROOT = DATA_ROOT / "videos"

STEPS = [
    "INIT",
    "SCRIPT_GEN",
    "VALIDATE_SCRIPT",
    "TTS",
    "ASR",
    "ASSET_SELECT",
    "RENDER",
    "POST_PACK",
]


# ─────────────────────────────────────────────────────────────────────────────
# Context – shared mutable dict passed between steps
# ─────────────────────────────────────────────────────────────────────────────

def _make_context(job: dict) -> dict[str, Any]:
    job_id    = str(job["id"])
    job_date  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    job_dir   = VIDEOS_ROOT / job_date / job_id
    return {
        "job_id":      job_id,
        "job":         job,
        "job_dir":     job_dir,
        "script":      None,
        "voice_path":  None,
        "srt_path":    None,
        "broll":       [],
        "music_path":  None,
        "final_path":  None,
        "duration":    float(job.get("duration_target_seconds", 30)),
    }


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
            indent=2,
            default=str,
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

    # Persist in DB
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
    voiceover  = ctx["script"]["voiceover"]
    generate_voice(voiceover, voice_path)
    ctx["voice_path"] = voice_path
    save_asset(
        ctx["job_id"], "voice", str(voice_path),
        file_size=voice_path.stat().st_size,
    )


def _step_asr(ctx: dict) -> None:
    srt_path  = ctx["job_dir"] / "captions.srt"
    voice_dur = get_audio_duration(ctx["voice_path"])

    if voice_dur > 0:
        ctx["duration"] = voice_dur   # use actual audio duration

    generate_srt(
        wav_path       = ctx["voice_path"],
        output_path    = srt_path,
        fallback_text  = ctx["script"]["voiceover"],
        audio_duration = ctx["duration"],
    )
    ctx["srt_path"] = srt_path
    save_asset(
        ctx["job_id"], "srt", str(srt_path),
        file_size=srt_path.stat().st_size,
    )


def _step_asset_select(ctx: dict) -> None:
    script  = ctx["script"]
    job     = ctx["job"]
    keywords = script.get("keywords", []) + [job.get("niche", "finance")]
    scenes   = script.get("scenes", [])

    broll = select_broll(
        keywords       = keywords,
        assets_profile = job.get("assets_profile") or job.get("niche", "finance"),
        num_scenes     = max(len(scenes), 3),
    )
    music = select_music(keywords)

    ctx["broll"]       = broll
    ctx["music_path"]  = music

    for asset in broll:
        save_asset(ctx["job_id"], asset.kind, str(asset.path))
    if music:
        save_asset(ctx["job_id"], "music", str(music))


def _step_render(ctx: dict) -> None:
    final_path = render_video(
        job_dir        = ctx["job_dir"],
        voice_path     = ctx["voice_path"],
        srt_path       = ctx["srt_path"],
        broll_assets   = ctx["broll"],
        music_path     = ctx["music_path"],
        total_duration = ctx["duration"],
    )
    ctx["final_path"] = final_path
    save_asset(
        ctx["job_id"], "final_video", str(final_path),
        file_size=final_path.stat().st_size,
        duration_s=ctx["duration"],
    )


def _step_post_pack(ctx: dict) -> None:
    script    = ctx["script"]
    job       = ctx["job"]
    job_dir   = ctx["job_dir"]

    hashtags  = " ".join(script.get("hashtags", []))
    caption   = (
        f"{script.get('title', '')}\n\n"
        f"{script.get('hook', '')}\n\n"
        f"{hashtags}\n\n"
        f"---\n"
        f"{script.get('disclaimer', 'This video is for educational purposes only.')}"
    )

    post_pack = (
        f"=== POST PACK ===\n"
        f"Status : READY_FOR_MANUAL_POST\n"
        f"Job ID : {ctx['job_id']}\n"
        f"Topic  : {job.get('main_subject', '')}\n"
        f"Niche  : {job.get('niche', 'finance')}\n"
        f"Lang   : {job.get('language', 'en_US')}\n\n"
        f"--- TITLE (for video / cover) ---\n"
        f"{script.get('title', '')}\n\n"
        f"--- CAPTION (copy-paste) ---\n"
        f"{caption}\n\n"
        f"--- HASHTAGS (standalone) ---\n"
        f"{hashtags}\n\n"
        f"--- KEYWORDS ---\n"
        f"{', '.join(script.get('keywords', []))}\n\n"
        f"--- DISCLAIMER ---\n"
        f"{script.get('disclaimer', '')}\n\n"
        f"--- FILE PATHS ---\n"
        f"Video  : {ctx.get('final_path', 'N/A')}\n"
        f"SRT    : {ctx.get('srt_path', 'N/A')}\n"
        f"Script : {job_dir / 'script.json'}\n"
    )

    pack_path = job_dir / "post_pack.txt"
    pack_path.write_text(post_pack, encoding="utf-8")

    save_asset(
        ctx["job_id"], "post_pack", str(pack_path),
        file_size=pack_path.stat().st_size,
    )

    update_job_status(
        ctx["job_id"],
        "READY_FOR_MANUAL_POST",
        output_dir        = str(job_dir),
        final_video_path  = str(ctx.get("final_path", "")),
        post_pack_path    = str(pack_path),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

STEP_FNS = {
    "INIT":            _step_init,
    "SCRIPT_GEN":      _step_script_gen,
    "VALIDATE_SCRIPT": _step_validate_script,
    "TTS":             _step_tts,
    "ASR":             _step_asr,
    "ASSET_SELECT":    _step_asset_select,
    "RENDER":          _step_render,
    "POST_PACK":       _step_post_pack,
}


def run_pipeline(job_id: str) -> dict[str, Any]:
    """
    Execute the full pipeline for job_id.
    Safe to call multiple times (idempotent per step).
    """
    job = get_job(job_id)
    if not job:
        raise ValueError(f"Job not found: {job_id}")

    if job["status"] not in ("PROCESSING", "PENDING"):
        return {
            "status":  job["status"],
            "message": f"Job already in status={job['status']}; skipping.",
        }

    completed = get_completed_steps(job_id)
    ctx = _make_context(job)

    # Re-load any previously generated files into context for resumed runs
    _reload_context(ctx, completed)

    for step in STEPS:
        jl = JobLogger(job_id, step)

        if step in completed:
            jl.info(f"Step {step} already completed – skipping")
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

            # Check if retries exhausted
            job = get_job(job_id)
            if job and job.get("retry_count", 0) >= job.get("max_retries", 3):
                from utils.db import get_conn
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT move_to_dead_letter(%s, %s)",
                            (job_id, str(exc)),
                        )
                    conn.commit()
            else:
                update_job_status(job_id, "FAILED", error_message=str(exc))
            raise

    final_video = str(ctx.get("final_path", ""))
    return {
        "status":      "READY_FOR_MANUAL_POST",
        "job_id":      job_id,
        "output_dir":  str(ctx["job_dir"]),
        "final_video": final_video,
        "post_pack":   str(ctx["job_dir"] / "post_pack.txt"),
    }


def _reload_context(ctx: dict, completed: set[str]) -> None:
    """Re-populate context for steps that were already done in a previous run."""
    job_dir: Path = ctx["job_dir"]

    if "SCRIPT_GEN" in completed:
        script_path = job_dir / "script.json"
        if script_path.exists():
            ctx["script"] = json.loads(script_path.read_text(encoding="utf-8"))

    if "TTS" in completed:
        vp = job_dir / "voice.wav"
        if vp.exists():
            ctx["voice_path"] = vp
            ctx["duration"] = get_audio_duration(vp) or ctx["duration"]

    if "ASR" in completed:
        sp = job_dir / "captions.srt"
        if sp.exists():
            ctx["srt_path"] = sp

    if "RENDER" in completed:
        fp = job_dir / "final.mp4"
        if fp.exists():
            ctx["final_path"] = fp
