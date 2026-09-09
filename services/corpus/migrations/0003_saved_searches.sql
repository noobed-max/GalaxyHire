-- Saved searches (docs/04 §5, Phase 6). A named compiled query re-run on demand; `last_run_at`
-- powers "only new since last run".

CREATE TABLE IF NOT EXISTS saved_searches (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL,
    name        TEXT NOT NULL,
    params      JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_run_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)
);
CREATE INDEX IF NOT EXISTS ix_saved_searches_user ON saved_searches (user_id, created_at DESC);
