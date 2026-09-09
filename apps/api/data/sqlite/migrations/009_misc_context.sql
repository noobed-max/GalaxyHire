-- Miscellaneous user context ("Miscellaneous user data" box in Add experience).
--
-- ONE record per user, not a document library: every new paste/upload is MERGED with the
-- previous text by the LLM into a single comprehensive record (visa, citizenship, military,
-- gender/EEO answers, notice period, ... — the facts that belong on application forms but not
-- on a resume). A separate singleton table enforces that structurally: there is no second row
-- to accumulate, unlike documents.kind which would invite multi-row pile-up.
-- Misc stays OUT of the profile graph/vectors/snapshot (it is untyped prose, not skills or
-- roles) and is read only where form-fill or generation context needs it.
CREATE TABLE IF NOT EXISTS misc_context(
    id TEXT PRIMARY KEY CHECK(id = 'singleton'),
    merged_text TEXT NOT NULL DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO misc_context(id, merged_text) VALUES('singleton', '');
