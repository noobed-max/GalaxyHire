-- Preserve each LLM-authoritative resume parse independently of the merged
-- master profile. This makes resume IDs durable and enables safe future
-- reprocessing without reconstructing source ownership from flattened text.
ALTER TABLE documents ADD COLUMN resume_id TEXT DEFAULT '';
ALTER TABLE documents ADD COLUMN parsed_profile_json TEXT DEFAULT '{}';

CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_resume_id
    ON documents(resume_id) WHERE resume_id <> '';
