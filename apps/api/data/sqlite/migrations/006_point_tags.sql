-- Bullet-level profile-point tags: which profile points (skills, projects,
-- experience) belong to a profile/tag scope. Mirrors My-Resume-Maker's
-- track model — tags classify points; the master superset stays intact.
-- A point with no rows here is universal material (fits every tag).
CREATE TABLE IF NOT EXISTS point_tags(
    point_kind TEXT NOT NULL CHECK(point_kind IN ('skill', 'project', 'experience')),
    point_id   TEXT NOT NULL,
    tag_id     TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (point_kind, point_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_point_tags_tag ON point_tags(tag_id);
