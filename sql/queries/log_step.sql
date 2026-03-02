-- =============================================================================
-- Insert a log entry for a job step.
-- $1 = job_id    (UUID)
-- $2 = step_name (text)
-- $3 = level     ('DEBUG' | 'INFO' | 'WARNING' | 'ERROR')
-- $4 = message   (text)
-- $5 = details   (JSONB, e.g. '{}')
-- =============================================================================

INSERT INTO job_logs (job_id, step_name, level, message, details)
VALUES ($1, $2, $3::log_level, $4, $5::jsonb);

-- =============================================================================
-- Upsert a step status (mark started or done).
-- $1 = job_id     (UUID)
-- $2 = step_name  (text)
-- $3 = status     ('STARTED' | 'DONE' | 'FAILED' | 'SKIPPED')
-- $4 = metadata   (JSONB, e.g. '{}')
-- =============================================================================
-- (use in a separate Postgres node call)

/*
INSERT INTO job_steps (job_id, step_name, status, metadata)
VALUES ($1, $2, $3::step_status, $4::jsonb)
ON CONFLICT (job_id, step_name) DO UPDATE
SET
    status      = EXCLUDED.status,
    finished_at = CASE WHEN EXCLUDED.status IN ('DONE', 'FAILED', 'SKIPPED')
                       THEN NOW() ELSE job_steps.finished_at END,
    duration_ms = CASE WHEN EXCLUDED.status IN ('DONE', 'FAILED', 'SKIPPED')
                       THEN EXTRACT(EPOCH FROM (NOW() - job_steps.started_at)) * 1000
                       ELSE job_steps.duration_ms END,
    metadata    = EXCLUDED.metadata;
*/
