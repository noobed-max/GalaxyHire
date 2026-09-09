"""Safe compilation helpers for a plain-English job-search brief.

The scraper has one deliberately narrow input: a role phrase. Everything else in a brief is a
post-scrape filter. Keeping that split here gives the API a deterministic safety net when the
corpus parser or its optional LLM is unavailable; an exclusion such as ``no SDE 3`` must never
become a positive scraper keyword merely because another service is down.
"""

from __future__ import annotations

import re
from typing import Any

_NEGATION = r"(?:no|not|without|excluding|except)"
_NEGATIVE_CLAUSE_RE = re.compile(
    rf"\b{_NEGATION}\s+(?:looking\s+for\s+)?(?:any\s+)?"
    r"([a-z0-9 /+#.\-]+?)(?=,|\.|;|:|\s+and\s+|\s+but\s+|$)",
    re.IGNORECASE,
)
_ROLE_BOUNDARY_RE = re.compile(rf"[,;]|\b{_NEGATION}\b", re.IGNORECASE)
_LEADING_INTENT_RE = re.compile(
    r"^(?:i(?:'m| am)?\s+)?(?:looking|searching)\s+for\s+|"
    r"^(?:please\s+)?(?:find|show)\s+(?:me\s+)?|^i\s+want\s+",
    re.IGNORECASE,
)
_LIST_FIELDS = ("negative_titles", "negative_phrases", "positive_skills")
_SCALAR_FIELDS = ("max_seniority", "max_years", "remote", "location")

# Corrections are intentionally limited to common role words. A general spell checker can turn a
# company, language, or niche title into something the user never asked for; these pairs only heal
# obvious keyboard transpositions observed in the search box.
_ROLE_WORD_CORRECTIONS = {
    "sofwtare": "software",
    "softawre": "software",
    "softwere": "software",
    "enginer": "engineer",
    "enginner": "engineer",
    "engeneer": "engineer",
    "developper": "developer",
    "develoepr": "developer",
}

_SOFTWARE_ROLE_ALIASES = (
    re.compile(r"^software\s+(?:engineer|developer|development\s+engineer)s?$", re.IGNORECASE),
    re.compile(r"^(?:sde|swe)(?:\s*(?:i|1))?$", re.IGNORECASE),
)
_SOFTWARE_ROLE_QUERY_RE = re.compile(
    r"(^|\b)(software\s+(?:engineering|engineer|developer|development\s+engineer)|sde|swe)(\b|$)",
    re.IGNORECASE,
)
_SOFTWARE_TITLE_RE = re.compile(
    r"(?:"
    r"\bsoftware[\s-]+(?:engineer|developer)s?\b|"
    r"\bsoftware[\s-]+development[\s-]+engineers?\b|"
    r"\b(?:sde|swe)(?:[\s-]*(?:i|ii|iii|1|2|3))?\b|"
    r"\b(?:back[\s-]?end|front[\s-]?end|full[\s-]?stack|fullstack|web|mobile|android|ios)"
    r"[\s-]+(?:software[\s-]+)?(?:engineer|developer)s?\b|"
    r"\b(?:application|platform|cloud|devops|site[\s-]+reliability)"
    r"[\s-]+(?:engineer|developer)s?\b"
    r")",
    re.IGNORECASE,
)

# Places a search brief can name. Matched as whole comma-segments (or trailing words),
# NEVER as substrings — "India" must not meet "Indiana", "UK" must not meet "task".
# Canonical output reuses the retrieval/scraper alias table (services/corpus
# galaxy/search/retrieval.py _COUNTRY_ALIASES + scraper-node src/audit/relevance.ts
# normalizedLocation): "UK" and "United Kingdom" must produce one freshness key.
_LOCATION_COUNTRIES = frozenset({
    "afghanistan", "albania", "algeria", "argentina", "armenia", "australia", "austria",
    "azerbaijan", "bahrain", "bangladesh", "belarus", "belgium", "brazil", "bulgaria",
    "cambodia", "canada", "chile", "china", "colombia", "costa rica", "croatia",
    "czech republic", "czechia", "denmark", "egypt", "estonia", "ethiopia", "finland",
    "france", "georgia", "germany", "ghana", "greece", "hong kong", "hungary", "iceland",
    "india", "indonesia", "ireland", "israel", "italy", "japan", "jordan", "kazakhstan",
    "kenya", "kuwait", "latvia", "lithuania", "luxembourg", "malaysia", "mexico",
    "morocco", "nepal", "netherlands", "new zealand", "nigeria", "norway", "oman",
    "pakistan", "peru", "philippines", "poland", "portugal", "qatar", "romania",
    "saudi arabia", "serbia", "singapore", "slovakia", "slovenia", "south africa",
    "south korea", "spain", "sri lanka", "sweden", "switzerland", "taiwan", "thailand",
    "turkey", "ukraine", "united arab emirates", "united kingdom", "united states",
    "uruguay", "uzbekistan", "vietnam",
})
_LOCATION_COUNTRY_ALIASES = {
    "uk": "united kingdom",
    "gb": "united kingdom",
    "great britain": "united kingdom",
    "britain": "united kingdom",
    "england": "united kingdom",
    "scotland": "united kingdom",
    "wales": "united kingdom",
    "usa": "united states",
    "us": "united states",
    "america": "united states",
    "uae": "united arab emirates",
    "emirates": "united arab emirates",
    "holland": "netherlands",
    "czechia": "czech republic",
}
_LOCATION_CITIES = frozenset({
    "amsterdam", "athens", "atlanta", "austin", "bangalore", "bengaluru", "barcelona",
    "beijing", "berlin", "bogota", "boston", "brussels", "bucharest", "budapest",
    "buenos aires", "chennai", "chicago", "copenhagen", "dallas", "delhi", "new delhi",
    "denver", "dubai", "dublin", "edinburgh", "frankfurt", "gurgaon", "gurugram",
    "hamburg", "helsinki", "ho chi minh city", "hong kong", "hyderabad", "istanbul",
    "jakarta", "johannesburg", "kuala lumpur", "kyiv", "lagos", "lima", "lisbon",
    "london", "los angeles", "madrid", "manchester", "manila", "melbourne", "mexico city",
    "miami", "milan", "montreal", "moscow", "mumbai", "munich", "nairobi", "new york",
    "noida", "oslo", "ottawa", "paris", "philadelphia", "prague", "pune", "rio de janeiro",
    "rome", "san francisco", "santiago", "sao paulo", "seattle", "seoul", "shanghai",
    "shenzhen", "singapore", "stockholm", "sydney", "taipei", "tel aviv", "tokyo",
    "toronto", "vancouver", "vienna", "warsaw", "washington", "zurich",
})
_LOCATION_CITY_ALIASES = {
    "bangalore": "bengaluru",
    "gurgaon": "gurugram",
    "new york city": "new york",
    "nyc": "new york",
    "sf": "san francisco",
    "la": "los angeles",
}

# States/provinces resolve to "Region, Country" so a bare "Texas" still scopes the
# search to the right country AND keeps the region for substring matching.
_LOCATION_REGIONS = {
    "texas": "texas, united states",
    "california": "california, united states",
    "florida": "florida, united states",
    "washington": "washington, united states",
    "oregon": "oregon, united states",
    "colorado": "colorado, united states",
    "illinois": "illinois, united states",
    "ohio": "ohio, united states",
    "arizona": "arizona, united states",
    "virginia": "virginia, united states",
    "massachusetts": "massachusetts, united states",
    "pennsylvania": "pennsylvania, united states",
    "north carolina": "north carolina, united states",
    "new jersey": "new jersey, united states",
    "ontario": "ontario, canada",
    "quebec": "quebec, canada",
    "british columbia": "british columbia, canada",
    "karnataka": "karnataka, india",
    "maharashtra": "maharashtra, india",
    "tamil nadu": "tamil nadu, india",
    "telangana": "telangana, india",
    "kerala": "kerala, india",
    "gujarat": "gujarat, india",
    "punjab": "punjab, india",
    "west bengal": "west bengal, india",
}

# A low-experience search is safer when title evidence is considered before description-derived
# years. These are title markers, not phrases to ban from the whole JD: a junior posting can quite
# legitimately say that it works with a senior engineer.
_EXPERIENCED_TITLE_EXCLUSIONS = (
    "senior",
    "sr",
    "staff",
    "principal",
    "lead",
    "director",
    "manager",
    "architect",
    "intermediate",
    "mid-level",
    "level ii",
    "level 2",
    "l2",
    "sde 2",
    "sde ii",
    "sde 3",
    "sde iii",
)


def normalize_role_phrase(text: str | None) -> str:
    """Correct safe role typos and collapse exact software-role aliases.

    ``software engineer``, ``software developer``, ``SDE`` and ``SWE`` represent one retrieval
    family. Canonicalising the exact aliases makes freshness tracking and collection consistent:
    changing only the abbreviation does not trigger three redundant all-connector scrapes.
    """
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value:
        return ""
    words = re.findall(r"[A-Za-z0-9+#.-]+|[^A-Za-z0-9+#.-]+", value)
    corrected = "".join(
        _ROLE_WORD_CORRECTIONS.get(part.casefold(), part) if re.fullmatch(r"[A-Za-z]+", part) else part
        for part in words
    )
    corrected = re.sub(r"\s+", " ", corrected).strip()
    if any(pattern.fullmatch(corrected) for pattern in _SOFTWARE_ROLE_ALIASES):
        return "software engineer"
    return corrected


def role_title_matches(query: str | None, title: str | None) -> bool:
    """Whether a known role-family query accepts ``title``.

    Unknown roles return True and keep semantic retrieval available. This is a second safety gate
    at the app/corpus boundary: even if the corpus is an older process without the SQL gate, a
    software search cannot save a Production Associate as a plausible nearest neighbour.
    """
    if not _SOFTWARE_ROLE_QUERY_RE.search(str(query or "")):
        return True
    return bool(_SOFTWARE_TITLE_RE.search(str(title or "")))


def extract_role_phrase(text: str | None) -> str:
    """Return only the first positive role phrase from a full search description."""
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value:
        return ""
    role = _ROLE_BOUNDARY_RE.split(value, maxsplit=1)[0]
    role = _LEADING_INTENT_RE.sub("", role).strip(" \t,.;:-")
    return normalize_role_phrase(role)


def _canonical_place(token: str) -> tuple[str, str] | None:
    """Classify one token as ("city"|"country"|"region", canonical name), or None if unknown."""
    low = re.sub(r"\s+", " ", token.strip().casefold())
    if not low:
        return None
    if low in _LOCATION_COUNTRY_ALIASES:
        return ("country", _LOCATION_COUNTRY_ALIASES[low])
    if low in _LOCATION_COUNTRIES:
        return ("country", low)
    if low in _LOCATION_REGIONS:
        return ("region", _LOCATION_REGIONS[low])
    if low in _LOCATION_CITY_ALIASES:
        return ("city", _LOCATION_CITY_ALIASES[low])
    if low in _LOCATION_CITIES:
        return ("city", low)
    return None


def extract_location_phrase(text: str | None) -> str:
    """Lift a "City, Country" location out of a free-text brief, or "" if none is named.

    The role phrase and every negation clause are removed first, so "no principal" can
    never become a place and the role itself ("Software Engineer") is never mistaken for
    one. Remaining comma-segments match whole against the gazetteer; a segment without
    commas falls back to trailing 2-word then 1-word suffixes ("New York", "London").
    Returns "City, Country" when both are found, else whichever was found, canonicalized
    ("UK" → "United Kingdom", "Bangalore" → "Bengaluru") so scrapes, freshness keys and
    retrieval all see one stable string for one place.
    """
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value:
        return ""
    # Remove every negation clause first ("no principal" must never become a place), then
    # scan each comma-segment whole plus its trailing 2-word/1-word suffixes. The role
    # head needs no special-casing: no role word is in the gazetteer, so "Software
    # Engineer" matches nothing while "London" (or "... New York") does — with or
    # without a comma before it.
    rest = _NEGATIVE_CLAUSE_RE.sub(" ", value)
    cities: list[str] = []
    countries: list[str] = []
    regions: list[str] = []
    for segment in re.split(r"[,;]", rest):
        words = segment.strip().split()
        for size in (2, 1):
            if size > len(words):
                continue
            hit = _canonical_place(" ".join(words[-size:]))
            if hit:
                kind, name = hit
                if kind == "city":
                    cities.append(name)
                elif kind == "country":
                    countries.append(name)
                else:
                    regions.append(name)
                break
    city = next(iter(dict.fromkeys(cities)), "")
    country = next(iter(dict.fromkeys(countries)), "")
    region = next(iter(dict.fromkeys(regions)), "")

    def _title(name: str) -> str:
        return " ".join(w.capitalize() for w in name.split())

    if city and country:
        return f"{_title(city)}, {_title(country)}"
    if region and not country:
        # Region already carries its country ("Texas, United States").
        return _title(region)
    return ", ".join(part for part in (_title(city), _title(country)) if part)


def _unique_strings(*values: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        items = value if isinstance(value, list) else []
        for item in items:
            cleaned = re.sub(r"\s+", " ", str(item or "")).strip(" ,.")
            key = cleaned.casefold()
            if cleaned and key not in seen:
                seen.add(key)
                out.append(cleaned)
    return out


def fallback_search_intent(text: str | None) -> dict[str, Any]:
    """Deterministically lift the common exclusions from a search brief."""
    value = str(text or "").strip()
    if not value:
        return {}

    out: dict[str, Any] = {}
    role = extract_role_phrase(value)
    if role:
        out["search_term"] = role

    # Places named in the brief ("..., India, Bengaluru") become the location filter.
    # Without this they vanished entirely: the role phrase drops them, no other parser
    # sets location, and the search ran worldwide while claiming to be local.
    location = extract_location_phrase(value)
    if location:
        out["location"] = location

    raw_negative_phrases = [
        match.group(1).strip(" ,.") for match in _NEGATIVE_CLAUSE_RE.finditer(value)
        if match.group(1).strip(" ,.")
    ]

    # A negated level is a ceiling one band below it. Specific names such as SDE 2 remain exact
    # negative phrases; silently treating every numbered title as a universal seniority band would
    # be unreliable across companies.
    ceiling_by_excluded_level = {
        "junior": "intern",
        "jr": "intern",
        "mid": "junior",
        "mid-level": "junior",
        "senior": "mid",
        "sr": "mid",
        "staff": "senior",
        "principal": "senior",
        "lead": "senior",
    }
    ceiling_rank = {"intern": 0, "junior": 1, "mid": 2, "senior": 3}
    ceilings: list[str] = []
    for phrase in raw_negative_phrases:
        low = phrase.casefold()
        for word, ceiling in ceiling_by_excluded_level.items():
            if re.search(rf"\b{re.escape(word)}\b", low):
                ceilings.append(ceiling)
    if ceilings:
        out["max_seniority"] = min(ceilings, key=ceiling_rank.__getitem__)

    # "No 3+ years" means jobs requiring three years are excluded too, hence a maximum of two.
    # "No more than 3 years" permits three, so that wording keeps a ceiling of three.
    year_ceilings: list[int] = []
    year_phrases: set[str] = set()
    for phrase in raw_negative_phrases:
        plus = re.search(r"\b(\d+)\s*\+\s*(?:years?|yrs?)\b", phrase, re.IGNORECASE)
        at_least = re.search(
            r"\b(?:at\s+least|minimum(?:\s+of)?)\s+(\d+)\s*(?:years?|yrs?)\b",
            phrase,
            re.IGNORECASE,
        )
        above = re.search(
            r"\b(?:more\s+than|over|above)\s+(\d+)\s*(?:years?|yrs?)\b",
            phrase,
            re.IGNORECASE,
        )
        if plus or at_least:
            year_ceilings.append(max(0, int((plus or at_least).group(1)) - 1))
            year_phrases.add(phrase.casefold())
        elif above:
            year_ceilings.append(int(above.group(1)))
            year_phrases.add(phrase.casefold())
    if year_ceilings:
        out["max_years"] = min(year_ceilings)

    title_phrases: list[str] = []
    remaining_phrases: list[str] = []
    title_markers = {
        *_EXPERIENCED_TITLE_EXCLUSIONS,
        "junior",
        "jr",
        "mid",
        "intern",
        "internship",
        "entry",
        "entry-level",
        "associate",
        "new grad",
    }
    for phrase in raw_negative_phrases:
        low = phrase.casefold()
        if low in year_phrases:
            continue
        if any(re.search(rf"\b{re.escape(marker)}\b", low) for marker in title_markers):
            title_phrases.append(phrase)
        else:
            remaining_phrases.append(phrase)

    # A "no 3+ years" search is not complete if it relies only on imperfect JD extraction. Apply
    # the requested title-first screen, then let max_years inspect the structured JD evidence.
    if out.get("max_years") is not None and int(out["max_years"]) <= 2:
        title_phrases.extend(_EXPERIENCED_TITLE_EXCLUSIONS)

    if title_phrases:
        out["negative_titles"] = _unique_strings(title_phrases)
    if remaining_phrases:
        out["negative_phrases"] = _unique_strings(remaining_phrases)

    return out


def normalize_search_intent(
    text: str | None,
    parsed: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Merge a corpus parse with deterministic constraints and a sanitized role phrase."""
    local = fallback_search_intent(text)
    parsed = parsed if isinstance(parsed, dict) else {}

    # Even a parser-provided term is sanitized. This is the final invariant before a string can
    # reach the scraper and protects against an LLM echoing the whole sentence into search_term.
    query = extract_role_phrase(
        str(parsed.get("search_term") or local.get("search_term") or text or "")
    )

    filters: dict[str, Any] = {}
    for field in _LIST_FIELDS:
        values = _unique_strings(local.get(field), parsed.get(field))
        if values:
            filters[field] = values

    for field in _SCALAR_FIELDS:
        parsed_value = parsed.get(field)
        local_value = local.get(field)
        if field == "max_years" and parsed_value is not None and local_value is not None:
            # A syntactic "3+" ceiling is unambiguous; take the stricter correct interpretation if
            # an LLM interpreted it as "more than three".
            try:
                filters[field] = min(int(parsed_value), int(local_value))
            except (TypeError, ValueError):
                filters[field] = local_value
        elif parsed_value is not None:
            filters[field] = parsed_value
        elif local_value is not None:
            filters[field] = local_value

    return query, filters
