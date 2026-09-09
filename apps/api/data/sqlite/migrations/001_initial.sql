CREATE TABLE IF NOT EXISTS leads(
    job_id TEXT PRIMARY KEY,
    title TEXT,
    company TEXT,
    url TEXT,
    platform TEXT,
    status TEXT DEFAULT 'discovered',
    score INTEGER DEFAULT 0,
    reason TEXT DEFAULT '',
    match_points TEXT DEFAULT '',
    asset_path TEXT DEFAULT '',
    cover_letter_path TEXT DEFAULT '',
    selected_projects TEXT DEFAULT '',
    description TEXT DEFAULT '',
    gaps TEXT DEFAULT '',
    resume_version INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    action TEXT,
    ts TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY,
    val TEXT
);

