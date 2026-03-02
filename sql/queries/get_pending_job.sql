-- =============================================================================
-- Atomically claim the next PENDING job.
-- Returns 0 or 1 row. Uses FOR UPDATE SKIP LOCKED for safe concurrency.
-- Compatible with n8n 2.9.4 Postgres node (Execute Query operation).
--
-- Usage in n8n: paste this query directly into the Postgres node.
-- $1 → locked_by label, e.g. 'n8n' or a workflow execution ID expression.
-- =============================================================================

UPDATE video_jobs
SET
    status     = 'PROCESSING',
    locked_by  = $1,
    locked_at  = NOW(),
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
    id,
    main_subject,
    niche,
    language,
    style,
    duration_target_seconds,
    assets_profile,
    extra_params,
    priority,
    retry_count;
