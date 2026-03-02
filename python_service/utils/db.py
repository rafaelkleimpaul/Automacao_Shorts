"""
Database utilities.
Single psycopg2 connection-pool; all helpers are synchronous
(FastAPI background tasks run them in a thread pool via run_in_executor).
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator, Optional
from uuid import UUID

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]

_pool: Optional[ThreadedConnectionPool] = None


def init_pool(minconn: int = 1, maxconn: int = 5) -> None:
    global _pool
    _pool = ThreadedConnectionPool(minconn, maxconn, DATABASE_URL)
    logger.info("DB connection pool initialised (min=%d, max=%d)", minconn, maxconn)


def get_pool() -> ThreadedConnectionPool:
    if _pool is None:
        init_pool()
    return _pool  # type: ignore[return-value]


@contextmanager
def get_conn() -> Generator[psycopg2.extensions.connection, None, None]:
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# ─────────────────────────────────────────────────────────────────────────────
# Job helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_job(job_id: str) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM video_jobs WHERE id = %s",
                (job_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def update_job_status(
    job_id: str,
    status: str,
    *,
    error_message: Optional[str] = None,
    output_dir: Optional[str] = None,
    final_video_path: Optional[str] = None,
    post_pack_path: Optional[str] = None,
    script_json: Optional[dict] = None,
) -> None:
    sets = ["status = %s"]
    params: list[Any] = [status]

    if error_message is not None:
        sets.append("error_message = %s")
        params.append(error_message)
    if output_dir is not None:
        sets.append("output_dir = %s")
        params.append(output_dir)
    if final_video_path is not None:
        sets.append("final_video_path = %s")
        params.append(final_video_path)
    if post_pack_path is not None:
        sets.append("post_pack_path = %s")
        params.append(post_pack_path)
    if script_json is not None:
        sets.append("script_json = %s")
        params.append(json.dumps(script_json))
    if status in ("READY_FOR_MANUAL_POST", "POSTED", "FAILED", "DEAD_LETTER", "CANCELLED"):
        sets.append("completed_at = %s")
        params.append(datetime.now(timezone.utc))
    if status == "FAILED":
        sets.append("failed_at = %s")
        params.append(datetime.now(timezone.utc))
        sets.append("retry_count = retry_count + 1")

    params.append(job_id)
    sql = f"UPDATE video_jobs SET {', '.join(sets)} WHERE id = %s"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Step helpers  (idempotency)
# ─────────────────────────────────────────────────────────────────────────────

def get_completed_steps(job_id: str) -> set[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT step_name FROM job_steps WHERE job_id = %s AND status = 'DONE'",
                (job_id,),
            )
            return {row[0] for row in cur.fetchall()}


def mark_step_started(job_id: str, step_name: str, metadata: Optional[dict] = None) -> None:
    meta = json.dumps(metadata or {})
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job_steps (job_id, step_name, status, metadata)
                VALUES (%s, %s, 'STARTED', %s)
                ON CONFLICT (job_id, step_name) DO UPDATE
                SET status = 'STARTED', started_at = NOW(), metadata = EXCLUDED.metadata
                """,
                (job_id, step_name, meta),
            )
        conn.commit()


def mark_step_done(job_id: str, step_name: str, metadata: Optional[dict] = None) -> None:
    meta = json.dumps(metadata or {})
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job_steps (job_id, step_name, status, finished_at, metadata)
                VALUES (%s, %s, 'DONE', NOW(), %s)
                ON CONFLICT (job_id, step_name) DO UPDATE
                SET
                    status      = 'DONE',
                    finished_at = NOW(),
                    duration_ms = EXTRACT(EPOCH FROM (NOW() - job_steps.started_at)) * 1000,
                    metadata    = EXCLUDED.metadata
                """,
                (job_id, step_name, meta),
            )
        conn.commit()


def mark_step_failed(job_id: str, step_name: str, error: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job_steps (job_id, step_name, status, finished_at, metadata)
                VALUES (%s, %s, 'FAILED', NOW(), %s)
                ON CONFLICT (job_id, step_name) DO UPDATE
                SET status = 'FAILED', finished_at = NOW(),
                    metadata = jsonb_set(job_steps.metadata, '{error}', %s)
                """,
                (job_id, step_name, json.dumps({"error": error}), json.dumps(error)),
            )
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Log helpers
# ─────────────────────────────────────────────────────────────────────────────

def log_step(
    job_id: str,
    step_name: str,
    message: str,
    level: str = "INFO",
    details: Optional[dict] = None,
) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job_logs (job_id, step_name, level, message, details)
                VALUES (%s, %s, %s::log_level, %s, %s)
                """,
                (job_id, step_name, level, message, json.dumps(details or {})),
            )
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Asset tracking
# ─────────────────────────────────────────────────────────────────────────────

def save_asset(
    job_id: str,
    asset_type: str,
    file_path: str,
    *,
    file_size: Optional[int] = None,
    duration_s: Optional[float] = None,
    metadata: Optional[dict] = None,
) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO assets (job_id, asset_type, file_path, file_size, duration_s, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    job_id,
                    asset_type,
                    file_path,
                    file_size,
                    duration_s,
                    json.dumps(metadata or {}),
                ),
            )
        conn.commit()
