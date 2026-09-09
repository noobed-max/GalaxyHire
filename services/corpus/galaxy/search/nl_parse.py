"""Natural-language search → structured SearchRequest filters (docs/11 §W1.1).

The web client's search bar takes a free-form request ("SWE intern/junior roles, no senior, no
SDE 2/3") and this compiles it — via one LLM tool-call — into the same filter fields the normal
`POST /search` accepts, which the UI shows as editable chips before submitting. The LLM only
*extracts* intent; it never invents a constraint. If the LLM is unavailable, a deterministic
regex fallback lifts obvious negations so search NEVER 502s on a parse.
"""

from __future__ import annotations

import re

import structlog

from galaxy.llm.client import LLMClient
from galaxy.models.enums import Seniority
from galaxy.search.planner import _SENIORITY_WORDS
from galaxy.search.profile_import import parse_tool_json

log = structlog.get_logger(__name__)

# fields we emit — a subset of SearchRequest the UI turns into filter chips
_FIELDS = (
    "search_term", "positive_skills", "positive_phrases",
    "negative_titles", "negative_phrases", "max_seniority", "max_years",
    "remote", "location",
)

_COMPILE_TOOL = {
    "type": "function",
    "function": {
        "name": "compile_search",
        "description": (
            "Compile a natural-language job search into structured filters. Only set a field the "
            "user clearly implies; never invent a constraint they did not state."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "search_term": {"type": "string", "description": "core role/title, e.g. 'software engineer'"},
                "positive_skills": {"type": "array", "items": {"type": "string"}},
                "positive_phrases": {"type": "array", "items": {"type": "string"}},
                "negative_titles": {
                    "type": "array", "items": {"type": "string"},
                    "description": "specific titles/levels to EXCLUDE, e.g. 'senior', 'sde 2', 'staff'",
                },
                "negative_phrases": {"type": "array", "items": {"type": "string"}},
                "max_seniority": {
                    "type": "string",
                    "enum": ["intern", "junior", "mid", "senior", "lead", "exec"],
                    "description": "HIGHEST seniority to include; excludes anything more senior",
                },
                "max_years": {"type": "integer", "description": "exclude jobs requiring more than N years"},
                "remote": {"type": "boolean"},
                "location": {"type": "string"},
            },
        },
    },
}

_SYSTEM = (
    "You convert a job seeker's request into structured search filters by calling compile_search "
    "exactly once. Put the core role in search_term. Put levels/titles they DON'T want into "
    "negative_titles (e.g. 'senior', 'SDE 2', 'SDE 3') and other exclusions into negative_phrases. "
    "If they cap the level (e.g. only intern/junior), set max_seniority to the highest level they "
    "will accept. Extract only what they state — never invent filters."
)


def _clean(data: dict) -> dict:
    out: dict = {}
    for k in _FIELDS:
        v = data.get(k)
        if v in (None, "", [], {}):
            continue
        out[k] = v
    ms = out.get("max_seniority")
    if isinstance(ms, str):
        low = ms.strip().lower()
        # keep only a band the planner understands (it maps max_seniority via _SENIORITY_WORDS)
        out["max_seniority"] = low if (low in _SENIORITY_WORDS or low == "exec") else None
        if out["max_seniority"] is None:
            del out["max_seniority"]
    return out


async def parse_search(text: str, client: LLMClient | None = None) -> dict:
    """Return SearchRequest-shaped filter fields parsed from `text` (only the fields it set)."""
    text = (text or "").strip()
    if not text:
        return {}
    try:
        client = client or LLMClient()
        resp = await client.chat(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": text[:2000]}],
            tools=[_COMPILE_TOOL],
            max_tokens=500,
        )
        message = resp.get("choices", [{}])[0].get("message", {})
        raw = ""
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            raw = tool_calls[0].get("function", {}).get("arguments", "")
        raw = raw or message.get("content") or ""
        cleaned = _clean(parse_tool_json(raw))
        if cleaned.get("search_term"):
            return cleaned
        log.info("nl_parse.llm_empty_fallback")
    except Exception as exc:  # noqa: BLE001 — LLM down must never break search
        log.warning("nl_parse.llm_failed", error=str(exc))
    return _fallback(text)


# --- deterministic fallback -------------------------------------------------

_NEG_RE = re.compile(
    r"\b(?:no|not|without|excluding|except)\s+(?:looking\s+for\s+)?(?:any\s+)?"
    r"([a-z0-9 /+#.\-]+?)(?=,|\.|;|:| and | but |$)",
    re.IGNORECASE,
)
_NOISE_RE = re.compile(
    r"\b(?:i'?m|i am|looking for|searching for|search for|want|roles?|jobs?|positions?|"
    r"as well as|for|any|some|the)\b",
    re.IGNORECASE,
)


def _band_below(sen: Seniority) -> Seniority | None:
    below = [s for s in Seniority if s.rank < sen.rank]
    return max(below, key=lambda s: s.rank) if below else None


def _fallback(text: str) -> dict:
    neg_phrases = [m.group(1).strip(" ,.") for m in _NEG_RE.finditer(text)]
    neg_phrases = [p for p in neg_phrases if p]

    positive = _NEG_RE.sub(" ", text)
    positive = _NOISE_RE.sub(" ", positive)
    positive = re.sub(r"\b(?:and|or|but)\b", " ", positive, flags=re.IGNORECASE)  # stray conjunctions
    positive = re.sub(r"\s+", " ", positive).strip(" ,.")

    out: dict = {}
    if positive:
        out["search_term"] = positive
    if neg_phrases:
        out["negative_phrases"] = neg_phrases

    # a negated seniority word ("no senior") caps the ceiling one band below it
    ceiling: Seniority | None = None
    for phrase in neg_phrases:
        for word, sen in _SENIORITY_WORDS.items():
            if re.search(rf"\b{re.escape(word)}\b", phrase.lower()):
                band = _band_below(sen)
                if band is not None and (ceiling is None or band.rank < ceiling.rank):
                    ceiling = band
    if ceiling is not None:
        out["max_seniority"] = ceiling.value

    # A negated threshold includes the threshold itself: "no 3+ years" means a posting requiring
    # three years is already too experienced, so SearchRequest.max_years must be two. Phrases such
    # as "no more than 3 years" permit three and therefore keep a ceiling of three.
    year_ceilings: list[int] = []
    for phrase in neg_phrases:
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
        elif above:
            year_ceilings.append(int(above.group(1)))
    if year_ceilings:
        out["max_years"] = min(year_ceilings)
    return out
