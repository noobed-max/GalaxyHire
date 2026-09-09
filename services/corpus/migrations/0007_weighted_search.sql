-- Field-weighted full-text search.
--
-- A title match is much stronger evidence than the same token appearing once in a long job
-- description. The original search_tsv flattened both fields into one unweighted document, so
-- PostgreSQL could not express that distinction. Keep the original generated column for backwards
-- compatibility and add the weighted document as a separately indexed migration.

ALTER TABLE canonical_jobs
    ADD COLUMN IF NOT EXISTS search_tsv_weighted TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(jd_skills::text, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(company, '')), 'C') ||
        setweight(to_tsvector('english', coalesce(description_md, '')), 'D')
    ) STORED;

CREATE INDEX IF NOT EXISTS ix_cj_search_tsv_weighted
    ON canonical_jobs USING gin (search_tsv_weighted);
