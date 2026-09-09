"""Enumerations shared across the system."""

from __future__ import annotations

import re
from enum import StrEnum


class Site(StrEnum):
    """The provenance key of every observation (docs/02 §2).

    **Open enum.** The connector set is career-ops' provider modules and the members below are
    only the historically-embedded subset of their ids; the vendored tree ships ~80 provider ids
    (`services/scraper-node/vendor/career-ops-providers/`), most of which this enum has never
    heard of — `arbeitnow`, `hackernews`, `jibeapply`…

    `_missing_` therefore admits any well-formed id instead of raising, so a provider posting
    as `jobsoid` round-trips through the model layer unchanged. This is safe because `site` is
    stored as TEXT (not a Postgres enum), so no migration is implied — see ARCHITECTURE.md D6.
    It is also why adding a provider upstream requires zero changes here: adding members would
    only create a second list that can drift from the vendor tree.

    Admission is not unconditional: an id must look like an id. Anything else is a bug in a
    worker, and silently coining a member for it would bury the bug in the corpus.
    """

    # ATS boards (also career-ops provider ids)
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    WORKDAY = "workday"
    SMARTRECRUITERS = "smartrecruiters"
    RECRUITEE = "recruitee"
    WORKABLE = "workable"
    # Boards (Strategy A)
    INDEED = "indeed"
    LINKEDIN = "linkedin"
    ZIPRECRUITER = "ziprecruiter"
    GLASSDOOR = "glassdoor"
    GOOGLE = "google"
    NAUKRI = "naukri"
    BAYT = "bayt"
    BDJOBS = "bdjobs"
    # Company-specific career APIs (own websites)
    AMAZON = "amazon"
    GOOGLE_CAREERS = "google_careers"
    MICROSOFT = "microsoft"
    NVIDIA = "nvidia"
    ZOOM = "zoom"
    # Feeds (Strategy C)
    REMOTEOK = "remoteok"
    REMOTIVE = "remotive"
    JOBICY = "jobicy"
    WEWORKREMOTELY = "weworkremotely"
    HACKERNEWS = "hackernews"

    @classmethod
    def _missing_(cls, value: object) -> Site | None:
        """Admit a registry-shaped site id that has no Python adapter here.

        Accepts alphanumerics plus `_`/`-`, which is exactly what `packages/contract`'s
        `portalKey` can emit. Returns None (→ ValueError) for anything else, so genuinely
        malformed input still fails loudly rather than polluting the corpus with a member named
        after someone's stack trace.

        Matching is **case-insensitive**, normalizing to the canonical lowercase value. That is
        deliberate on two counts: `Site("GREENHOUSE") is Site.GREENHOUSE` rather than a 500, and
        two workers sending `Foo` and `foo` converge on one member instead of splitting the same
        portal in two. The registry always emits lowercase; this only forgives everything else.
        """
        if not isinstance(value, str):
            return None
        candidate = value.strip().lower()
        if not candidate or len(candidate) > 64:
            return None
        if not _SITE_ID_RE.fullmatch(candidate):
            return None
        # Reuse an already-coined pseudo-member so identity comparisons stay stable across calls.
        existing = cls._value2member_map_.get(candidate)
        if existing is not None:
            return existing  # type: ignore[return-value]
        member = str.__new__(cls, candidate)
        member._name_ = candidate.upper().replace("-", "_")
        member._value_ = candidate
        # Register so `Site(candidate) is Site(candidate)` holds and iteration stays coherent.
        cls._value2member_map_[candidate] = member
        return member


_SITE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")


class SourceKind(StrEnum):
    BOARD = "board"
    ATS = "ats"
    FEED = "feed"


class JobStatus(StrEnum):
    OPEN = "open"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class Seniority(StrEnum):
    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    EXEC = "exec"

    @property
    def rank(self) -> int:
        order = ["intern", "junior", "mid", "senior", "lead", "exec"]
        return order.index(self.value)


class OnsitePolicy(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class CompInterval(StrEnum):
    YEARLY = "yearly"
    MONTHLY = "monthly"
    WEEKLY = "weekly"
    DAILY = "daily"
    HOURLY = "hourly"


class SalarySource(StrEnum):
    DIRECT = "direct_data"
    DESCRIPTION = "description"


class Legitimacy(StrEnum):
    """Tri-state; `uncertain` is the safe default (docs/03 §5)."""

    LEGIT = "legit"
    UNCERTAIN = "uncertain"
    SCAM = "scam"


class ApplicationStatus(StrEnum):
    SAVED = "Saved"
    EVALUATED = "Evaluated"
    TAILORED = "Tailored"
    APPLIED = "Applied"
    RESPONDED = "Responded"
    INTERVIEW = "Interview"
    OFFER = "Offer"
    REJECTED = "Rejected"
    DISCARDED = "Discarded"


# Static source-trust ordering for the merge resolver (docs/03 §4).
# Higher = more trusted for factual fields. ATS feeds beat boards.
SOURCE_TRUST: dict[Site, int] = {
    Site.GREENHOUSE: 100,
    Site.LEVER: 100,
    Site.ASHBY: 100,
    Site.WORKDAY: 95,
    Site.SMARTRECRUITERS: 95,
    Site.RECRUITEE: 90,
    Site.WORKABLE: 90,
    # company-specific career APIs are the employer's own source → top trust
    Site.AMAZON: 100,
    Site.GOOGLE_CAREERS: 100,
    Site.MICROSOFT: 100,
    Site.NVIDIA: 100,
    Site.ZOOM: 100,
    Site.INDEED: 70,
    Site.GLASSDOOR: 60,
    Site.LINKEDIN: 55,
    Site.ZIPRECRUITER: 55,
    Site.NAUKRI: 55,
    Site.BAYT: 50,
    Site.BDJOBS: 50,
    Site.GOOGLE: 50,
    Site.REMOTEOK: 45,
    Site.REMOTIVE: 45,
    Site.JOBICY: 45,
    Site.WEWORKREMOTELY: 45,
    Site.HACKERNEWS: 40,
}


def source_trust(site: Site) -> int:
    """Merge-resolver trust for a site's factual fields. Higher wins (docs/03 §4).

    The 30 default now covers the ~1800 connectors hosted by the Node worker, which sits below
    every explicitly-ranked source. That is the right *safe* default — an unvetted long-tail
    board should not overwrite Greenhouse's idea of a job's title — but it is crude: a
    career-ops ATS connector is employer-direct and deserves to outrank an aggregator board it
    currently ties with.

    Refining this needs each connector's category plumbed through from the registry, which is
    tracked separately. Until then, unknown means least-trusted.
    """
    return SOURCE_TRUST.get(site, 30)
