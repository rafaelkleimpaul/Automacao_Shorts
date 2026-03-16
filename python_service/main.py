"""
Shorts Video Worker – FastAPI entry point.

Endpoints:
  GET  /health                    – liveness + DB probe
  POST /run/{job_id}              – execute pipeline (called by n8n)
  POST /jobs                      – create a single job (and optionally run it)
  POST /generate_jobs             – auto-generate jobs from trending news via LLM
  GET  /job/{job_id}              – job status + outputs + publish results
  GET  /jobs                      – list recent jobs
  GET  /serve/{job_id}/{filename} – serve a generated file (used by Instagram)
  GET  /publish_results           – list recent publish results across all platforms
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from utils.db import create_job, get_conn, get_job, init_pool, update_job_status
from utils.logger import configure_logging
from pipeline.orchestrator import run_pipeline, RUNNABLE_STATUSES

configure_logging()
logger = logging.getLogger(__name__)

DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/data"))


# ─────────────────────────────────────────────────────────────────────────────
# Startup / shutdown
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting video worker…")
    init_pool(minconn=1, maxconn=5)
    # Ensure credentials dir exists for OAuth token files
    (DATA_ROOT / "credentials").mkdir(parents=True, exist_ok=True)
    yield
    logger.info("Shutting down video worker.")


app = FastAPI(
    title="Shorts Video Worker",
    version="2.0.0",
    description=(
        "Local video generation + auto-publishing pipeline "
        "for short-form finance content (YouTube, Instagram, TikTok)."
    ),
    lifespan=lifespan,
)


# ─────────────────────────────────────────────────────────────────────────────
# Background task wrapper
# ─────────────────────────────────────────────────────────────────────────────

def _run_pipeline_task(job_id: str) -> None:
    try:
        result = run_pipeline(job_id)
        logger.info("Pipeline completed for job %s → status=%s", job_id, result.get("status"))
    except Exception as exc:
        logger.error("Pipeline error for job %s: %s", job_id, exc, exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"])
def health() -> dict[str, Any]:
    """Liveness + basic DB probe + publishing platform status."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        db_ok = True
    except Exception as exc:
        logger.warning("DB health check failed: %s", exc)
        db_ok = False

    return {
        "status":    "ok" if db_ok else "degraded",
        "db":        "ok" if db_ok else "unreachable",
        "worker":    "ok",
        "data_root": str(DATA_ROOT),
        "publishing": {
            "youtube":   os.getenv("YOUTUBE_ENABLED", "false"),
            "instagram": os.getenv("INSTAGRAM_ENABLED", "false"),
            "tiktok":    os.getenv("TIKTOK_ENABLED", "false"),
        },
    }


class JobCreateRequest(BaseModel):
    main_subject:            str  = Field(..., description="Video topic")
    niche:                   str  = Field("finance", description="Content niche")
    language:                str  = Field("en_US")
    style:                   str  = Field("commentary", description="commentary | educational | listicle")
    duration_target_seconds: int  = Field(30, ge=15, le=60)
    assets_profile:          str  = Field("finance")
    priority:                int  = Field(7, ge=1, le=10)
    extra_params:            dict = Field(default_factory=dict)
    auto_run:                bool = Field(True, description="Dispatch pipeline immediately after creation")


class GenerateJobsRequest(BaseModel):
    niche:    str = Field("finance", description="Niche key — see GET /niches for available options")
    count:    int = Field(3, ge=1, le=10, description="Number of jobs to generate")
    auto_run: bool = Field(True, description="Dispatch pipeline immediately after creation")


@app.post("/jobs", tags=["pipeline"])
def create_job_endpoint(body: JobCreateRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """
    Create a single job and optionally start the pipeline immediately.
    Useful for manual scheduling or external integrations.
    """
    job_id = create_job(
        main_subject=body.main_subject,
        niche=body.niche,
        language=body.language,
        style=body.style,
        duration_target_seconds=body.duration_target_seconds,
        assets_profile=body.assets_profile,
        priority=body.priority,
        extra_params=body.extra_params,
    )
    if body.auto_run:
        update_job_status(job_id, "PROCESSING")
        background_tasks.add_task(_run_pipeline_task, job_id)

    return {
        "status":   "accepted" if body.auto_run else "created",
        "job_id":   job_id,
        "message":  "Pipeline started." if body.auto_run else "Job created. Call POST /run/{job_id} to start.",
    }


@app.post("/generate_jobs", tags=["pipeline"])
def generate_jobs_endpoint(body: GenerateJobsRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """
    Fetch trending news for the given niche, use the local LLM to generate
    video topic ideas, create a job for each, and optionally dispatch them.

    The n8n workflow can call this endpoint on a schedule (e.g. daily at 8am)
    to run the pipeline fully autonomously.
    """
    from pipeline.topic_generator import generate_topics, list_available_niches

    available = list_available_niches()
    if body.niche not in available:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown niche '{body.niche}'. Available: {available}",
        )

    try:
        topics = generate_topics(niche=body.niche, count=body.count)
    except Exception as exc:
        logger.error("Topic generation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Topic generation failed: {exc}")

    job_ids: list[str] = []
    for topic_params in topics:
        job_id = create_job(**topic_params)
        if body.auto_run:
            update_job_status(job_id, "PROCESSING")
            background_tasks.add_task(_run_pipeline_task, job_id)
        job_ids.append(job_id)
        logger.info("Job created%s: %s — %s",
                    " and dispatched" if body.auto_run else "",
                    job_id, topic_params["main_subject"])

    return {
        "status":   "accepted" if body.auto_run else "created",
        "niche":    body.niche,
        "count":    len(job_ids),
        "job_ids":  job_ids,
        "message":  f"{len(job_ids)} jobs {'started' if body.auto_run else 'created — call POST /run/{{id}} to start each'}.",
    }


@app.get("/niches", tags=["pipeline"])
def list_niches() -> dict[str, Any]:
    """List available niche configurations for topic generation."""
    from pipeline.topic_generator import list_available_niches
    return {"niches": list_available_niches()}


@app.post("/run/{job_id}", tags=["pipeline"])
def run_job(job_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """
    Trigger pipeline execution for a job.
    Returns immediately with status=accepted; pipeline runs in background.
    """
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    current_status = job["status"]

    # Allow re-running publishing if it previously failed
    if current_status in ("PUBLISHED", "POSTED"):
        return {
            "status":  "already_done",
            "job_id":  job_id,
            "message": f"Job already completed with status={current_status}",
        }

    if current_status not in RUNNABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Job status={current_status} is not runnable. "
                   f"Reset to PENDING via SQL to retry.",
        )

    if current_status == "PENDING":
        update_job_status(job_id, "PROCESSING")

    background_tasks.add_task(_run_pipeline_task, job_id)

    return {
        "status":  "accepted",
        "job_id":  job_id,
        "message": "Pipeline started. Poll /job/{job_id} for status.",
    }


@app.get("/job/{job_id}", tags=["pipeline"])
def get_job_status(job_id: str) -> dict[str, Any]:
    """Return current status, output paths, and publish results for a job."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Fetch publish results
    pub_results: list[dict] = []
    try:
        import psycopg2.extras
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT platform, status, platform_post_id, platform_url, "
                    "error_message, published_at "
                    "FROM publish_results WHERE job_id = %s ORDER BY created_at",
                    (job_id,),
                )
                pub_results = [dict(r) for r in cur.fetchall()]
    except Exception:
        pass   # table might not exist yet (before migration)

    return {
        "id":               str(job["id"]),
        "status":           job["status"],
        "main_subject":     job.get("main_subject"),
        "niche":            job.get("niche"),
        "language":         job.get("language"),
        "output_dir":       job.get("output_dir"),
        "final_video_path": job.get("final_video_path"),
        "post_pack_path":   job.get("post_pack_path"),
        "error_message":    job.get("error_message"),
        "started_at":       str(job["started_at"])   if job.get("started_at")   else None,
        "completed_at":     str(job["completed_at"]) if job.get("completed_at") else None,
        "retry_count":      job.get("retry_count", 0),
        "publish_results":  pub_results,
    }


@app.get("/jobs", tags=["pipeline"])
def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    """List the most recent jobs with their publish status."""
    import psycopg2.extras
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    j.id, j.status, j.main_subject, j.niche, j.language,
                    j.priority, j.created_at, j.started_at, j.completed_at,
                    j.retry_count,
                    jsonb_agg(
                        jsonb_build_object(
                            'platform',     r.platform,
                            'status',       r.status,
                            'platform_url', r.platform_url
                        )
                    ) FILTER (WHERE r.platform IS NOT NULL) AS publish_results
                FROM   video_jobs j
                LEFT   JOIN publish_results r ON r.job_id = j.id
                GROUP  BY j.id
                ORDER  BY j.created_at DESC
                LIMIT  %s
                """,
                (min(limit, 200),),
            )
            return [dict(row) for row in cur.fetchall()]


@app.get("/serve/{job_id}/{filename}", tags=["ops"])
def serve_file(job_id: str, filename: str) -> FileResponse:
    """
    Serve a generated file for external access (required for Instagram uploads).
    The PUBLIC_BASE_URL env var should point to the public URL of this worker.

    Example: GET /serve/abc-123/final.mp4
    """
    # Security: only allow safe characters in filename
    import re
    if not re.match(r"^[\w\-. ]+$", filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    output_dir = job.get("output_dir")
    if not output_dir:
        raise HTTPException(status_code=404, detail="Job output directory not set")

    file_path = Path(output_dir) / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    # Only serve files within the data directory (path traversal guard)
    try:
        file_path.resolve().relative_to(DATA_ROOT.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    return FileResponse(str(file_path))


@app.get("/publish_results", tags=["publishing"])
def list_publish_results(limit: int = 50) -> list[dict[str, Any]]:
    """List recent publishing results across all platforms and jobs."""
    import psycopg2.extras
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    r.id, r.job_id, r.platform, r.status,
                    r.platform_post_id, r.platform_url,
                    r.error_message, r.published_at, r.created_at,
                    j.main_subject
                FROM   publish_results r
                JOIN   video_jobs j ON j.id = r.job_id
                ORDER  BY r.created_at DESC
                LIMIT  %s
                """,
                (min(limit, 200),),
            )
            return [dict(row) for row in cur.fetchall()]


@app.get("/publish_logs", tags=["publishing"])
def list_publish_logs(
    platform: str | None = None,
    job_id:   str | None = None,
    level:    str | None = None,
    limit:    int = 100,
) -> list[dict[str, Any]]:
    """
    List publish_logs entries with optional filters.

    Query params:
      platform  – filter by platform (youtube | instagram | tiktok)
      job_id    – filter by job UUID
      level     – filter by level (INFO | ERROR | WARNING)
      limit     – max rows (default 100)
    """
    import psycopg2.extras
    conditions = []
    params: list[Any] = []

    if platform:
        conditions.append("l.platform = %s")
        params.append(platform.lower())
    if job_id:
        conditions.append("l.job_id = %s")
        params.append(job_id)
    if level:
        conditions.append("l.level = %s")
        params.append(level.upper())

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(min(limit, 500))

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    l.id, l.created_at, l.platform, l.level, l.event,
                    l.message, l.details, l.job_id, j.main_subject
                FROM   publish_logs l
                JOIN   video_jobs   j ON j.id = l.job_id
                {where}
                ORDER  BY l.created_at DESC
                LIMIT  %s
                """,
                params,
            )
            return [dict(r) for r in cur.fetchall()]


@app.post("/daily_report", tags=["ops"])
def daily_report_endpoint() -> dict[str, Any]:
    """
    Send full daily report via Telegram: asset stock + server health + stuck jobs.
    Call from n8n Schedule Trigger every morning before videos start.
    """
    from pipeline.stock_monitor import build_stock_report, build_health_report, send_daily_full_report
    send_daily_full_report()
    return {
        "status": "sent",
        "stock":  build_stock_report(),
        "health": build_health_report(),
    }


@app.post("/stock_report", tags=["ops"])
def stock_report_endpoint() -> dict[str, Any]:
    """Send asset stock report via Telegram (kept for backwards compatibility)."""
    from pipeline.stock_monitor import build_stock_report, send_daily_report
    send_daily_report()
    return {"status": "sent", "report": build_stock_report()}


@app.post("/weekly_report", tags=["ops"])
def weekly_report_endpoint(days: int = 7) -> dict[str, Any]:
    """
    Fetch YouTube video stats for the last `days` days and send a
    performance report via Telegram.
    Call from an n8n Schedule Trigger every Monday morning.
    """
    from pipeline.analytics import build_weekly_report, send_weekly_report
    send_weekly_report(days=days)
    return {"status": "sent", "report": build_weekly_report(days=days)}


@app.post("/refresh_hashtags", tags=["ops"])
def refresh_hashtags_endpoint(niche: str | None = None) -> dict[str, Any]:
    """
    Discover trending hashtags for all niches (or a specific one) by analysing
    top-performing YouTube Shorts. Results are cached and automatically used
    in every new video's hashtag set.

    Call from an n8n Schedule Trigger every Monday morning (before /weekly_report).
    Pass ?niche=finance to refresh only one niche.
    """
    from pipeline.hashtag_manager import refresh_and_notify, NICHE_SEARCH_QUERIES
    niches = [niche] if niche else None
    results = refresh_and_notify(niches=niches)
    return {
        "status": "done",
        "niches": {n: len(tags) for n, tags in results.items()},
    }


@app.get("/hashtags/{niche}", tags=["ops"])
def get_hashtags(niche: str) -> dict[str, Any]:
    """Return the current cached hashtags for a niche."""
    from pipeline.hashtag_manager import load_cached_hashtags, _cache_path
    import json
    path = _cache_path(niche)
    if not path.exists():
        return {"niche": niche, "hashtags": [], "message": "No cache yet. Call POST /refresh_hashtags first."}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"niche": niche, "updated_at": data.get("updated_at"), "hashtags": data.get("hashtags", [])}


@app.post("/cleanup", tags=["ops"])
def cleanup_endpoint(older_than_days: int = 30, dry_run: bool = False) -> dict[str, Any]:
    """
    Delete heavy media files (mp4, wav, aac) from jobs older than `older_than_days`
    days in a terminal status. Metadata files (.json, .txt, .srt) are kept.
    DB records are never touched.

    Use dry_run=true first to preview what would be deleted without removing anything.
    Call from an n8n Schedule Trigger weekly (e.g. every Sunday at 03:00).
    """
    from pipeline.stock_monitor import cleanup_and_notify
    return cleanup_and_notify(older_than_days=older_than_days, dry_run=dry_run)


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"service": "shorts-video-worker", "docs": "/docs"}
