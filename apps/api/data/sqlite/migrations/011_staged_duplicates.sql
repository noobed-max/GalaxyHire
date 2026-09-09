-- Migration 011: Staged Duplicates Persistence
-- Persists candidate profile duplicate pairs staged during asynchronous ingestion.
-- Scoped strictly by company/role or project entity for interactive user resolution.

CREATE TABLE IF NOT EXISTS staged_duplicates(
    id                  TEXT PRIMARY KEY,
    task_id             TEXT NOT NULL REFERENCES ingestion_tasks(task_id) ON DELETE CASCADE,
    document_id         TEXT NOT NULL DEFAULT '',
    tag_id              TEXT DEFAULT NULL REFERENCES tags(id) ON DELETE SET NULL,
    parent_kind         TEXT NOT NULL CHECK(parent_kind IN ('experience', 'project')),
    parent_id           TEXT NOT NULL,
    company_name        TEXT NOT NULL DEFAULT '',
    role                TEXT NOT NULL DEFAULT '',
    period              TEXT NOT NULL DEFAULT '',
    entity_title        TEXT NOT NULL DEFAULT '',
    existing_point_id   TEXT NOT NULL,
    existing_text       TEXT NOT NULL,
    new_text            TEXT NOT NULL,
    explanation         TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'resolved_use_new', 'resolved_keep_both', 'resolved_keep_existing')),
    created_at          TEXT DEFAULT (datetime('now')),
    resolved_at         TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_staged_dup_task ON staged_duplicates(task_id);
CREATE INDEX IF NOT EXISTS idx_staged_dup_status ON staged_duplicates(status);
