"""Job level extraction — seniority from how the market actually expresses it.

## Why this replaced years-of-experience as the primary signal

`min_years_experience` was the filter's main seniority input and it is the wrong choice. Most
postings never state years, and the ones that do are inconsistent; meanwhile the *level* is almost
always right there in the title — "SDE II", "L5", "Senior", "Principal Architect", "Werkstudent".
Industry ladders make the mapping explicit but noisy: published guidance puts Google L4 at "2-4
years" in one place and "5+ years" in another, and levelling guides say outright that years do not
determine level. So years are treated here as *weak, corroborating* evidence and levels as primary,
with every inference carrying a band rather than a point value.

## The bug this was built to fix

The previous extractor scanned the whole job description for level words. Measured on a real
4,225-job corpus, that produced:

  * **741 of 1,343 `lead` labels (55%)** from description text alone, on titles like "Relationship
    Manager, Scale" and "Solutions Engineer - LATAM" — the description merely contained "lead" as a
    verb, or "architect"/"staff" in passing.
  * **211 of 399 `exec` labels (53%)** the same way, from descriptions mentioning a director.
  * **zero `mid` labels ever**, because no pattern could produce one.
  * `II`/`III` mapped to `senior`, when "Engineer II" is mid-level across every ladder that uses it.

About 23% of the corpus was mislabelled, which made `max_seniority` worse than useless: a
junior-only search actively excluded 741 jobs wrongly marked lead.

So: **the title is authoritative**. A description contributes only through explicitly anchored
phrasing ("this is a senior-level role", "hiring at the L5 level"), never through bare word presence.

## Resolution is by specificity, not list order

"Senior Staff Engineer" is staff, not senior. "Associate Director" is a director, not an associate.
The old first-match-wins ordering got both wrong. Every matching signal is collected, then the most
*specific* one wins, with ladder codes and explicit words outranking bare numerals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

from galaxy.models.enums import Seniority


class Confidence(IntEnum):
    """How much a signal should be trusted. Higher wins ties."""

    WEAK = 1      # bare numeral, or a years figure implying a band
    MEDIUM = 2    # an anchored phrase in the description
    STRONG = 3    # an explicit level word or ladder code in the title


@dataclass(frozen=True)
class LevelSignal:
    seniority: Seniority
    confidence: Confidence
    source: str      # "title" | "description" | "years"
    evidence: str    # the text that matched, for auditing a wrong call


@dataclass
class LevelVerdict:
    seniority: Seniority | None = None
    confidence: Confidence | None = None
    source: str = ""
    evidence: str = ""
    #: Implied years range for the resolved level, as (min, max); max None means open-ended.
    years_band: tuple[int, int | None] | None = None
    #: Every signal found, so a surprising verdict can be traced.
    signals: tuple[LevelSignal, ...] = ()

    def as_dict(self) -> dict:
        return {
            "seniority": str(self.seniority) if self.seniority else None,
            "confidence": int(self.confidence) if self.confidence else None,
            "source": self.source,
            "evidence": self.evidence,
            "years_band": list(self.years_band) if self.years_band else None,
        }


# ── level ↔ years ─────────────────────────────────────────────────────────────
#
# Bands, not points, and deliberately overlapping. Drawn from published ladder guidance (Google
# L3-L6, Meta E3-E6, Amazon SDE I-III, generic IC1-IC7), which disagrees with itself at the edges —
# so a band that admits the disagreement is more honest than a single number that hides it.
YEARS_BAND: dict[Seniority, tuple[int, int | None]] = {
    Seniority.INTERN: (0, 1),
    Seniority.JUNIOR: (0, 2),
    Seniority.MID: (2, 5),
    Seniority.SENIOR: (5, 9),
    Seniority.LEAD: (8, None),
    Seniority.EXEC: (10, None),
}


def years_to_seniority(years: int) -> Seniority:
    """The level a stated years figure implies.

    Used when a posting gives years but no level word, so a level filter still has something to act
    on. Bands are half-open upward so the mapping is total.
    """
    if years <= 1:
        return Seniority.JUNIOR
    if years < 5:
        return Seniority.MID
    if years < 9:
        return Seniority.SENIOR
    return Seniority.LEAD


def seniority_to_years(level: Seniority) -> tuple[int, int | None]:
    return YEARS_BAND.get(level, (0, None))


# ── title patterns ────────────────────────────────────────────────────────────

# Ladder codes. Anchored to a word boundary so "L5" matches but "HTML5" and "SQL5" do not.
_LADDER = [
    # Google-style L3-L9 and Meta-style E3-E9 share a scale: L3/E3 entry, L5/E5 senior, L6/E6 staff.
    (re.compile(r"\b[LE]\s?([3-9])\b"), "ladder_le"),
    # Generic IC ladders: IC1-2 junior, IC3 mid, IC4 senior, IC5 staff, IC6+ principal/distinguished.
    (re.compile(r"\bIC\s?([1-9])\b", re.I), "ladder_ic"),
    # Apple-style ICT3-ICT6 and generic T-levels.
    (re.compile(r"\bICT\s?([1-9])\b", re.I), "ladder_ict"),
]

_LE_TO_LEVEL = {3: Seniority.JUNIOR, 4: Seniority.MID, 5: Seniority.SENIOR,
                6: Seniority.LEAD, 7: Seniority.LEAD, 8: Seniority.EXEC, 9: Seniority.EXEC}
_IC_TO_LEVEL = {1: Seniority.JUNIOR, 2: Seniority.JUNIOR, 3: Seniority.MID, 4: Seniority.SENIOR,
                5: Seniority.LEAD, 6: Seniority.LEAD, 7: Seniority.EXEC, 8: Seniority.EXEC, 9: Seniority.EXEC}
# Apple's ICT scale tracks the generic IC one closely enough to share a mapping.
_ICT_TO_LEVEL = dict(_IC_TO_LEVEL)

# Amazon-style SDE/SWE ladders: I entry, II mid, III senior.
_ROLE_LADDER = re.compile(r"\b(?:SDE|SWE|MTS|SDET)\s?(I{1,3}|[123])\b", re.I)
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "1": 1, "2": 2, "3": 3}
_ROLE_LADDER_TO_LEVEL = {1: Seniority.JUNIOR, 2: Seniority.MID, 3: Seniority.SENIOR}

# Bare trailing numerals: "Engineer II", "Developer 2". Weak on purpose — a numeral can be a team
# name or a product version, and it is the signal most often wrong.
_BARE_NUMERAL = re.compile(r"\b(I{1,3}|[123])\b(?!\s*\w)")

# Explicit level words. Ordered high-to-low so the most senior match wins for a title like
# "Senior Staff Engineer"; each is checked independently and the resolver picks by rank, not order.
_WORD_SIGNALS: list[tuple[Seniority, re.Pattern[str]]] = [
    (Seniority.EXEC, re.compile(
        r"\b(chief|c[tefoi]o|cxo|vice[\s-]president|\bvp\b|head\s+of|director|"
        r"partner|general\s+manager)\b", re.I)),
    (Seniority.LEAD, re.compile(
        r"\b(principal|staff|distinguished|fellow|lead|tech\s+lead|"
        r"team\s+lead|architect)\b", re.I)),
    (Seniority.SENIOR, re.compile(r"\b(senior|snr\.?|sr\.?)\b", re.I)),
    (Seniority.MID, re.compile(r"\b(mid[\s-]?level|mid|intermediate|regular)\b", re.I)),
    (Seniority.JUNIOR, re.compile(
        r"\b(junior|jnr\.?|jr\.?|entry[\s-]?level|new\s+grad(?:uate)?|graduate|grad\s+scheme|"
        r"fresher|associate|apprentice|apprenticeship|einsteiger|nachwuchs)\b", re.I)),
    (Seniority.INTERN, re.compile(
        # "Working student" is the English rendering of Werkstudent and appears on the same postings;
        # spotted in the corpus after the first re-derivation left those titles unlabelled.
        r"\b(intern|internship|trainee|co-?op|placement|working\s+student|werkstudent|praktikum|"
        r"praktikant|stage|stagiaire|becario|studentische)\b", re.I)),
]

# "Associate" is genuinely ambiguous: an Associate Engineer is junior, an Associate Director is not.
# Detected so the resolver can drop the junior reading when a senior word is also present.
_ASSOCIATE = re.compile(r"\bassociate\b", re.I)

# Years stated in the title, e.g. "Engineer (5+ years)".
_TITLE_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:\+|years?|yrs?|yoe)\b", re.I)

# ── description patterns ──────────────────────────────────────────────────────
#
# Anchored phrasing only. This is the entire fix for the 23% mislabelling: a description may say
# "you will lead the team" without the role being a lead position, so bare word presence is
# inadmissible. Each pattern below requires the level to be *asserted about the role*.
_DESC_ANCHORED: list[tuple[Seniority, re.Pattern[str]]] = [
    (Seniority.EXEC, re.compile(r"\bthis is (?:an? )?(?:executive|c[\s-]level|vp)[\s-]level\b", re.I)),
    (Seniority.LEAD, re.compile(
        r"\b(?:this is|hiring (?:at|for)|role is|position is|level:)\s*(?:an? )?"
        r"(?:staff|principal|lead)[\s-]?(?:level)?\b", re.I)),
    (Seniority.SENIOR, re.compile(
        r"\b(?:this is|hiring (?:at|for)|role is|position is|level:)\s*(?:an? )?"
        r"senior[\s-]?(?:level)?\b", re.I)),
    (Seniority.MID, re.compile(
        r"\b(?:this is|hiring (?:at|for)|role is|position is|level:)\s*(?:an? )?"
        r"(?:mid|intermediate)[\s-]?(?:level)?\b", re.I)),
    (Seniority.JUNIOR, re.compile(
        r"\b(?:this is|hiring (?:at|for)|role is|position is|level:)\s*(?:an? )?"
        r"(?:junior|entry)[\s-]?(?:level)?\b|\bno (?:prior )?experience (?:is )?(?:required|necessary)\b",
        re.I)),
    (Seniority.INTERN, re.compile(r"\bthis is (?:an? )?internship\b", re.I)),
    (Seniority.LEAD, re.compile(r"\b(?:we are hiring|hiring) at the [LE]\s?[67]\b", re.I)),
    (Seniority.SENIOR, re.compile(r"\b(?:we are hiring|hiring) at the [LE]\s?5\b", re.I)),
]


def _title_signals(title: str) -> list[LevelSignal]:
    out: list[LevelSignal] = []
    if not title:
        return out

    for pattern, kind in _LADDER:
        match = pattern.search(title)
        if not match:
            continue
        n = int(match.group(1))
        table = {"ladder_le": _LE_TO_LEVEL, "ladder_ic": _IC_TO_LEVEL, "ladder_ict": _ICT_TO_LEVEL}[kind]
        level = table.get(n)
        if level:
            out.append(LevelSignal(level, Confidence.STRONG, "title", match.group(0)))

    role_ladder = _ROLE_LADDER.search(title)
    if role_ladder:
        n = _ROMAN.get(role_ladder.group(1).lower())
        level = _ROLE_LADDER_TO_LEVEL.get(n or 0)
        if level:
            out.append(LevelSignal(level, Confidence.STRONG, "title", role_ladder.group(0)))

    for level, pattern in _WORD_SIGNALS:
        match = pattern.search(title)
        if match:
            out.append(LevelSignal(level, Confidence.STRONG, "title", match.group(0)))

    # Bare numerals only when no ladder code already explained the number, so "SDE II" isn't
    # double-counted and "Engineer II" still resolves.
    if not role_ladder and not any(s.evidence and s.evidence[0] in "LEIi" for s in out):
        numeral = _BARE_NUMERAL.search(title)
        if numeral:
            n = _ROMAN.get(numeral.group(1).lower())
            level = _ROLE_LADDER_TO_LEVEL.get(n or 0)
            if level:
                out.append(LevelSignal(level, Confidence.WEAK, "title", numeral.group(0)))

    years = _TITLE_YEARS.search(title)
    if years:
        out.append(
            LevelSignal(years_to_seniority(int(years.group(1))), Confidence.WEAK, "years", years.group(0))
        )
    return out


def _description_signals(description: str | None) -> list[LevelSignal]:
    """Only anchored assertions about the role. See the module docstring for why."""
    if not description:
        return []
    out: list[LevelSignal] = []
    for level, pattern in _DESC_ANCHORED:
        match = pattern.search(description)
        if match:
            out.append(LevelSignal(level, Confidence.MEDIUM, "description", match.group(0).strip()))
    return out


def _resolve(signals: list[LevelSignal], title: str) -> LevelSignal | None:
    """Pick the winning signal by specificity, then seniority.

    Ordering rules, each earning its place from a title the naive version got wrong:
      * higher confidence first — a title ladder code beats a description phrase beats a numeral
      * then higher seniority — "Senior Staff Engineer" is staff, "Associate Director" is a director
    """
    if not signals:
        return None

    # "Associate X" reads junior only when nothing more senior is claimed. "Associate Director" and
    # "Associate Principal" are senior roles that happen to contain the word.
    if _ASSOCIATE.search(title or ""):
        senior_present = any(s.seniority.rank >= Seniority.SENIOR.rank for s in signals)
        if senior_present:
            signals = [
                s for s in signals
                if not (s.seniority is Seniority.JUNIOR and "associate" in s.evidence.lower())
            ]

    return max(signals, key=lambda s: (int(s.confidence), s.seniority.rank))


def extract_level(
    title: str, description: str | None = None, *, stated_years: int | None = None
) -> LevelVerdict:
    """Resolve a job's level from its title, an anchored description phrase, or stated years.

    Title first and title almost-only. `stated_years` (from the years extractor) is admitted as a
    last resort so a posting that gives "6+ years" but no level word still filters correctly — the
    case the user is most likely to hit and the old code could not handle at all.
    """
    signals = _title_signals(title) + _description_signals(description)

    # A stated 0 is discarded rather than read as "entry level". `extract_min_years` takes the
    # *minimum* across every match and floors months, so "6 months of Kubernetes" and "0 to 5 years"
    # both yield 0 — which is why a corpus row titled "Senior Forward Deployed Engineer" carried
    # min_years_experience = 0. Genuine entry-level roles are caught by the anchored
    # "no experience required" pattern instead, which is evidence rather than an artefact.
    if not signals and stated_years is not None and stated_years > 0:
        signals.append(
            LevelSignal(years_to_seniority(stated_years), Confidence.WEAK, "years", f"{stated_years} years")
        )

    winner = _resolve(signals, title)
    if winner is None:
        return LevelVerdict(signals=tuple(signals))

    return LevelVerdict(
        seniority=winner.seniority,
        confidence=winner.confidence,
        source=winner.source,
        evidence=winner.evidence,
        years_band=seniority_to_years(winner.seniority),
        signals=tuple(signals),
    )


def matches_max_seniority(job_level: Seniority | None, ceiling: Seniority) -> bool:
    """Whether a job passes a "no more senior than X" filter.

    An unknown level passes. That is deliberate and matches the retrieval layer's existing stance:
    unknown is not the same as excluded, and silently dropping every unlabelled job would hide most
    of the corpus. The fix for unknowns is better extraction, not a stricter filter.
    """
    if job_level is None:
        return True
    return job_level.rank <= ceiling.rank


def matches_years(job_level: Seniority | None, stated_years: int | None, wanted_years: int) -> bool:
    """Whether a job suits someone with `wanted_years` of experience.

    Uses whichever signal exists, which is the point: a posting with no stated years still filters
    via its level's implied band, and a posting with no level still filters via its years. Previously
    a job missing `min_years_experience` — most of them — escaped years filtering entirely.
    """
    floor = 0
    if stated_years is not None and stated_years > 0:
        floor = stated_years
    if job_level is not None:
        band_floor = seniority_to_years(job_level)[0]
        # The stricter of the two wins, because the two signals fail in opposite directions and the
        # level is the more trustworthy. Measured: postings titled "Senior Software Engineer" asking
        # for 5+ and 7+ years had years extracted as 2 and 0, which let them pass a "max 2 years"
        # search. Taking the max means a mis-read years figure can no longer soften an explicit
        # title, while a genuinely low-bar senior posting still filters on what it asked for.
        floor = max(floor, band_floor)
    # Compared against the floor only: a senior role wanting 5+ years is a bad fit for someone with
    # 2, but someone with 12 is not disqualified from a mid-level role.
    return wanted_years >= floor
