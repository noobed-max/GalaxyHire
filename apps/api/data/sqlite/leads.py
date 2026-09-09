from __future__ import annotations
import logging

import json
import html
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from data.sqlite.connection import DEFAULT_DB_PATH, get_connection

# Compatibility for older tests and integrations that monkeypatch this module's
# connection factory. Runtime code uses get_connection directly.
connect = get_connection


LEAD_SELECT_COLUMNS = (
    "job_id,title,company,url,platform,status,score,reason,match_points,asset_path,"
    "description,gaps,cover_letter_path,selected_projects,kind,budget,signal_score,"
    "signal_reason,signal_tags,outreach_reply,outreach_dm,source_meta,feedback,"
    "feedback_note,followup_due_at,last_contacted_at,outreach_email,proposal_draft,"
    "fit_bullets,followup_sequence,proof_snippet,tech_stack,location,urgency,"
    "base_signal_score,learning_delta,learning_reason,created_at,resume_version,base_score"
)
LEAD_COLUMN_NAMES = tuple(part.strip() for part in LEAD_SELECT_COLUMNS.split(","))

# Discovery records are deliberately treated as a user's local, recoverable view of the shared
# corpus.  A fresh scrape may retire rows that were only ever shown as ``discovered``; it must not
# erase application history or anything the user has engaged with.  Thirty days is long enough to
# keep a user from losing a saved posting during normal review, while bounding the scope of a
# cleanup after a query is run repeatedly.
DISCOVERY_RETIRE_AFTER_DAYS = 30
_TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "referrer", "source", "src",
}


def normalized_source_url(value: object) -> str:
    """Return a stable comparison key for a source URL.

    Providers occasionally vary tracking parameters, fragments, host casing, or a trailing slash
    between runs.  Canonical IDs are the primary identity; this key is the conservative fallback
    for old/local leads whose canonical ID was generated differently.  Empty URLs intentionally
    produce an empty key and therefore never make two unrelated rows duplicates.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw.rstrip("/").casefold()
    if not parsed.scheme or not parsed.netloc:
        return raw.rstrip("/").casefold()
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    try:
        port = parsed.port
    except ValueError:
        port = None
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host
    if parsed.username or parsed.password:
        # Source URLs should not normally include credentials, but retaining them in the key is
        # safer than accidentally equating two authenticated endpoints.
        userinfo = f"{parsed.username or ''}:{parsed.password or ''}@"
        netloc = userinfo + netloc
    if port and not default_port:
        netloc = f"{netloc}:{port}"
    path = parsed.path.rstrip("/") or "/"
    kept_query = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_QUERY_KEYS and not key.casefold().startswith("utm_")
    ]
    kept_query.sort()
    return urlunsplit((scheme, netloc, path, urlencode(kept_query, doseq=True), ""))


def _lead_identity(lead: dict) -> tuple[str, str]:
    """Return ``(canonical-id, normalized-url)`` without inventing an empty identity."""
    canonical_id = str(lead.get("job_id") or lead.get("canonical_job_id") or "").strip().casefold()
    return canonical_id, normalized_source_url(lead.get("url") or lead.get("primary_url"))


def _identity_sets(leads: list[dict]) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    urls: set[str] = set()
    for lead in leads:
        canonical_id, source_url = _lead_identity(lead)
        if canonical_id:
            ids.add(canonical_id)
        if source_url:
            urls.add(source_url)
    return ids, urls


def row_get(row, key: str, default=None):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        # row has no string-key access (e.g. a positional tuple) — fall back to
        # the known column order. This is an expected path, so don't warn.
        pass
    try:
        return row[LEAD_COLUMN_NAMES.index(key)]
    except ValueError:
        # L3: `key` is not a known lead column. This indicates schema drift, so
        # log it at DEBUG to keep it visible in diagnostics without spamming.
        logging.getLogger(__name__).debug("row_get: unknown lead column %r (schema drift?)", key)
        return default
    except (IndexError, TypeError):
        return default


def json_list(value: str | list) -> list:
    if isinstance(value, list):
        return value
    raw = str(value or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except Exception as log_exc:
        logging.getLogger(__name__).warning('suppressed exception in json_list: %s', log_exc)
        return [part.strip() for part in raw.split(",") if part.strip()]


def json_dict(value: str | dict) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception as log_exc:
        logging.getLogger(__name__).warning('suppressed exception in json_dict: %s', log_exc)
        return {}


def json_dumps_list(items: list | str | None) -> str:
    if items is None:
        values = []
    elif isinstance(items, str):
        raw = items.strip()
        if not raw:
            values = []
        elif raw.startswith("["):
            return raw
        else:
            values = [part.strip() for part in raw.split(",") if part.strip()]
    else:
        values = [str(item).strip() for item in items if str(item).strip()]
    return json.dumps(values, ensure_ascii=False)


def lead_row_dict(row) -> dict:
    source_meta = json_dict(row_get(row, "source_meta") or "{}")
    asset_path = row_get(row, "asset_path") or ""
    return {
        "job_id": row_get(row, "job_id"), "title": row_get(row, "title"), "company": row_get(row, "company"), "url": row_get(row, "url"),
        "platform": row_get(row, "platform"), "status": row_get(row, "status"), "score": row_get(row, "score") or 0,
        "reason": row_get(row, "reason") or "",
        "match_points": json_list(row_get(row, "match_points") or "[]"),
        "asset": asset_path,
        "description": row_get(row, "description") or "",
        "gaps": json_list(row_get(row, "gaps") or "[]"),
        "resume_asset": asset_path,
        "cover_letter_asset": row_get(row, "cover_letter_path") or "",
        "selected_projects": json_list(row_get(row, "selected_projects") or "[]"),
        "kind": row_get(row, "kind") or "job",
        "budget": row_get(row, "budget") or "",
        "signal_score": row_get(row, "signal_score") or 0,
        "signal_reason": row_get(row, "signal_reason") or "",
        "signal_tags": json_list(row_get(row, "signal_tags") or "[]"),
        "outreach_reply": row_get(row, "outreach_reply") or "",
        "outreach_dm": row_get(row, "outreach_dm") or "",
        "source_meta": source_meta,
        "lead_quality_score": source_meta.get("lead_quality_score") or 0,
        "lead_quality_reason": source_meta.get("lead_quality_reason") or "",
        "keyword_coverage": source_meta.get("keyword_coverage") or {},
        "contact_lookup": source_meta.get("contact_lookup") or {},
        "feedback": row_get(row, "feedback") or "",
        "feedback_note": row_get(row, "feedback_note") or "",
        "followup_due_at": row_get(row, "followup_due_at") or "",
        "last_contacted_at": row_get(row, "last_contacted_at") or "",
        "outreach_email": row_get(row, "outreach_email") or "",
        "proposal_draft": row_get(row, "proposal_draft") or "",
        "fit_bullets": json_list(row_get(row, "fit_bullets") or "[]"),
        "followup_sequence": json_list(row_get(row, "followup_sequence") or "[]"),
        "proof_snippet": row_get(row, "proof_snippet") or "",
        "tech_stack": json_list(row_get(row, "tech_stack") or "[]"),
        "location": row_get(row, "location") or "",
        "urgency": row_get(row, "urgency") or "",
        "base_signal_score": row_get(row, "base_signal_score") or 0,
        "learning_delta": row_get(row, "learning_delta") or 0,
        "learning_reason": row_get(row, "learning_reason") or "",
        "created_at": row_get(row, "created_at") or "",
        "resume_version": row_get(row, "resume_version") or 0,
        "base_score": row_get(row, "base_score") or 0,
    }


def save_lead(lead: dict, db_path: str = DEFAULT_DB_PATH) -> bool:
    conn = get_connection(db_path)
    try:
        result = conn.execute(
            """
            INSERT OR IGNORE INTO leads(
                job_id,title,company,url,platform,description,kind,budget,
                signal_score,signal_reason,signal_tags,outreach_reply,outreach_dm,
                outreach_email,proposal_draft,fit_bullets,followup_sequence,
                proof_snippet,tech_stack,location,urgency,base_signal_score,
                learning_delta,learning_reason,source_meta
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                lead.get("job_id") or "",
                lead.get("title") or "",
                lead.get("company") or "",
                lead.get("url") or "",
                lead.get("platform") or "",
                lead.get("description") or "",
                lead.get("kind") or "job",
                lead.get("budget") or "",
                int(lead.get("signal_score") or 0),
                str(lead.get("signal_reason") or "")[:700],
                json_dumps_list(lead.get("signal_tags")),
                lead.get("outreach_reply") or "",
                lead.get("outreach_dm") or "",
                lead.get("outreach_email") or "",
                lead.get("proposal_draft") or "",
                json_dumps_list(lead.get("fit_bullets")),
                json_dumps_list(lead.get("followup_sequence")),
                lead.get("proof_snippet") or "",
                json_dumps_list(lead.get("tech_stack")),
                lead.get("location") or "",
                lead.get("urgency") or "",
                int(lead.get("base_signal_score") or lead.get("signal_score") or 0),
                int(lead.get("learning_delta") or 0),
                str(lead.get("learning_reason") or "")[:700],
                json.dumps(lead.get("source_meta") or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
        # sqlite's rowcount is 0 for INSERT OR IGNORE when the canonical id was already present.
        # Existing callers ignore the return value; discovery uses it to report an honest new vs
        # deduplicated count.
        return bool(getattr(result, "rowcount", 0))
    finally:
        conn.close()


def save_discovery_leads(
    leads: list[dict],
    *,
    query: str,
    location: str = "",
    portals: list[str] | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    """Save one fresh result set with canonical-id/URL deduplication and provenance.

    Deduplication happens against both the result set currently being written and all existing
    local leads.  The latter matters for older rows whose generated canonical id differs but whose
    source URL is the same.  Existing rows are never updated: their application status, notes,
    generated assets, and user feedback remain authoritative.

    ``discovery_scope`` is written only on newly inserted rows.  It is intentionally metadata, not
    a schema-level query table, so old/manual leads without this marker are outside the bounded
    retirement policy below.
    """
    existing = get_all_leads(db_path)
    seen_ids, seen_urls = _identity_sets(existing)
    scope = {
        "query": " ".join(str(query or "").split()).casefold(),
        "location": " ".join(str(location or "").split()).casefold(),
        "portals": sorted({str(p).strip() for p in (portals or []) if str(p).strip()}),
        "collected_at": datetime.now(UTC).isoformat(),
    }
    saved = 0
    deduplicated = 0
    accepted: list[dict] = []
    for original in leads:
        lead = dict(original or {})
        canonical_id, source_url = _lead_identity(lead)
        if not canonical_id and not source_url:
            # An unidentifiable result cannot be safely persisted or compared later.
            deduplicated += 1
            continue
        if (canonical_id and canonical_id in seen_ids) or (source_url and source_url in seen_urls):
            deduplicated += 1
            continue
        meta = dict(lead.get("source_meta") or {})
        meta["discovery_scope"] = scope
        lead["source_meta"] = meta
        inserted = save_lead(lead, db_path)
        if inserted:
            saved += 1
            accepted.append(lead)
        else:
            # A concurrent writer may have inserted the canonical ID between our snapshot and
            # INSERT OR IGNORE.  Report it exactly like a duplicate rather than claiming a new
            # lead was saved.
            deduplicated += 1
        if canonical_id:
            seen_ids.add(canonical_id)
        if source_url:
            seen_urls.add(source_url)
    return {"saved": saved, "deduplicated": deduplicated, "leads": accepted}


def _scope_value(scope: object, key: str) -> str:
    if not isinstance(scope, dict):
        return ""
    return " ".join(str(scope.get(key) or "").split()).casefold()


def _parse_created_at(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def retire_stale_discovery_leads(
    *,
    query: str,
    location: str = "",
    portals: list[str] | None = None,
    fresh_leads: list[dict] | None = None,
    max_age_days: int = DISCOVERY_RETIRE_AFTER_DAYS,
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    """Archive stale, unengaged rows superseded by a completed fresh scrape.

    Exact safety policy: only rows with ``status='discovered'``, empty feedback, a matching
    ``discovery_scope`` query/location, a scope whose portals are covered by this run, and a
    ``created_at`` older than ``max_age_days`` are eligible.  A fresh canonical ID or normalized
    source URL protects a row from retirement.  Retirement is a status transition to
    ``discarded`` (plus an audit event and metadata marker), never a hard delete.  Rows without
    scope metadata and every engaged/application state are intentionally untouched.
    """
    requested_query = " ".join(str(query or "").split()).casefold()
    requested_location = " ".join(str(location or "").split()).casefold()
    requested_portals = {str(p).strip() for p in (portals or []) if str(p).strip()}
    fresh_ids, fresh_urls = _identity_sets(fresh_leads or [])
    cutoff = datetime.now(UTC) - timedelta(days=max(1, int(max_age_days or DISCOVERY_RETIRE_AFTER_DAYS)))

    conn = get_connection(db_path)
    retired: list[dict] = []
    try:
        rows = conn.execute(
            f"SELECT {LEAD_SELECT_COLUMNS} FROM leads "
            "WHERE status='discovered' AND COALESCE(feedback, '')=''"
        ).fetchall()
        for row in rows:
            created = _parse_created_at(row_get(row, "created_at"))
            if created is None or created >= cutoff:
                continue
            lead = lead_row_dict(row)
            meta = json_dict(row_get(row, "source_meta") or "{}")
            scope = meta.get("discovery_scope")
            if not isinstance(scope, dict):
                continue
            if _scope_value(scope, "query") != requested_query or _scope_value(scope, "location") != requested_location:
                continue
            old_portals = {str(p).strip() for p in (scope.get("portals") or []) if str(p).strip()}
            if requested_portals and old_portals and not old_portals.issubset(requested_portals):
                continue
            canonical_id, source_url = _lead_identity(lead)
            if (canonical_id and canonical_id in fresh_ids) or (source_url and source_url in fresh_urls):
                continue
            meta["retired_at"] = datetime.now(UTC).isoformat()
            meta["retire_reason"] = "superseded by a fresh scrape for the same query/location"
            conn.execute(
                "UPDATE leads SET status='discarded', source_meta=? "
                "WHERE job_id=? AND status='discovered' AND COALESCE(feedback, '')=''",
                (json.dumps(meta, ensure_ascii=False), lead["job_id"]),
            )
            conn.execute(
                "INSERT INTO events(job_id,action) VALUES(?,?)",
                (lead["job_id"], "discovery_retired=superseded_by_fresh_scrape"),
            )
            retired.append({"job_id": lead["job_id"], "title": lead.get("title", "")})
        conn.commit()
    finally:
        conn.close()
    return {"retired": len(retired), "items": retired, "policy_days": max(1, int(max_age_days or DISCOVERY_RETIRE_AFTER_DAYS))}


def update_lead_description(job_id: str, description: str, db_path: str = DEFAULT_DB_PATH) -> dict | None:
    """Backfill a lead's description (e.g. lazy full-JD fetch from the corpus detail
    endpoint) and mark it hydrated in source_meta so we never fetch twice."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT source_meta FROM leads WHERE job_id = ?", (job_id,)
        ).fetchone()
        if not row:
            return None
        try:
            meta = json.loads(row_get(row, "source_meta") or "{}")
        except Exception:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta["description_hydrated"] = True
        conn.execute(
            "UPDATE leads SET description = ?, source_meta = ? WHERE job_id = ?",
            (description, json.dumps(meta, ensure_ascii=False), job_id),
        )
        conn.commit()
    finally:
        conn.close()
    lead = get_lead_by_id(job_id, db_path)
    return lead


def get_all_leads(db_path: str = DEFAULT_DB_PATH) -> list:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            f"SELECT {LEAD_SELECT_COLUMNS} FROM leads ORDER BY created_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return [lead_row_dict(row) for row in rows]


def get_feedback_training_examples(limit: int = 300, db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT feedback,platform,company,kind,signal_tags,tech_stack,source_meta,
                   location,urgency,budget,title,description
            FROM leads
            WHERE feedback != ''
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(int(limit or 300), 1000)),),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "feedback": row["feedback"] or "",
            "platform": row["platform"] or "",
            "company": row["company"] or "",
            "kind": row["kind"] or "job",
            "signal_tags": json_list(row["signal_tags"] or "[]"),
            "tech_stack": json_list(row["tech_stack"] or "[]"),
            "source_meta": json_dict(row["source_meta"] or "{}"),
            "location": row["location"] or "",
            "urgency": row["urgency"] or "",
            "budget": row["budget"] or "",
            "title": row["title"] or "",
            "description": row["description"] or "",
        }
        for row in rows
    ]


def get_leads_for_learning(limit: int = 500, db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT {LEAD_SELECT_COLUMNS}
            FROM leads
            WHERE feedback = '' AND status != 'discarded'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(int(limit or 500), 1000)),),
        ).fetchall()
    finally:
        conn.close()
    return [lead_row_dict(row) for row in rows]


def update_learning_scores(updates: list[tuple[str, dict, int]], db_path: str = DEFAULT_DB_PATH) -> None:
    """Batch form of update_learning_score: one connection + one commit for the whole
    feedback recompute (up to 1000 leads) instead of a connect/commit/close per lead.
    The recompute now runs on every feedback click (via the coalescing relearn runner),
    so per-row transaction overhead was the dominant cost."""
    if not updates:
        return
    params = [
        (
            int(ranked.get("signal_score") or 0),
            str(ranked.get("signal_reason") or "")[:700],
            json.dumps(ranked.get("source_meta") or {}, ensure_ascii=False),
            int(ranked.get("base_signal_score") or base_signal_score),
            int(ranked.get("learning_delta") or 0),
            str(ranked.get("learning_reason") or "")[:700],
            # Match score + its idempotency base (feedback re-rank). Fall back to the
            # base only when the caller didn't compute a match score at all (key
            # absent/None) — a legitimately-computed score of exactly 0 (a strong
            # negative feedback delta driving a low-base lead to the floor) must
            # persist as 0, not silently revert to the higher base_score.
            int(ranked["score"] if ranked.get("score") is not None else (ranked.get("base_score") or 0)),
            int(ranked.get("base_score") or 0),
            job_id,
        )
        for job_id, ranked, base_signal_score in updates
    ]
    conn = get_connection(db_path)
    try:
        conn.executemany(
            """
            UPDATE leads
            SET signal_score=?, signal_reason=?, source_meta=?, base_signal_score=?,
                learning_delta=?, learning_reason=?, score=?, base_score=?
            WHERE job_id=?
            """,
            params,
        )
        conn.commit()
    finally:
        conn.close()


def cleanup_text(lead: dict) -> str:
    parts = [
        lead.get("title", ""),
        lead.get("company", ""),
        lead.get("platform", ""),
        lead.get("url", ""),
        lead.get("description", ""),
        lead.get("reason", ""),
        lead.get("signal_reason", ""),
    ]
    text = html.unescape("\n".join(str(part or "") for part in parts))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def looks_like_cleanup_hn_job(text: str) -> bool:
    clean = cleanup_text({"description": text})
    if len(clean) < 80:
        return False
    first_line = clean.splitlines()[0].lower()
    lower = clean.lower()
    role_terms = (
        "engineer", "developer", "software", "backend", "front-end", "frontend",
        "full-stack", "full stack", "devops", "sre", "data", "analyst",
        "designer", "product", "security", "machine learning", "ml", "ai",
        "research", "infrastructure", "platform", "mobile", "ios", "android",
        "qa", "support", "solutions", "sales", "marketing", "operations",
        "founding",
    )
    hiring_terms = (
        "remote", "onsite", "on-site", "hybrid", "visa", "salary", "apply",
        "full-time", "part-time", "contract", "intern", "hiring", "equity",
        "location", "relocation",
    )
    has_role = any(term in lower for term in role_terms)
    has_hiring_signal = any(term in lower for term in hiring_terms)
    explicit_hiring = any(
        phrase in lower
        for phrase in ("we are hiring", "we're hiring", "is hiring", "are hiring", "hiring for")
    )
    return (first_line.count("|") >= 2 and has_role and has_hiring_signal) or (has_role and has_hiring_signal and explicit_hiring)


def lead_cleanup_reasons(lead: dict) -> list[str]:
    text = cleanup_text(lead)
    lower = text.lower()
    title = str(lead.get("title") or "").strip()
    title_lower = title.lower()
    platform = str(lead.get("platform") or "").lower()
    url = str(lead.get("url") or "").lower()
    reasons: list[str] = []

    if not title:
        reasons.append("missing title")
    if not str(lead.get("url") or "").strip():
        reasons.append("missing source url")

    if title_lower.startswith(("ask hn:", "show hn:", "tell hn:", "launch hn:")) and "who is hiring" not in title_lower:
        reasons.append("HN story/commentary title, not a job")

    is_hn = platform in {"hn", "hackernews", "hn_hiring"} or "news.ycombinator.com/item?id=" in url
    if is_hn and not looks_like_cleanup_hn_job(text):
        reasons.append("HN item does not match a job-posting pattern")

    discussion_terms = (
        "maybe ", "i think", "why ", "what ", "how ", "should ", "deprecate",
        "tutorial", "blog post", "newsletter", "podcast", "comment thread",
        "this thread", "discussion", "upvote", "downvote", "karma",
    )
    hiring_terms = (
        "apply", "hiring", "full-time", "part-time", "contract", "salary",
        "equity", "remote", "onsite", "hybrid", "visa", "recruiter",
    )
    if any(term in lower for term in discussion_terms) and not any(term in lower for term in hiring_terms):
        reasons.append("discussion/tutorial content without hiring signal")

    return sorted(set(reasons))


def cleanup_bad_leads(limit: int = 1000, dry_run: bool = False, db_path: str = DEFAULT_DB_PATH) -> dict:
    limit = max(1, min(int(limit or 1000), 5000))
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT {LEAD_SELECT_COLUMNS}
            FROM leads
            WHERE status NOT IN ('approved','applied','interviewing','rejected','accepted','discarded','completed')
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        touched: list[dict] = []
        for row in rows:
            lead = lead_row_dict(row)
            reasons = lead_cleanup_reasons(lead)
            if not reasons:
                continue

            note = "DB cleanup: " + "; ".join(reasons)
            touched.append({
                "job_id": lead["job_id"],
                "title": lead.get("title", ""),
                "company": lead.get("company", ""),
                "platform": lead.get("platform", ""),
                "reasons": reasons,
            })
            if dry_run:
                continue
            conn.execute(
                """
                UPDATE leads
                SET status='discarded', feedback='incorrect_category', feedback_note=?
                WHERE job_id=?
                """,
                (note[:1000], lead["job_id"]),
            )
            conn.execute(
                "INSERT INTO events(job_id,action) VALUES(?,?)",
                (lead["job_id"], note[:1000]),
            )

        conn.commit()
    finally:
        conn.close()
    return {"scanned": len(rows), "discarded": 0 if dry_run else len(touched), "candidates": len(touched), "dry_run": dry_run, "items": touched}


def _score_threshold(db_path: str, key: str, default: int) -> int:
    """Read a 0-100 score-band setting, falling back to the default."""
    try:
        from data.sqlite.settings import get_setting
        raw = get_setting(key, str(default), db_path)
        return max(0, min(int(raw or default), 100))
    except Exception:
        return default


def update_lead_score(
    job_id: str,
    score: int,
    reason: str,
    match_points: list | None = None,
    gaps: list | None = None,
    preserve_status: bool = False,
    scored_by: str = "",
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT kind,status,source_meta FROM leads WHERE job_id=?", (job_id,)).fetchone()
        kind = row["kind"] if row else "job"
        current_status = row["status"] if row and row["status"] else "discovered"
        source_meta = json_dict(row["source_meta"] if row else "{}")
        if scored_by:
            source_meta["scored_by"] = scored_by

        # Two settable bands so the loop surfaces genuine matches in EVERY field: the
        # deterministic rubric scores non-software roles lower, so a single hard 76 bar
        # hid a nurse/lawyer's real matches entirely. "tailoring" = strong fit ready to
        # generate; "matched" = moderate-but-genuine fit, shown for the user to review
        # (off-field junk is already capped to ~15 by the ranker, well below the bar).
        tailor_at = _score_threshold(db_path, "tailor_threshold", 76)
        show_at = min(_score_threshold(db_path, "match_threshold", 45), tailor_at)
        if preserve_status:
            status = current_status
        elif score >= tailor_at:
            status = "matched" if kind == "freelance" else "tailoring"
        elif score >= show_at:
            status = "matched"
        else:
            status = "discarded"

        if preserve_status:
            conn.execute(
                "UPDATE leads SET score=?, reason=?, match_points=?, gaps=?, source_meta=? WHERE job_id=?",
                (score, reason[:500], json_dumps_list(match_points), json_dumps_list(gaps), json.dumps(source_meta, ensure_ascii=False), job_id),
            )
        else:
            conn.execute(
                "UPDATE leads SET status=?, score=?, reason=?, match_points=?, gaps=?, source_meta=? WHERE job_id=?",
                (status, score, reason[:500], json_dumps_list(match_points), json_dumps_list(gaps), json.dumps(source_meta, ensure_ascii=False), job_id),
            )
        conn.execute(
            "INSERT INTO events(job_id,action) VALUES(?,?)",
            (job_id, f"score={score} status={'preserved:' if preserve_status else ''}{status}"),
        )
        conn.commit()
    finally:
        conn.close()


def record_event(job_id: str, action: str, db_path: str = DEFAULT_DB_PATH) -> None:
    """Append an activity-log line without changing the lead's status.

    Every other writer here appends to `events` as a side effect of a status change. The apply flow
    needs the opposite: it records what happened (a portal opened, the extension filled 9 fields)
    while deliberately *not* advancing the status, because opening a form is not applying.
    """
    conn = get_connection(db_path)
    try:
        conn.execute("INSERT INTO events(job_id,action) VALUES(?,?)", (job_id, action[:200]))
        conn.commit()
    finally:
        conn.close()


def save_prepared_fill_context(job_id: str, context: dict, db_path: str = DEFAULT_DB_PATH) -> None:
    """Persist the extension handoff for a locally-created/manual lead.

    Corpus jobs keep this state in Postgres. Manual jobs live only in the gateway's
    SQLite store, so keeping their context in source_meta makes the handoff survive
    a gateway or browser restart without adding a second applications table.
    """
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT source_meta FROM leads WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise LookupError(f"lead {job_id!r} not found")
        source_meta = json_dict(row["source_meta"] or "{}")
        source_meta["prepared_fill_context"] = context
        conn.execute(
            "UPDATE leads SET source_meta=? WHERE job_id=?",
            (json.dumps(source_meta, ensure_ascii=False), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_lead_status(job_id: str, status: str, db_path: str = DEFAULT_DB_PATH) -> None:
    valid = {
        "discovered", "evaluating", "tailoring", "approved",
        "applied", "interviewing", "rejected", "accepted", "discarded",
        "matched", "bidding", "proposal_sent", "awarded", "completed",
    }
    if status not in valid:
        raise ValueError(f"Invalid status: {status}")
    conn = get_connection(db_path)
    try:
        cur = conn.execute("UPDATE leads SET status=? WHERE job_id=?", (status, job_id))
        if getattr(cur, "rowcount", 0) == 0:
            raise LookupError(f"lead {job_id!r} not found")
        conn.execute(
            "INSERT INTO events(job_id,action) VALUES(?,?)",
            (job_id, f"status_changed={status}"),
        )
        conn.commit()
    finally:
        conn.close()


def save_asset_package(
    job_id: str,
    resume_path: str,
    cover_letter_path: str = "",
    selected_projects: list | None = None,
    keyword_coverage: dict | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    conn = get_connection(db_path)
    try:
        meta_row = conn.execute("SELECT source_meta FROM leads WHERE job_id=?", (job_id,)).fetchone()
        source_meta = json_dict(meta_row["source_meta"] if meta_row else "{}")
        if keyword_coverage:
            source_meta["keyword_coverage"] = keyword_coverage
        conn.execute(
            "UPDATE leads SET status='approved', asset_path=?, cover_letter_path=?, selected_projects=?, source_meta=? WHERE job_id=?",
            (
                resume_path,
                cover_letter_path,
                json.dumps(selected_projects or []),
                json.dumps(source_meta, ensure_ascii=False),
                job_id,
            ),
        )
        conn.execute(
            "INSERT INTO events(job_id,action) VALUES(?,?)",
            (job_id, f"assets=resume:{resume_path} cover:{cover_letter_path}"),
        )
        conn.commit()
    finally:
        conn.close()


def get_resume_version(job_id: str, db_path: str = DEFAULT_DB_PATH) -> int:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT resume_version FROM leads WHERE job_id = ?", (job_id,)).fetchone()
    finally:
        conn.close()
    return int(row["resume_version"] or 0) if row else 0


def save_generated_asset_version(
    job_id: str,
    resume_path: str,
    cover_letter_path: str,
    resume_version: int,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            UPDATE leads
            SET asset_path = ?, cover_letter_path = ?, resume_version = ?
            WHERE job_id = ?
            """,
            (resume_path, cover_letter_path, int(resume_version or 0), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def save_contact_lookup(job_id: str, contact_lookup: dict | None, db_path: str = DEFAULT_DB_PATH) -> None:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT source_meta FROM leads WHERE job_id=?", (job_id,)).fetchone()
        source_meta = json_dict(row["source_meta"] if row else "{}")
        source_meta["contact_lookup"] = contact_lookup or {"status": "empty", "contacts": []}
        conn.execute(
            "UPDATE leads SET source_meta=? WHERE job_id=?",
            (json.dumps(source_meta, ensure_ascii=False), job_id),
        )
        conn.execute(
            "INSERT INTO events(job_id,action) VALUES(?,?)",
            (job_id, f"contact_lookup={source_meta['contact_lookup'].get('status', 'unknown')}"),
        )
        conn.commit()
    finally:
        conn.close()


def update_outreach_fields(job_id: str, fields: dict[str, str], db_path: str = DEFAULT_DB_PATH) -> None:
    allowed = {"outreach_reply", "outreach_dm", "outreach_email", "proposal_draft"}
    payload = {key: str(value or "") for key, value in fields.items() if key in allowed}
    if not payload:
        return
    conn = get_connection(db_path)
    try:
        sets = ", ".join(f"{key}=?" for key in payload)
        vals = [*payload.values(), job_id]
        conn.execute(f"UPDATE leads SET {sets} WHERE job_id=?", vals)
        conn.commit()
    finally:
        conn.close()


def mark_applied(job_id: str, db_path: str = DEFAULT_DB_PATH) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute("UPDATE leads SET status='applied' WHERE job_id=?", (job_id,))
        conn.execute(
            "INSERT INTO events(job_id,action) VALUES(?,?)",
            (job_id, "submitted application"),
        )
        conn.commit()
    finally:
        conn.close()


def save_lead_feedback(
    job_id: str,
    feedback: str,
    note: str = "",
    contacted_at: str = "",
    followup_due_at: str = "",
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    valid = {
        "good", "trash", "too_generic", "not_ai",
        "not_freelance", "already_contacted",
        "relevant", "not_relevant", "duplicate",
        "low_quality", "incorrect_category",
    }
    if feedback not in valid:
        raise ValueError(f"Invalid feedback: {feedback}")

    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT kind,status FROM leads WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return {}

        kind = row["kind"] or "job"
        status = row["status"] or "discovered"
        new_status = status
        if feedback in {
            "trash", "too_generic", "not_ai", "not_freelance",
            "not_relevant", "duplicate", "low_quality", "incorrect_category",
        }:
            new_status = "discarded"
        elif feedback == "already_contacted":
            new_status = "proposal_sent" if kind == "freelance" else "applied"

        conn.execute(
            "UPDATE leads SET feedback=?, feedback_note=?, status=?, last_contacted_at=COALESCE(NULLIF(?, ''), last_contacted_at), followup_due_at=COALESCE(NULLIF(?, ''), followup_due_at) WHERE job_id=?",
            (feedback, note or "", new_status, contacted_at, followup_due_at, job_id),
        )
        conn.execute(
            "INSERT INTO events(job_id,action) VALUES(?,?)",
            (job_id, f"feedback={feedback}"),
        )
        conn.commit()
    finally:
        conn.close()
    return get_lead_by_id(job_id, db_path)


def update_lead_followup(
    job_id: str,
    contacted_at: str,
    followup_due_at: str,
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT 1 FROM leads WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return {}
        conn.execute(
            "UPDATE leads SET followup_due_at=?, last_contacted_at=COALESCE(NULLIF(last_contacted_at, ''), ?) WHERE job_id=?",
            (followup_due_at, contacted_at, job_id),
        )
        conn.execute(
            "INSERT INTO events(job_id,action) VALUES(?,?)",
            (job_id, f"followup_due={followup_due_at}"),
        )
        conn.commit()
    finally:
        conn.close()
    return get_lead_by_id(job_id, db_path)


def get_lead_by_id(job_id: str, db_path: str = DEFAULT_DB_PATH) -> dict:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            f"SELECT {LEAD_SELECT_COLUMNS} FROM leads WHERE job_id=?",
            (job_id,),
        ).fetchone()
        events = conn.execute(
            "SELECT action, ts FROM events WHERE job_id=? ORDER BY ts DESC LIMIT 20",
            (job_id,),
        ).fetchall()
    finally:
        conn.close()
    if not row:
        return {}
    lead = lead_row_dict(row)
    lead["events"] = [{"action": event["action"], "ts": event["ts"]} for event in events]
    return lead


def get_lead_for_fire_base(job_id: str, db_path: str = DEFAULT_DB_PATH) -> tuple[dict, str]:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT job_id,title,company,url,platform,status,score,reason,match_points,asset_path,description,gaps,cover_letter_path,selected_projects,kind,budget FROM leads WHERE job_id=?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {}, ""

    path = row["asset_path"] or ""
    cover_path = row["cover_letter_path"] or ""
    lead = {
        "job_id": row["job_id"], "title": row["title"], "company": row["company"], "url": row["url"],
        "platform": row["platform"], "status": row["status"], "score": row["score"] or 0,
        "reason": row["reason"] or "",
        "match_points": json_list(row["match_points"] or "[]"),
        "asset": path,
        "resume_asset": path,
        "asset_path": path,
        "description": row["description"] or "",
        "gaps": json_list(row["gaps"] or "[]"),
        "cover_letter_asset": cover_path,
        "cover_letter_path": cover_path,
        "selected_projects": json_list(row["selected_projects"] or "[]"),
        "kind": row["kind"] or "job",
        "budget": row["budget"] or "",
    }
    return lead, path


def get_lead_for_fire(job_id: str, db_path: str = DEFAULT_DB_PATH) -> tuple[dict, str]:
    return get_lead_for_fire_base(job_id, db_path=db_path)


def delete_lead(job_id: str, db_path: str = DEFAULT_DB_PATH) -> None:
    conn = get_connection(db_path)
    try:
        cur = conn.execute("DELETE FROM leads WHERE job_id=?", (job_id,))
        if getattr(cur, "rowcount", 0) == 0:
            raise LookupError(f"lead {job_id!r} not found")
        conn.execute("DELETE FROM events WHERE job_id=?", (job_id,))
        conn.commit()
    finally:
        conn.close()


def get_due_followups(limit: int = 25, now: str = "", db_path: str = DEFAULT_DB_PATH) -> list:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT {LEAD_SELECT_COLUMNS}
            FROM leads
            WHERE followup_due_at != '' AND followup_due_at <= ? AND status != 'discarded'
            ORDER BY followup_due_at ASC
            LIMIT ?
            """,
            (now, max(1, min(int(limit or 25), 100))),
        ).fetchall()
    finally:
        conn.close()
    return [lead_row_dict(row) for row in rows]


def get_discovered_leads(db_path: str = DEFAULT_DB_PATH) -> list:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT job_id,title,company,url,platform,description FROM leads WHERE status='discovered' AND COALESCE(NULLIF(kind, ''), 'job')='job'"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "job_id": row["job_id"],
            "title": row["title"],
            "company": row["company"],
            "url": row["url"],
            "platform": row["platform"],
            "description": row["description"] or "",
        }
        for row in rows
    ]
