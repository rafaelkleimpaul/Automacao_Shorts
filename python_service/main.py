"""
Shorts Video Worker – FastAPI entry point.

Endpoints:
  GET  /health              – liveness + DB probe
  POST /run/{job_id}        – execute pipeline for a job (called by n8n)
  GET  /job/{job_id}        – query job status + outputs
  GET  /jobs                – list recent jobs (last 50)
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from utils.db import get_conn, get_job, init_pool, update_job_status
from utils.logger import configure_logging
from pipeline.orchestrator import run_pipeline

configure_logging()
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Startup / shutdown
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting video worker…")
    init_pool(minconn=1, maxconn=5)
    yield
    logger.info("Shutting down video worker.")


app = FastAPI(
    title="Shorts Video Worker",
    version="1.0.0",
    description="Local video generation pipeline for finance short-form content.",
    lifespan=lifespan,
)


# ─────────────────────────────────────────────────────────────────────────────
# Background task wrapper
# ─────────────────────────────────────────────────────────────────────────────

def _run_pipeline_task(job_id: str) -> None:
    """Runs synchronously in a thread pool (FastAPI BackgroundTasks)."""
    try:
        result = run_pipeline(job_id)
        logger.info("Pipeline completed for job %s: %s", job_id, result)
    except Exception as exc:
        logger.error("Pipeline error for job %s: %s", job_id, exc, exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"])
def health() -> dict[str, Any]:
    """Liveness + basic DB probe."""
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
        "data_root": os.environ.get("DATA_ROOT", "/data"),
    }


@app.post("/run/{job_id}", tags=["pipeline"])
def run_job(job_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """
    Trigger pipeline execution for a job.
    Returns immediately with status=accepted; pipeline runs in background.
    n8n should poll /job/{job_id} or rely on the DB status for completion.
    """
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if job["status"] in ("READY_FOR_MANUAL_POST", "POSTED"):
        return {
            "status":  "already_done",
            "job_id":  job_id,
            "message": f"Job already completed with status={job['status']}",
        }

    if job["status"] == "PROCESSING":
        # Could be a retry – allow re-entry (orchestrator is idempotent)
        logger.info("Job %s already PROCESSING – re-entering pipeline (idempotent)", job_id)

    # Ensure status is PROCESSING before starting
    if job["status"] == "PENDING":
        update_job_status(job_id, "PROCESSING")

    background_tasks.add_task(_run_pipeline_task, job_id)

    return {
        "status":  "accepted",
        "job_id":  job_id,
        "message": "Pipeline started. Poll /job/{job_id} for status.",
    }


@app.get("/job/{job_id}", tags=["pipeline"])
def get_job_status(job_id: str) -> dict[str, Any]:
    """Return current status and output paths for a job."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

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
        "started_at":       str(job["started_at"]) if job.get("started_at") else None,
        "completed_at":     str(job["completed_at"]) if job.get("completed_at") else None,
        "retry_count":      job.get("retry_count", 0),
    }


@app.get("/jobs", tags=["pipeline"])
def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    """List the most recent jobs."""
    with get_conn() as conn:
        import psycopg2.extras
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, status, main_subject, niche, language, priority,
                       created_at, started_at, completed_at, retry_count
                FROM   video_jobs
                ORDER  BY created_at DESC
                LIMIT  %s
                """,
                (min(limit, 200),),
            )
            return [dict(row) for row in cur.fetchall()]


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"service": "shorts-video-worker", "docs": "/docs"}
