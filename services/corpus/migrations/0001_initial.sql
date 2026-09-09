-- GalaxyJobsAi initial schema (docs/03 §6, §7).
-- Applied by galaxy.db.migrate. Idempotent-ish: guarded by CREATE ... IF NOT EXISTS.

CREATE EXTENSION IF NOT EXISTS vector;

-- one row per logical role (docs/03 §6)
CREATE TABLE IF NOT EXISTS canonical_jobs (
    canonical_job_id      TEXT PRIMARY KEY,
    title                 TEXT NOT NULL,
    company               TEXT NOT NULL,
    company_key           TEXT NOT NULL,
    location              JSONB NOT NULL DEFAULT '{}'::jsonb,
    description_md         TEXT,
    primary_url           TEXT NOT NULL,
    fields                JSONB NOT NULL DEFAULT '{}'::jsonb,
    compensation          JSONB,
    -- derived at ingest (docs/02 §3.1); negative-filter columns for docs/04
    seniority             TEXT,
    min_years_experience  SMALLINT,
    onsite_policy         TEXT,
    clearance_required    BOOLEAN,
    jd_keywords           JSONB NOT NULL DEFAULT '[]'::jsonb,
    status                TEXT NOT NULL DEFAULT 'open',
    legitimacy            JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at         TIMESTAMPTZ NOT NULL,
    last_seen_at          TIMESTAMPTZ NOT NULL,
    date_posted           TIMESTAMPTZ,
    normalizer_version    TEXT NOT NULL,
    embedding_version     TEXT,
    embedding             VECTOR(384),
    -- stored generated tsvector; coalesce() prevents NULL description nulling the whole vector
    search_tsv            TSVECTOR GENERATED ALWAYS AS (
                              to_tsvector('english',
                                  coalesce(title, '') || ' ' || coalesce(description_md, ''))
                          ) STORED
);

CREATE INDEX IF NOT EXISTS ix_cj_company_key       ON canonical_jobs (company_key);
CREATE INDEX IF NOT EXISTS ix_cj_status_posted     ON canonical_jobs (status, date_posted DESC);
CREATE INDEX IF NOT EXISTS ix_cj_search_tsv        ON canonical_jobs USING gin (search_tsv);
CREATE INDEX IF NOT EXISTS ix_cj_seniority         ON canonical_jobs (seniority);
CREATE INDEX IF NOT EXISTS ix_cj_min_years         ON canonical_jobs (min_years_experience);
CREATE INDEX IF NOT EXISTS ix_cj_onsite            ON canonical_jobs (onsite_policy);
CREATE INDEX IF NOT EXISTS ix_cj_embedding_version ON canonical_jobs (embedding_version);
-- HNSW vector index is created after pgvector >= 0.8 is confirmed; see migrate() note.

-- every per-source sighting (provenance + dedup audit) (docs/03 §6)
CREATE TABLE IF NOT EXISTS source_observations (
    site              TEXT NOT NULL,
    source_job_id     TEXT NOT NULL,
    canonical_job_id  TEXT NOT NULL REFERENCES canonical_jobs (canonical_job_id) ON DELETE CASCADE,
    url               TEXT NOT NULL,
    raw_title         TEXT,
    raw_fields        JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_observed_at TIMESTAMPTZ NOT NULL,
    last_observed_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (site, source_job_id)
);
CREATE INDEX IF NOT EXISTS ix_so_canonical ON source_observations (canonical_job_id);

-- profiles (docs/03 §7)
CREATE TABLE IF NOT EXISTS profiles (
    user_id         UUID PRIMARY KEY,
    identity        JSONB NOT NULL DEFAULT '{}'::jsonb,
    roles           JSONB NOT NULL DEFAULT '[]'::jsonb,
    skills          JSONB NOT NULL DEFAULT '[]'::jsonb,
    projects        JSONB NOT NULL DEFAULT '[]'::jsonb,
    experience      JSONB NOT NULL DEFAULT '[]'::jsonb,
    education        JSONB NOT NULL DEFAULT '[]'::jsonb,
    publications    JSONB NOT NULL DEFAULT '[]'::jsonb,
    certifications  JSONB NOT NULL DEFAULT '[]'::jsonb,
    preferences     JSONB NOT NULL DEFAULT '{}'::jsonb,
    anything_else   TEXT NOT NULL DEFAULT '',
    profile_version INTEGER NOT NULL DEFAULT 1,
    updated_at      TIMESTAMPTZ
);

-- applications (docs/03 §7)
CREATE TABLE IF NOT EXISTS applications (
    id                   UUID PRIMARY KEY,
    user_id              UUID NOT NULL REFERENCES profiles (user_id) ON DELETE CASCADE,
    canonical_job_id     TEXT NOT NULL REFERENCES canonical_jobs (canonical_job_id),
    status               TEXT,
    fit_score            NUMERIC,
    ats_score            NUMERIC,
    resume_pdf_ref       TEXT,
    cover_ref            TEXT,
    selected_project_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    applied_url          TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ,
    UNIQUE (user_id, canonical_job_id)
);
