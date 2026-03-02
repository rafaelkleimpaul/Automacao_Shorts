-- =============================================================================
-- Shorts Video Pipeline – Database Schema
-- PostgreSQL 16  |  Run order: 01_schema.sql  →  02_seeds.sql
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- Extensions
-- ─────────────────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ─────────────────────────────────────────────────────────────────────────────
-- Enum types
-- ─────────────────────────────────────────────────────────────────────────────
DO $$ BEGIN
    CREATE TYPE job_status AS ENUM (
        'PENDING',
        'PROCESSING',
        'READY_FOR_MANUAL_POST',
        'POSTED',
        'FAILED',
        'DEAD_LETTER',
        'CANCELLED'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE step_status AS ENUM (
        'STARTED',
        'DONE',
        'SKIPPED',
        'FAILED'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE log_level AS ENUM (
        'DEBUG',
        'INFO',
        'WARNING',
        'ERROR'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- video_jobs  (main control table)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS video_jobs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status                  job_status NOT NULL DEFAULT 'PENDING',
    priority                SMALLINT NOT NULL DEFAULT 5,          -- 1 (low) → 10 (high)

    -- Content configuration ──────────────────────────────────────────────────
    language                VARCHAR(10) NOT NULL DEFAULT 'en_US',
    niche                   VARCHAR(100) NOT NULL DEFAULT 'finance',
    main_subject            VARCHAR(255) NOT NULL,                -- e.g. "ETFs", "budgeting"
    style                   VARCHAR(50) NOT NULL DEFAULT 'commentary',
    duration_target_seconds SMALLINT NOT NULL DEFAULT 30
                                CHECK (duration_target_seconds BETWEEN 15 AND 60),
    assets_profile          VARCHAR(100) DEFAULT 'finance',       -- maps to /data/assets/broll/<profile>/
    extra_params            JSONB DEFAULT '{}',                   -- arbitrary overrides

    -- Scheduling ─────────────────────────────────────────────────────────────
    scheduled_at            TIMESTAMPTZ DEFAULT NOW(),
    retry_count             SMALLINT NOT NULL DEFAULT 0,
    max_retries             SMALLINT NOT NULL DEFAULT 3,

    -- Lock info ──────────────────────────────────────────────────────────────
    locked_by               VARCHAR(255),
    locked_at               TIMESTAMPTZ,

    -- Output ─────────────────────────────────────────────────────────────────
    output_dir              TEXT,                                 -- /data/videos/YYYY-MM-DD/<id>/
    final_video_path        TEXT,
    post_pack_path          TEXT,
    script_json             JSONB,

    -- Timestamps ─────────────────────────────────────────────────────────────
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at              TIMESTAMPTZ,
    completed_at            TIMESTAMPTZ,
    failed_at               TIMESTAMPTZ,
    error_message           TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_jobs_status_scheduled
    ON video_jobs (status, scheduled_at, priority DESC)
    WHERE status = 'PENDING';

CREATE INDEX IF NOT EXISTS idx_jobs_created
    ON video_jobs (created_at DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- job_steps  (per-step idempotency tracking)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS job_steps (
    id          BIGSERIAL PRIMARY KEY,
    job_id      UUID NOT NULL REFERENCES video_jobs(id) ON DELETE CASCADE,
    step_name   VARCHAR(100) NOT NULL,
    status      step_status NOT NULL DEFAULT 'STARTED',
    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    duration_ms INTEGER,
    metadata    JSONB DEFAULT '{}',
    UNIQUE (job_id, step_name)
);

CREATE INDEX IF NOT EXISTS idx_steps_job_id ON job_steps (job_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- job_logs  (structured execution log)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS job_logs (
    id          BIGSERIAL PRIMARY KEY,
    job_id      UUID NOT NULL REFERENCES video_jobs(id) ON DELETE CASCADE,
    step_name   VARCHAR(100),
    level       log_level NOT NULL DEFAULT 'INFO',
    message     TEXT NOT NULL,
    details     JSONB DEFAULT '{}',
    logged_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_logs_job_id    ON job_logs (job_id, logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_level     ON job_logs (level) WHERE level IN ('ERROR', 'WARNING');

-- ─────────────────────────────────────────────────────────────────────────────
-- assets  (tracks files used / generated per job)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS assets (
    id          BIGSERIAL PRIMARY KEY,
    job_id      UUID NOT NULL REFERENCES video_jobs(id) ON DELETE CASCADE,
    asset_type  VARCHAR(50) NOT NULL,  -- 'broll_video','broll_image','music','voice','srt','final_video','post_pack'
    file_path   TEXT NOT NULL,
    file_size   BIGINT,
    duration_s  NUMERIC(8,3),
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_assets_job_id ON assets (job_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- dead_letters  (permanently failed jobs for review)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dead_letters (
    id              BIGSERIAL PRIMARY KEY,
    original_job_id UUID NOT NULL,
    job_snapshot    JSONB NOT NULL,         -- full row at time of failure
    steps_snapshot  JSONB DEFAULT '[]',
    logs_snapshot   JSONB DEFAULT '[]',
    moved_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason          TEXT
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Helper functions
-- ─────────────────────────────────────────────────────────────────────────────

-- Claim the next PENDING job atomically (FOR UPDATE SKIP LOCKED).
-- Returns 0 or 1 row.
CREATE OR REPLACE FUNCTION claim_next_job(p_locked_by VARCHAR)
RETURNS TABLE (
    id                      UUID,
    main_subject            VARCHAR,
    niche                   VARCHAR,
    language                VARCHAR,
    style                   VARCHAR,
    duration_target_seconds SMALLINT,
    assets_profile          VARCHAR,
    extra_params            JSONB
)
LANGUAGE sql AS $$
    UPDATE video_jobs
    SET
        status    = 'PROCESSING',
        locked_by = p_locked_by,
        locked_at = NOW(),
        started_at = NOW()
    WHERE id = (
        SELECT id
        FROM   video_jobs
        WHERE  status       = 'PENDING'
          AND  (scheduled_at IS NULL OR scheduled_at <= NOW())
          AND  retry_count  < max_retries
        ORDER  BY priority DESC, created_at ASC
        LIMIT  1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING
        id, main_subject, niche, language, style,
        duration_target_seconds, assets_profile, extra_params;
$$;

-- Move a failed job to dead_letters when retries are exhausted.
CREATE OR REPLACE FUNCTION move_to_dead_letter(p_job_id UUID, p_reason TEXT)
RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO dead_letters (original_job_id, job_snapshot, steps_snapshot, logs_snapshot, reason)
    SELECT
        j.id,
        to_jsonb(j),
        COALESCE((SELECT jsonb_agg(to_jsonb(s)) FROM job_steps s WHERE s.job_id = j.id), '[]'),
        COALESCE((SELECT jsonb_agg(to_jsonb(l)) FROM job_logs  l WHERE l.job_id = j.id), '[]'),
        p_reason
    FROM video_jobs j
    WHERE j.id = p_job_id;

    UPDATE video_jobs
    SET status = 'DEAD_LETTER', error_message = p_reason
    WHERE id = p_job_id;
END;
$$;
