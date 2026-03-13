-- =============================================================================
-- Topic Deduplication – Run AFTER 01_schema.sql
-- Uses PostgreSQL trigram similarity to prevent generating near-duplicate
-- video topics within a configurable lookback window.
-- =============================================================================

-- pg_trgm: trigram similarity search (built-in Postgres extension)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- GIN index for fast similarity queries on main_subject
CREATE INDEX IF NOT EXISTS idx_jobs_subject_trgm
    ON video_jobs USING gin (lower(main_subject) gin_trgm_ops);

-- ─────────────────────────────────────────────────────────────────────────────
-- Helper function: find recent jobs with a similar main_subject
-- Returns up to 5 matching subjects, ordered by similarity descending.
--
-- Args:
--   p_subject   TEXT    – candidate topic to check
--   p_niche     VARCHAR – limit to same niche
--   p_days      INT     – lookback window in days (default 30)
--   p_threshold FLOAT   – minimum trigram similarity 0..1 (default 0.45)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION similar_recent_topics(
    p_subject   TEXT,
    p_niche     VARCHAR,
    p_days      INT     DEFAULT 30,
    p_threshold FLOAT   DEFAULT 0.45
)
RETURNS TABLE (main_subject TEXT, sim FLOAT)
LANGUAGE sql STABLE AS $$
    SELECT
        j.main_subject::TEXT,
        similarity(lower(j.main_subject), lower(p_subject))::FLOAT AS sim
    FROM video_jobs j
    WHERE j.niche       = p_niche
      AND j.created_at >= NOW() - make_interval(days => p_days)
      AND j.status NOT IN ('CANCELLED', 'DEAD_LETTER')
      AND similarity(lower(j.main_subject), lower(p_subject)) >= p_threshold
    ORDER BY sim DESC
    LIMIT 5;
$$;
