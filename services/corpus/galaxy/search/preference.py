"""Turn recorded search feedback into an effect on results.

`search_feedback` has been written to since the schema's first migration, and until now nothing
read it. `record_feedback` even claimed "stage-gated effect is applied elsewhere" — there was no
elsewhere. Every thumbs-down a user gave went into a table and changed nothing.

This is the read side. It is deliberately conservative, because a preference layer that
over-generalises is worse than none: it silently shrinks the corpus the user can see, and they
cannot tell why a job stopped appearing.

## What is inferred, and what is not

**Suppression** comes only from `not_relevant`, and only for the exact job it was given on. That
signal is unambiguous — the user looked at that posting and said no — so one is enough.

**Seniority bounds** come from `too_senior` / `too_junior`, and need `INFER_AT` consistent signals
with no recent contradiction. One "too senior" is as likely to be a mis-titled posting as a
statement about the user, so acting on it immediately would be reading far too much into a click.

**Company-level judgements are not inferred at all.** Three rejected postings at a large employer
says something about those three postings, not the company; auto-blacklisting on that basis would
hide roles the user never rejected and never asked to hide. Company exclusion stays a thing the
user does explicitly, through the existing blacklist.

## Why it expires

Signals older than `WINDOW_DAYS` are ignored. Seniority preference is a fact about a moment in
someone's career, and a job search that permanently remembers "no senior roles" from two years ago
is actively harmful to someone who has since been promoted.

## Why it reports itself

`Preference.describe()` exists so the caller can tell the user what was applied. A filter the user
cannot see is the same failure as an endpoint that returns the wrong thing with a 200 — it looks
like the system simply has less to offer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text

from galaxy.db.engine import get_sessionmaker
from galaxy.models.enums import Seniority

#: Feedback older than this is ignored entirely. Careers move.
WINDOW_DAYS = 180

#: Consistent signals needed before a seniority bound is inferred.
INFER_AT = 3

#: Upper bound on suppressed ids sent to SQL. Protects the query from an unbounded IN-list if a
#: user rejects thousands of jobs; the most recent rejections are the ones that matter.
MAX_SUPPRESSED = 500

NOT_RELEVANT = "not_relevant"
TOO_SENIOR = "too_senior"
TOO_JUNIOR = "too_junior"
GOOD = "good"


@dataclass(frozen=True)
class Preference:
    """What a user's feedback implies for their next search."""

    #: Jobs to exclude outright — each was explicitly marked not relevant.
    suppressed_job_ids: frozenset[str] = frozenset()
    #: Inferred ceiling: exclude anything more senior than this.
    max_seniority: Seniority | None = None
    #: Inferred floor: exclude anything more junior than this.
    min_seniority: Seniority | None = None
    #: Raw counts, kept for explanation and for tests that assert *why* a bound was inferred.
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return bool(self.suppressed_job_ids or self.max_seniority or self.min_seniority)

    def describe(self) -> list[str]:
        """Human-readable statements of every effect applied, for showing back to the user."""
        out: list[str] = []
        if self.suppressed_job_ids:
            n = len(self.suppressed_job_ids)
            out.append(f"Hiding {n} job{'s' if n != 1 else ''} you marked not relevant")
        if self.max_seniority:
            out.append(f"Excluding roles above {self.max_seniority.value}, based on your feedback")
        if self.min_seniority:
            out.append(f"Excluding roles below {self.min_seniority.value}, based on your feedback")
        return out

    def as_dict(self) -> dict:
        return {
            "active": self.is_active,
            "suppressed": len(self.suppressed_job_ids),
            "max_seniority": self.max_seniority.value if self.max_seniority else None,
            "min_seniority": self.min_seniority.value if self.min_seniority else None,
            "reasons": self.describe(),
        }


def infer_bounds(counts: dict[str, int]) -> tuple[Seniority | None, Seniority | None]:
    """Derive seniority bounds from signal counts.

    Contradiction is treated as "no opinion" rather than resolved by majority. A user who has said
    both too-senior and too-junior is describing a band this crude two-signal scheme cannot
    represent, and guessing which way they lean would filter out the middle they actually want.
    """
    too_senior = counts.get(TOO_SENIOR, 0)
    too_junior = counts.get(TOO_JUNIOR, 0)

    if too_senior >= INFER_AT and too_junior >= INFER_AT:
        return None, None
    if too_senior >= INFER_AT:
        # They keep seeing roles above their level; cap at mid so senior/lead/exec drop out.
        return Seniority.MID, None
    if too_junior >= INFER_AT:
        return None, Seniority.MID
    return None, None


async def load_preference(user_id: UUID | None) -> Preference:
    """Read a user's recent feedback and reduce it to an effect.

    Returns an inert Preference when there is no user or no feedback, so callers need no special
    case — an anonymous search behaves exactly as it did before this module existed.
    """
    if user_id is None:
        return Preference()

    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT canonical_job_id, signal FROM search_feedback "
                    "WHERE user_id = :u AND created_at >= now() - make_interval(days => :days) "
                    "ORDER BY created_at DESC"
                ),
                {"u": str(user_id), "days": WINDOW_DAYS},
            )
        ).mappings().all()

    counts: dict[str, int] = {}
    suppressed: list[str] = []
    for row in rows:
        signal = row["signal"]
        counts[signal] = counts.get(signal, 0) + 1
        # Rows are newest-first, so truncating keeps the most recent rejections.
        if signal == NOT_RELEVANT and len(suppressed) < MAX_SUPPRESSED:
            suppressed.append(row["canonical_job_id"])

    max_sen, min_sen = infer_bounds(counts)
    return Preference(
        suppressed_job_ids=frozenset(suppressed),
        max_seniority=max_sen,
        min_seniority=min_sen,
        counts=counts,
    )
