-- Duplicate/conflict handling: near-duplicate profile points (same work,
-- different wording) grouped so a build can include at most one wording.
-- Groups are tag-agnostic at detection time (tagging is manual and happens
-- after ingest); a group only constrains a build when >= 2 of its members
-- survive tag scoping. Nothing is ever deleted from the master superset.
CREATE TABLE IF NOT EXISTS conflict_groups(
    id          TEXT PRIMARY KEY,
    reason      TEXT NOT NULL DEFAULT 'embedding'
                CHECK(reason IN ('exact', 'fuzzy', 'embedding', 'judge', 'manual')),
    score       REAL NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'open'
                CHECK(status IN ('open', 'picked', 'keep_both', 'dismissed')),
    picked_kind TEXT,
    picked_id   TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS conflict_members(
    group_id   TEXT NOT NULL REFERENCES conflict_groups(id) ON DELETE CASCADE,
    point_kind TEXT NOT NULL CHECK(point_kind IN ('skill', 'project', 'experience')),
    point_id   TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (group_id, point_kind, point_id)
);

-- One point lives in at most one group (transitive merges are done in code).
CREATE UNIQUE INDEX IF NOT EXISTS idx_conflict_member_point
    ON conflict_members(point_kind, point_id);
