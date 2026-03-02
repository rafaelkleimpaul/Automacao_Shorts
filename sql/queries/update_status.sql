-- =============================================================================
-- Update job status + optional output paths.
-- $1 = new status  (e.g. 'READY_FOR_MANUAL_POST' | 'FAILED')
-- $2 = job id      (UUID)
-- $3 = error_msg   (NULL or text)
-- $4 = output_dir  (NULL or path)
-- $5 = final_video_path (NULL or path)
-- $6 = post_pack_path   (NULL or path)
-- =============================================================================

UPDATE video_jobs
SET
    status            = $1::job_status,
    error_message     = $3,
    output_dir        = COALESCE($4, output_dir),
    final_video_path  = COALESCE($5, final_video_path),
    post_pack_path    = COALESCE($6, post_pack_path),
    completed_at      = CASE WHEN $1 IN ('READY_FOR_MANUAL_POST', 'POSTED', 'FAILED', 'DEAD_LETTER', 'CANCELLED')
                             THEN NOW() ELSE completed_at END,
    failed_at         = CASE WHEN $1 = 'FAILED' THEN NOW() ELSE failed_at END,
    retry_count       = CASE WHEN $1 = 'FAILED'
                             THEN retry_count + 1 ELSE retry_count END
WHERE id = $2
RETURNING id, status, retry_count, max_retries;
