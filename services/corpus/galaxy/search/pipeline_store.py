"""Phase 6 pipeline: applications tracker, saved searches, insights (docs/04 §5, docs/10)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import text

from galaxy.db.engine import get_sessionmaker
from galaxy.models.enums import ApplicationStatus

# --- applications tracker ---------------------------------------------------

_CANONICAL_STATES = {s.value for s in ApplicationStatus}


async def list_applications(user_id: UUID) -> list[dict]:
    """The pipeline/kanban: every application joined to its canonical job."""
    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT a.id, a.status, a.fit_score, a.ats_score, a.resume_pdf_ref, "
                    "a.applied_url, a.created_at, a.updated_at, "
                    "cj.canonical_job_id, cj.title, cj.company, cj.primary_url, cj.location "
                    "FROM applications a JOIN canonical_jobs cj USING (canonical_job_id) "
                    "WHERE a.user_id = :u ORDER BY a.updated_at DESC NULLS LAST, a.created_at DESC"
                ),
                {"u": str(user_id)},
            )
        ).mappings().all()
    return [dict(r) for r in rows]


async def set_status(user_id: UUID, application_id: UUID, status: str, note: str | None) -> bool:
    """Update an application's status (canonical states only). Returns False if unknown state."""
    if status not in _CANONICAL_STATES:
        return False
    sm = get_sessionmaker()
    async with sm() as s:
        res = await s.execute(
            text(
                "UPDATE applications SET status = :st, updated_at = now() "
                "WHERE id = :id AND user_id = :u"
            ),
            {"st": status, "id": str(application_id), "u": str(user_id)},
        )
        await s.commit()
    _ = note  # notes column reserved; tracker note wiring is a later refinement
    return res.rowcount > 0


def _host(u: str | None) -> str:
    try:
        return (urlparse(u or "").netloc or "").lower()
    except ValueError:
        return ""


def _as_dict(v):
    if isinstance(v, dict):
        return v
    return json.loads(v) if v else None


async def get_fill_context(user_id: UUID, url: str | None = None) -> dict | None:
    """The fill-only extension's per-job payload (docs/11 §W1.5).

    Prefer the application whose apply-URL host matches `url` (the tab the extension is on);
    otherwise the most recently prepared one. None when nothing has been prepared.
    """
    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT canonical_job_id, fill_context, applied_url, updated_at FROM applications "
                    "WHERE user_id = :u AND fill_context IS NOT NULL "
                    "ORDER BY updated_at DESC NULLS LAST, created_at DESC"
                ),
                {"u": str(user_id)},
            )
        ).mappings().all()
    if not rows:
        return None
    host = _host(url) if url else ""
    if host:
        for r in rows:
            fc = _as_dict(r["fill_context"]) or {}
            apply_url = r["applied_url"] or fc.get("apply_url")
            if _host(apply_url) == host:
                return fc
        # Supplying a URL is an explicit request for that tab. Falling back to the
        # newest unrelated application can put another company's answers into a
        # form, which is materially worse than asking the user to prepare this job.
        return None
    return _as_dict(rows[0]["fill_context"])


async def pipeline_stats(user_id: UUID) -> dict:
    """Funnel counts by status + totals (docs/10 Phase 6)."""
    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            await s.execute(
                text("SELECT status, count(*) AS n FROM applications WHERE user_id = :u GROUP BY status"),
                {"u": str(user_id)},
            )
        ).all()
    by_status = {r[0]: r[1] for r in rows}
    return {"by_status": by_status, "total": sum(by_status.values())}


# --- saved searches ---------------------------------------------------------


async def save_search(user_id: UUID, name: str, params: dict) -> dict:
    sm = get_sessionmaker()
    async with sm() as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO saved_searches (user_id, name, params) "
                    "VALUES (:u, :n, CAST(:p AS jsonb)) "
                    "ON CONFLICT (user_id, name) DO UPDATE SET params = EXCLUDED.params "
                    "RETURNING id, name, created_at"
                ),
                {"u": str(user_id), "n": name, "p": json.dumps(params)},
            )
        ).mappings().one()
        await s.commit()
    return dict(row)


async def list_searches(user_id: UUID) -> list[dict]:
    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, name, params, last_run_at, created_at FROM saved_searches "
                    "WHERE user_id = :u ORDER BY created_at DESC"
                ),
                {"u": str(user_id)},
            )
        ).mappings().all()
    return [dict(r) for r in rows]


async def get_search(user_id: UUID, search_id: UUID) -> dict | None:
    sm = get_sessionmaker()
    async with sm() as s:
        row = (
            await s.execute(
                text("SELECT id, name, params, last_run_at FROM saved_searches WHERE id=:id AND user_id=:u"),
                {"id": str(search_id), "u": str(user_id)},
            )
        ).mappings().first()
    return dict(row) if row else None


async def mark_search_run(user_id: UUID, search_id: UUID, at: datetime | None = None) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(
            text("UPDATE saved_searches SET last_run_at = :t WHERE id = :id AND user_id = :u"),
            {"t": at or datetime.now(UTC), "id": str(search_id), "u": str(user_id)},
        )
        await s.commit()


# --- insights ---------------------------------------------------------------


async def reposts(days: int = 90, min_count: int = 2, limit: int = 50) -> list[dict]:
    """Roles re-listed 2+ times in a window — churny employer / evergreen req (docs/02 §4)."""
    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT cj.company, cj.title, "
                    "count(*) AS listings, min(so.first_observed_at) AS first_seen, "
                    "max(so.last_observed_at) AS last_seen "
                    "FROM source_observations so JOIN canonical_jobs cj USING (canonical_job_id) "
                    "WHERE so.first_observed_at > now() - make_interval(days => :days) "
                    "GROUP BY cj.company, cj.title "
                    "HAVING count(*) >= :minc ORDER BY listings DESC LIMIT :lim"
                ),
                {"days": days, "minc": min_count, "lim": limit},
            )
        ).mappings().all()
    return [dict(r) for r in rows]


async def skill_gap(user_id: UUID, limit: int = 20) -> list[dict]:
    """Most-frequent skills the user's tailored jobs asked for but their profile lacks.

    Aggregates jd_keywords of the user's applications minus the profile's skills → "skills to
    learn" (docs/04 §4).
    """
    from galaxy.search.profiles import ProfileStore

    profile = await ProfileStore().get(user_id)
    have = {s.name.lower() for s in profile.skills} if profile else set()

    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT lower(k) AS skill, count(*) AS n "
                    "FROM applications a JOIN canonical_jobs cj USING (canonical_job_id), "
                    "     jsonb_array_elements_text(cj.jd_keywords) k "
                    "WHERE a.user_id = :u GROUP BY lower(k) ORDER BY n DESC"
                ),
                {"u": str(user_id)},
            )
        ).all()
    gaps = [{"skill": r[0], "count": r[1]} for r in rows if r[0] not in have]
    return gaps[:limit]
