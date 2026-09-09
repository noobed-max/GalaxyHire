-- Migration 010: Persistent Ingestion Task Session Tracking
-- Tracks asynchronous resume parsing jobs, 4-stage progression, and LLM reasoning thoughts.
-- Enables the UI to reconnect to active/recent parsing jobs across page navigation and server restarts.

CREATE TABLE IF NOT EXISTS ingestion_tasks(
    task_id               TEXT PRIMARY KEY,
    status                TEXT NOT NULL DEFAULT 'processing'
                          CHECK(status IN ('processing', 'completed', 'failed', 'cancelled', 'review_required')),
    filename              TEXT NOT NULL DEFAULT '',
    file_path             TEXT NOT NULL DEFAULT '',
    tag_id                TEXT REFERENCES tags(id) ON DELETE SET NULL,
    topic                 TEXT NOT NULL DEFAULT '',
    stage                 TEXT NOT NULL DEFAULT 'reading'
                          CHECK(stage IN ('reading', 'extracting', 'deduping', 'indexing', 'completed', 'failed', 'idle')),
    stage_number          INTEGER NOT NULL DEFAULT 1,
    stage_message         TEXT NOT NULL DEFAULT '',
    progress_percent      INTEGER NOT NULL DEFAULT 0,
    thoughts_json         TEXT NOT NULL DEFAULT '[]',
    duplicates_json       TEXT NOT NULL DEFAULT '[]',
    result_json           TEXT NOT NULL DEFAULT '{}',
    error                 TEXT,
    started_at            TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at          TEXT,
    created_at            TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ingestion_tasks_status ON ingestion_tasks(status);
CREATE INDEX IF NOT EXISTS idx_ingestion_tasks_started ON ingestion_tasks(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_ingestion_tasks_tag ON ingestion_tasks(tag_id);
