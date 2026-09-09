-- Tags (custom user-defined profiles: "SWE", "AI Engineer", "Business Manager", ...)
-- and uploaded/generated document assets (resumes, cover letters) that can be
-- filed under a tag. Deleting a document removes only that file's row + blob —
-- the underlying profile points stay, because they live in the profile graph,
-- not per-document.
CREATE TABLE IF NOT EXISTS tags(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS documents(
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('resume','cover_letter')),
    topic TEXT NOT NULL DEFAULT '',
    tag_id TEXT REFERENCES tags(id) ON DELETE SET NULL,
    file_path TEXT NOT NULL,
    source_filename TEXT DEFAULT '',
    mime TEXT DEFAULT '',
    excerpt TEXT DEFAULT '',
    source TEXT DEFAULT 'upload',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_documents_kind ON documents(kind);
CREATE INDEX IF NOT EXISTS idx_documents_tag ON documents(tag_id);
