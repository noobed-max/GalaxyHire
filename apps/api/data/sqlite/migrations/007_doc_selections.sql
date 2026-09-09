-- Hand-picked resume content per (job, tag): which skills, experience rows,
-- project rows and individual points go into the generated package. One saved
-- selection per scope; presets reuse this table with job_id='' (see
-- doc_selections.list_presets).
CREATE TABLE IF NOT EXISTS doc_selections(
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL DEFAULT '',
    tag_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    selection_json TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(job_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_doc_sel_job ON doc_selections(job_id);
