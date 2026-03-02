-- =============================================================================
-- Shorts Video Pipeline – Seed Data
-- Example jobs with different finance sub-topics.
-- =============================================================================

-- Job 1: ETFs basics (high priority, runs immediately)
INSERT INTO video_jobs (
    main_subject, niche, language, style,
    duration_target_seconds, assets_profile, priority,
    scheduled_at, extra_params
) VALUES (
    'ETFs – How Exchange-Traded Funds Work',
    'finance', 'en_US', 'commentary',
    30, 'finance', 8,
    NOW(),
    '{"tone": "friendly", "complexity": "beginner"}'
);

-- Job 2: Compound interest
INSERT INTO video_jobs (
    main_subject, niche, language, style,
    duration_target_seconds, assets_profile, priority,
    scheduled_at, extra_params
) VALUES (
    'Compound Interest – The 8th Wonder of the World',
    'finance', 'en_US', 'educational',
    35, 'finance', 7,
    NOW() + INTERVAL '2 minutes',
    '{"tone": "motivational", "complexity": "beginner"}'
);

-- Job 3: Credit score improvement
INSERT INTO video_jobs (
    main_subject, niche, language, style,
    duration_target_seconds, assets_profile, priority,
    scheduled_at, extra_params
) VALUES (
    '5 Ways to Improve Your Credit Score Fast',
    'finance', 'en_US', 'commentary',
    40, 'finance', 6,
    NOW() + INTERVAL '4 minutes',
    '{"tone": "practical", "complexity": "intermediate"}'
);

-- Job 4: Inflation explained
INSERT INTO video_jobs (
    main_subject, niche, language, style,
    duration_target_seconds, assets_profile, priority,
    scheduled_at, extra_params
) VALUES (
    'Inflation Explained – What It Means for Your Wallet',
    'finance', 'en_US', 'educational',
    30, 'finance', 5,
    NOW() + INTERVAL '6 minutes',
    '{"tone": "informative", "complexity": "beginner"}'
);

-- Job 5: Emergency fund
INSERT INTO video_jobs (
    main_subject, niche, language, style,
    duration_target_seconds, assets_profile, priority,
    scheduled_at, extra_params
) VALUES (
    'Why You Need an Emergency Fund (And How to Build One)',
    'finance', 'en_US', 'commentary',
    25, 'finance', 5,
    NOW() + INTERVAL '8 minutes',
    '{"tone": "urgent", "complexity": "beginner"}'
);

-- ─────────────────────────────────────────────────────────────────────────────
-- HOW TO INSERT A CUSTOM JOB
-- Copy and adapt the template below:
-- ─────────────────────────────────────────────────────────────────────────────
/*
INSERT INTO video_jobs (
    main_subject,           -- The topic for this specific video
    niche,                  -- Content niche (default: 'finance')
    language,               -- Language code  (default: 'en_US')
    style,                  -- 'commentary' | 'educational' | 'listicle'
    duration_target_seconds,-- 15–60 seconds
    assets_profile,         -- Folder under /data/assets/broll/<profile>/
    priority,               -- 1 (lowest) → 10 (highest)
    scheduled_at,           -- When to run (NOW() = run next cycle)
    extra_params            -- Optional JSON overrides
) VALUES (
    'Your Custom Topic Here',
    'finance',
    'en_US',
    'commentary',
    30,
    'finance',
    7,
    NOW(),
    '{"tone": "friendly", "complexity": "beginner"}'
);
*/
