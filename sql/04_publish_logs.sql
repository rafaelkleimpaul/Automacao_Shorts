-- =============================================================================
-- Publish Logs — detailed per-platform publication event log
-- Run AFTER 03_publishing.sql
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- publish_logs — one row per event, per platform, per job
-- Unlike publish_results (one final row), this captures every step:
--   STARTED, UPLOADING, SUCCESS, FAILED, RETRIED, etc.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS publish_logs (
    id          BIGSERIAL    PRIMARY KEY,
    job_id      UUID         NOT NULL REFERENCES video_jobs(id) ON DELETE CASCADE,
    platform    VARCHAR(50)  NOT NULL,           -- 'youtube' | 'instagram' | 'tiktok'
    level       VARCHAR(10)  NOT NULL DEFAULT 'INFO',  -- 'INFO' | 'WARNING' | 'ERROR'
    event       VARCHAR(100) NOT NULL,           -- 'STARTED' | 'SUCCESS' | 'FAILED' | etc.
    message     TEXT         NOT NULL,
    details     JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_publish_logs_job_id   ON publish_logs (job_id);
CREATE INDEX IF NOT EXISTS idx_publish_logs_platform ON publish_logs (platform);
CREATE INDEX IF NOT EXISTS idx_publish_logs_level    ON publish_logs (level);
CREATE INDEX IF NOT EXISTS idx_publish_logs_created  ON publish_logs (created_at DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- View: full publication history with job context, ordered by time
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_publish_log_full AS
SELECT
    l.id,
    l.created_at,
    j.main_subject,
    l.platform,
    l.level,
    l.event,
    l.message,
    l.details,
    l.job_id
FROM publish_logs l
JOIN video_jobs   j ON j.id = l.job_id
ORDER BY l.created_at DESC;

-- ─────────────────────────────────────────────────────────────────────────────
-- View: latest result per platform per job (dashboard summary)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_publish_status AS
SELECT DISTINCT ON (r.job_id, r.platform)
    j.main_subject,
    j.created_at   AS job_created_at,
    r.platform,
    r.status,
    r.platform_url,
    r.error_message,
    r.published_at,
    r.job_id
FROM publish_results r
JOIN video_jobs      j ON j.id = r.job_id
ORDER BY r.job_id, r.platform, r.created_at DESC;
