-- =============================================================================
-- Publishing Extension – Run AFTER 01_schema.sql
-- Adds: new job statuses, publish_results table, oauth_tokens table
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- Extend job_status enum
-- ─────────────────────────────────────────────────────────────────────────────
-- PostgreSQL requires each ADD VALUE in a separate transaction,
-- so we wrap each one individually.

DO $$ BEGIN ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'PUBLISHING';    EXCEPTION WHEN others THEN NULL; END $$;
DO $$ BEGIN ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'PUBLISHED';     EXCEPTION WHEN others THEN NULL; END $$;
DO $$ BEGIN ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'PUBLISH_FAILED'; EXCEPTION WHEN others THEN NULL; END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- publish_results — one row per platform per job
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS publish_results (
    id                  BIGSERIAL PRIMARY KEY,
    job_id              UUID NOT NULL REFERENCES video_jobs(id) ON DELETE CASCADE,
    platform            VARCHAR(50)  NOT NULL,   -- 'youtube' | 'instagram' | 'tiktok'
    status              VARCHAR(50)  NOT NULL,   -- 'SUCCESS' | 'FAILED' | 'SKIPPED'
    platform_post_id    TEXT,                    -- video ID returned by the platform
    platform_url        TEXT,                    -- public URL of the posted video
    error_message       TEXT,
    published_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, platform)
);

CREATE INDEX IF NOT EXISTS idx_pub_results_job_id   ON publish_results (job_id);
CREATE INDEX IF NOT EXISTS idx_pub_results_platform ON publish_results (platform, status);

-- ─────────────────────────────────────────────────────────────────────────────
-- oauth_tokens — stores OAuth refresh tokens (alternative to file storage)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS oauth_tokens (
    platform        VARCHAR(50) PRIMARY KEY,    -- 'youtube' | 'tiktok'
    access_token    TEXT,
    refresh_token   TEXT,
    token_type      VARCHAR(50),
    expires_at      TIMESTAMPTZ,
    extra           JSONB DEFAULT '{}',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Helper view – publishing summary per job
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_publish_summary AS
SELECT
    j.id                       AS job_id,
    j.main_subject,
    j.status                   AS job_status,
    j.completed_at,
    r.platform,
    r.status                   AS pub_status,
    r.platform_url,
    r.error_message,
    r.published_at
FROM video_jobs   j
LEFT JOIN publish_results r ON r.job_id = j.id
WHERE j.status IN ('PUBLISHING', 'PUBLISHED', 'PUBLISH_FAILED', 'READY_FOR_MANUAL_POST')
ORDER BY j.completed_at DESC;
