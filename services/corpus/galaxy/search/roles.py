"""Small, auditable role families used for eligibility and synonym expansion.

Embeddings are useful for ordering related work, but they are not an eligibility rule. A vector
index will always return *something*, which is how "software engineer" searches ended up saving a
Production Associate and a mechanical-design project. Known role families therefore get a strict
title gate before semantic ranking.

This is deliberately a compact hand-maintained taxonomy, not a general occupation classifier. An
unknown or niche role keeps the normal hybrid retrieval path; only families whose aliases we can
state confidently are gated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RoleFamily:
    id: str
    canonical: str
    query_aliases: tuple[str, ...]
    # PostgreSQL ARE patterns. Explicit non-word guards avoid treating "sde" inside another token
    # as an abbreviation.
    title_patterns: tuple[str, ...]


SOFTWARE_ENGINEERING = RoleFamily(
    id="software-engineering",
    canonical="software engineer",
    query_aliases=(
        '"software engineer"',
        '"software developer"',
        '"software development engineer"',
        '"backend engineer"',
        '"backend developer"',
        '"front end engineer"',
        '"front end developer"',
        '"frontend engineer"',
        '"frontend developer"',
        '"full stack engineer"',
        '"full stack developer"',
        '"fullstack engineer"',
        '"fullstack developer"',
        "SDE",
        "SWE",
    ),
    title_patterns=(
        r"(^|[^a-z0-9])software[[:space:]-]+(engineer|developer)(s)?([^a-z0-9]|$)",
        r"(^|[^a-z0-9])software[[:space:]-]+development[[:space:]-]+engineer(s)?([^a-z0-9]|$)",
        r"(^|[^a-z0-9])(sde|swe)([[:space:]-]*(i|ii|iii|1|2|3))?([^a-z0-9]|$)",
        r"(^|[^a-z0-9])(back[[:space:]-]?end|front[[:space:]-]?end|full[[:space:]-]?stack|fullstack|web|mobile|android|ios)[[:space:]-]+(software[[:space:]-]+)?(engineer|developer)(s)?([^a-z0-9]|$)",
        r"(^|[^a-z0-9])(application|platform|cloud|devops|site[[:space:]-]+reliability)[[:space:]-]+(engineer|developer)(s)?([^a-z0-9]|$)",
    ),
)

_FAMILIES = (SOFTWARE_ENGINEERING,)

_SOFTWARE_QUERY_RE = re.compile(
    r"(^|\b)(software\s+(engineering|engineer|developer|development\s+engineer)|sde|swe)(\b|$)",
    re.IGNORECASE,
)


def role_family(search_term: str | None) -> RoleFamily | None:
    """Return the confident family represented by ``search_term``, if any."""
    text = re.sub(r"\s+", " ", str(search_term or "")).strip()
    if _SOFTWARE_QUERY_RE.search(text):
        return SOFTWARE_ENGINEERING
    return None


def lexical_query(search_term: str | None) -> str:
    """Expand a known family for the lexical leg; leave all other searches unchanged."""
    family = role_family(search_term)
    if family is None:
        return str(search_term or "").strip()
    return " OR ".join(family.query_aliases)


def title_matches(search_term: str | None, title: str | None) -> bool:
    """Pure-Python mirror of the SQL gate, used by tests and audit reports."""
    family = role_family(search_term)
    if family is None:
        return True
    value = str(title or "").casefold()
    # Convert the small subset of PostgreSQL character-class syntax used above.
    for pattern in family.title_patterns:
        py_pattern = (
            pattern
            .replace("[[:space:]-]", r"[\s-]")
            .replace("[[:space:]]", r"\s")
        )
        if re.search(py_pattern, value, re.IGNORECASE):
            return True
    return False
