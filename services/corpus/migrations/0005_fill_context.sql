-- Apply-prepare fill context (docs/11 §W1.4).
-- When the user hits Apply on the website, the backend tailors + renders the resume and stores a
-- per-job fill context (fixed identity/anything-else + job-tailored skills/projects/experience)
-- for the fill-only extension to read via GET /applications/fill-context. resume_path is where the
-- rendered PDF was written on disk (RESUME_OUTPUT_DIR).

ALTER TABLE applications ADD COLUMN IF NOT EXISTS fill_context JSONB;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS resume_path TEXT;
