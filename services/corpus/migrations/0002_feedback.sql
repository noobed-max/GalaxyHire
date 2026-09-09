-- Search feedback signals (docs/04 §5). Stored from day one; stage-gated effect: filters /
-- blacklist first, ranking weights only once a user has volume.

CREATE TABLE IF NOT EXISTS search_feedback (
    id               BIGSERIAL PRIMARY KEY,
    user_id          UUID NOT NULL,
    canonical_job_id TEXT NOT NULL,
    signal           TEXT NOT NULL,   -- e.g. not_relevant | too_senior | too_junior | good
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_feedback_user ON search_feedback (user_id, created_at DESC);
