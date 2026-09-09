-- Curated skills column (docs/04 §3.1).
-- jd_keywords stays the frequency-ranked bag for lexical/ATS density; jd_skills is the
-- controlled-vocabulary skill set used by the retrieval skill leg + ranker skill-overlap.
-- Existing rows default to '[]' and are repopulated by the normalizer-version backfill
-- (galaxy.ingestion.backfill.backfill_jd_skills), which raises normalizer_version to '2'.

ALTER TABLE canonical_jobs
    ADD COLUMN IF NOT EXISTS jd_skills JSONB NOT NULL DEFAULT '[]'::jsonb;
