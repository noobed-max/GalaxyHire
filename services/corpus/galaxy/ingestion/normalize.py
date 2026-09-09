"""Text normalization + derived-field extraction (docs/02 §3, §3.1).

Two responsibilities:
  1. Canonical-key normalization — normCompany / normTitle / normLocation used to build the
     collision-proof canonical id (docs/03 §2). Bump NORMALIZER_VERSION when these rules change.
  2. Derived-field extraction — min_years_experience, seniority, onsite_policy,
     clearance_required, jd_keywords, jd_skills — computed once at ingest so query-time filters
     and the ATS scorer are pure lookups (docs/04 §2.1, docs/05 §4).

`jd_keywords` and `jd_skills` are deliberately DIFFERENT things:
  - jd_keywords is a frequency-ranked bag of every non-stopword token — the right input for
    lexical/ATS keyword-density signals, where any word landing in the resume counts.
  - jd_skills is the curated set of real technologies/skills the posting names (matched against
    a controlled vocabulary) — the right input for the "does the user HAVE what this job asks
    for" skill-overlap signal in retrieval/ranking. Matching skills against the keyword bag
    over-matches on filler and truncates real tech terms on long descriptions (docs/04 §3.1).

Everything here is pure (no I/O) and unit-tested.
"""

from __future__ import annotations

import re
import unicodedata

from galaxy.ingestion.levels import extract_level
from galaxy.models.enums import OnsitePolicy, Seniority

# Stamped onto every CanonicalJob. Changing any normalization rule below is a version bump
# that triggers a re-derive migration (docs/03 §2).
#   v2: added jd_skills (curated skill extraction, separate from the jd_keywords bag).
NORMALIZER_VERSION = "2"

# --- company ---------------------------------------------------------------

_COMPANY_SUFFIXES = {
    "inc", "inc.", "llc", "l.l.c.", "ltd", "ltd.", "limited", "corp", "corp.",
    "corporation", "co", "co.", "gmbh", "ag", "sa", "s.a.", "plc", "pty", "bv",
    "b.v.", "srl", "oy", "ab", "as", "the",
}
_DOMAIN_RE = re.compile(r"\b([a-z0-9-]+)\.(com|io|co|net|org|ai|dev)\b")


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def norm_company(company: str) -> str:
    """Lowercase, fold accents, strip legal suffixes and domains → a stable match key.

    "Amazon.com, Inc." → "amazon" ; "Café Corp" → "cafe"
    """
    s = _strip_accents(company or "").lower().strip()
    s = _DOMAIN_RE.sub(r"\1", s)  # amazon.com -> amazon
    s = re.sub(r"[^a-z0-9\s&+-]", " ", s)
    tokens = [t for t in s.split() if t and t not in _COMPANY_SUFFIXES]
    return " ".join(tokens).strip()


# --- title -----------------------------------------------------------------

_SENIORITY_SYNONYMS = {
    "sr": "senior", "sr.": "senior", "snr": "senior",
    "jr": "junior", "jr.": "junior",
    "mgr": "manager", "eng": "engineer", "dev": "developer",
    "swe": "software engineer", "sde": "software engineer",
}
_TITLE_NOISE_RE = re.compile(
    r"\((?:remote|hybrid|on-?site|contract|full[- ]?time|part[- ]?time|[^)]*\bid[:#]?\s*\d+[^)]*)\)",
    re.IGNORECASE,
)
_REQ_CODE_RE = re.compile(r"\b(?:req|job|posting)?[-#]?\s?\d{3,}\b", re.IGNORECASE)


def norm_title(title: str) -> str:
    """Lowercase, expand seniority/role synonyms, drop parenthetical noise and req codes.

    "Sr. SWE (Remote) #12345" → "senior software engineer"
    """
    s = _strip_accents(title or "").lower()
    s = _TITLE_NOISE_RE.sub(" ", s)
    s = _REQ_CODE_RE.sub(" ", s)
    s = re.sub(r"[^a-z0-9\s+#]", " ", s)
    tokens = [_SENIORITY_SYNONYMS.get(t, t) for t in s.split()]
    # re-split because a synonym may expand to two words (swe -> software engineer)
    out: list[str] = []
    for t in tokens:
        out.extend(t.split())
    # drop leftover req-code fragments and lone punctuation, keep tech tokens like c++, c#
    out = [t for t in out if t not in {"#", "-", "+"} and not t.isdigit()]
    return " ".join(out).strip()


# --- location --------------------------------------------------------------

_REMOTE_SENTINEL = "remote"
_REMOTE_RE = re.compile(r"\b(remote|anywhere|work\s*from\s*home|wfh|distributed)\b", re.IGNORECASE)


def norm_location(city: str | None, country: str | None, remote: bool) -> str:
    """city+country, remote collapsed to a sentinel (docs/03 §2)."""
    if remote:
        return _REMOTE_SENTINEL
    parts = [p for p in (city, country) if p]
    if not parts:
        return ""
    s = _strip_accents(" ".join(parts)).lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return " ".join(s.split()).strip()


def is_remote_text(text: str) -> bool:
    return bool(_REMOTE_RE.search(text or ""))


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]*\n[ \t]*")


def html_to_text(html: str | None) -> str | None:
    """Cheap HTML→plaintext for the description_md rendering (docs/02 §3).

    Not a full markdown converter — strips tags, decodes a few common entities, collapses
    whitespace. Good enough for keyword extraction and preview; upgrade later if needed.
    """
    if not html:
        return None
    import html as html_mod

    text = re.sub(r"<(br|/p|/div|/li)\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = html_mod.unescape(text)
    text = _WS_RE.sub("\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() or None


# --- derived: years of experience ------------------------------------------

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
# `a` only matches a digit or a spelled-out number word — never an arbitrary word, so
# "Requires 3+ years" binds a=3, not a="Requires".
_NUM = r"\d{1,2}|" + "|".join(_NUMBER_WORDS)
# "3+ years", "2-4 yrs", "at least 5 years", "minimum of three years", "36 months"
_YEARS_RE = re.compile(
    rf"(?P<a>{_NUM})\s*(?:\+|to|-|–|—|or\s+more)?\s*(?P<b>\d{{1,2}})?\s*\+?\s*"
    r"(?P<unit>years?|yrs?|months?)",
    re.IGNORECASE,
)
_YEARS_CONTEXT_RE = re.compile(r"experience|exp\b|minimum|at least|required|\+", re.IGNORECASE)


def _word_to_num(tok: str) -> int | None:
    tok = tok.strip().lower()
    if tok.isdigit():
        return int(tok)
    return _NUMBER_WORDS.get(tok)


def extract_min_years(text: str) -> int | None:
    """Headline years-of-experience requirement; None when unstated (docs/02 §3.1).

    Takes the **first** match in an experience context, not the smallest. Postings routinely state
    several durations — "5+ years of relevant IT experience and at least two years of working with
    Kubernetes" — and lead with the headline requirement, so first-match reads the number the
    employer actually gates on.

    Taking the minimum, as this did originally, systematically under-read senior roles. Measured on
    a real corpus, three postings titled "Senior Software Engineer" asking for 5+, 7+ and 5+ years
    were recorded as 2, 0 and 2, which then let them pass a "max 2 years" search. Sub-year durations
    still floor to 0, so `levels.py` treats a 0 as no signal at all.

    Ranges take the lower bound; months convert to years (floor). None never triggers a negative
    filter (unknown != excluded).
    """
    if not text:
        return None
    for m in _YEARS_RE.finditer(text):
        a = _word_to_num(m.group("a"))
        if a is None:
            continue
        unit = m.group("unit").lower()
        years = a // 12 if unit.startswith("month") else a
        # only treat as a requirement in an experience-ish context (avoid "3 years ago")
        window = text[max(0, m.start() - 60) : m.end() + 60]
        if not _YEARS_CONTEXT_RE.search(window):
            continue
        return years
    return None


# --- derived: seniority ----------------------------------------------------

# The level patterns live in `galaxy/ingestion/levels.py` — ladder codes, regional wording,
# negations, and specificity-based conflict resolution. Deliberately not duplicated here: the table
# that used to sit at this spot was the source of the 23% mislabelling described below.


def extract_seniority(title: str, description: str | None = None) -> Seniority | None:
    """Derive the seniority band. Delegates to `galaxy.ingestion.levels`.

    The previous implementation scanned the whole description for level words, which mislabelled
    about 23% of a real corpus: 741 of 1,343 "lead" labels came from descriptions saying "you will
    lead the team", `II`/`III` were read as senior when they are mid across every ladder that uses
    them, and no job could ever be labelled "mid" at all. `levels.extract_level` reads ladder codes
    (L5, SDE II, IC4), regional wording, and negations, resolves conflicts by specificity, and
    admits description text only via anchored phrasing. See that module's docstring for the numbers.

    Kept as a thin wrapper because every source adapter calls this name.
    """
    from galaxy.ingestion.levels import extract_level

    return extract_level(title, description).seniority


# --- derived: onsite policy ------------------------------------------------

_HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
_ONSITE_RE = re.compile(r"\b(on-?site|in-?office|in person|relocat)\b", re.I)


def extract_onsite_policy(text: str, remote_flag: bool) -> OnsitePolicy | None:
    if remote_flag or is_remote_text(text):
        if _HYBRID_RE.search(text or ""):
            return OnsitePolicy.HYBRID
        return OnsitePolicy.REMOTE
    if _HYBRID_RE.search(text or ""):
        return OnsitePolicy.HYBRID
    if _ONSITE_RE.search(text or ""):
        return OnsitePolicy.ONSITE
    return None


# --- derived: clearance ----------------------------------------------------

_CLEARANCE_RE = re.compile(
    r"\b(security clearance|ts/sci|top secret|secret clearance|public trust|"
    r"polygraph|us citizen(?:ship)? required|must be a us citizen)\b",
    re.I,
)


def extract_clearance_required(text: str) -> bool | None:
    if not text:
        return None
    return True if _CLEARANCE_RE.search(text) else None


# --- derived: jd keywords --------------------------------------------------

# Common English function words + generic JD boilerplate. 2-letter tech terms (go, ai, ml, ci,
# qa, ux, ui, os, db) are deliberately NOT here so they survive as keywords.
_STOPWORDS = {
    # articles / conjunctions / prepositions / pronouns
    "the", "and", "for", "with", "you", "our", "will", "are", "have", "this", "that",
    "your", "their", "who", "all", "can", "from", "was", "has", "not", "but", "any",
    "to", "of", "in", "on", "at", "by", "as", "is", "it", "be", "or", "an", "we", "us",
    "they", "them", "he", "she", "his", "her", "its", "we're", "you'll", "we'll",
    "a", "i", "if", "so", "up", "out", "do", "no", "my", "me", "am", "than", "then",
    "these", "those", "there", "here", "how", "what", "when", "where", "which", "into",
    "over", "under", "such", "each", "both", "more", "most", "some", "other", "about",
    "would", "could", "should", "may", "might", "must", "been", "were", "being", "also",
    # generic JD boilerplate
    "job", "role", "work", "team", "years", "year", "experience", "including", "etc",
    "company", "candidate", "candidates", "position", "opportunity", "responsibilities",
    "requirements", "required", "preferred", "ability", "strong", "excellent", "good",
    "help", "join", "looking", "seeking", "across", "within", "using", "well", "new",
    "per", "via", "one", "two", "three", "based", "like", "make",
    "please", "apply", "email", "remote", "hybrid", "onsite", "full", "time", "part",
}
_KEYWORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,29}")


def apply_derived_fields(fields) -> None:  # noqa: C901 - a flat sequence of independent extractors
    """Populate a RawJobFields' derived columns in place (docs/02 §3.1).

    Runs the extractors over title + description + location so every ingested posting carries
    min_years_experience / seniority / onsite_policy / clearance_required / jd_keywords.
    Imported lazily-friendly: takes the pydantic RawJobFields to avoid a circular import.
    """
    text = f"{fields.title}\n{fields.description_md or fields.description_html or ''}"
    if fields.min_years_experience is None:
        fields.min_years_experience = extract_min_years(text)
    if fields.seniority is None:
        # Passes the years figure resolved just above, so a posting that states "6+ years" but no
        # level word still gets one. Without this the two extractors ran independently and level
        # coverage came out ~13 points lower than it needed to be — the years bridge in levels.py
        # existed but nothing at ingest was feeding it.
        fields.seniority = extract_level(
            fields.title, fields.description_md, stated_years=fields.min_years_experience
        ).seniority
    if fields.onsite_policy is None:
        fields.onsite_policy = extract_onsite_policy(text, fields.location.remote)
    if fields.clearance_required is None:
        fields.clearance_required = extract_clearance_required(text)
    if not fields.jd_keywords:
        fields.jd_keywords = extract_jd_keywords(fields.title, fields.description_md)
    if not fields.jd_skills:
        fields.jd_skills = extract_jd_skills(fields.title, fields.description_md)


def extract_jd_keywords(title: str, description: str | None, limit: int = 40) -> list[str]:
    """Keyword set consumed by the ranker (docs/04) and ATS scorer (docs/05 §4).

    Frequency-ranked, deduplicated, stopwords removed. Preserves tech tokens like c++, c#, .net.
    """
    text = f"{title} {description or ''}"
    counts: dict[str, int] = {}
    for m in _KEYWORD_RE.finditer(text):
        tok = m.group(0).lower().strip(".-")
        if len(tok) < 2 or tok in _STOPWORDS or tok.isdigit():
            continue
        counts[tok] = counts.get(tok, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [tok for tok, _ in ranked[:limit]]


# --- derived: jd skills ----------------------------------------------------

# Controlled skill vocabulary: canonical form -> alternate spellings. The canonical form is
# always matchable too; aliases fold variants ("reactjs", "react.js") onto one canonical
# ("react") so the user's "React" and the JD's "React.js" line up. This is intentionally a
# curated list, not an open bag — it is the difference between "the job asks for React" and
# "the word 'team' appears in the posting." Extend it by adding one line here; it is the single
# place that governs skill matching for retrieval + ranking (docs/04 §3.1). No I/O, offline.
_SKILL_DEFS: dict[str, tuple[str, ...]] = {
    # --- languages ---
    "python": ("py",),
    "javascript": ("js", "ecmascript"),
    "typescript": ("ts",),
    "java": (),
    "kotlin": (),
    "swift": (),
    "objective-c": ("objective c", "objc"),
    "c++": ("cpp", "c plus plus"),
    "c#": ("csharp", "c sharp"),
    "go": ("golang",),
    "rust": (),
    "ruby": (),
    "php": (),
    "scala": (),
    "perl": (),
    "matlab": (),
    "dart": (),
    "elixir": (),
    "erlang": (),
    "haskell": (),
    "clojure": (),
    "groovy": (),
    "lua": (),
    "solidity": (),
    "sql": (),
    "html": ("html5",),
    "css": ("css3",),
    "sass": ("scss",),
    "bash": ("shell scripting", "shell"),
    "powershell": (),
    # --- frontend / frameworks ---
    "react": ("reactjs", "react.js"),
    "react native": (),
    "angular": ("angularjs",),
    "vue": ("vuejs", "vue.js"),
    "svelte": ("sveltekit",),
    "next.js": ("nextjs",),
    "node.js": ("nodejs", "node"),
    "express": ("express.js",),
    "redux": (),
    "jquery": (),
    "tailwind": ("tailwindcss", "tailwind css"),
    # --- backend / frameworks ---
    "django": (),
    "flask": (),
    "fastapi": (),
    "spring": ("spring boot", "springboot"),
    "ruby on rails": ("rails",),
    "laravel": (),
    "asp.net": ("asp net",),
    ".net": ("dotnet", "dot net"),
    # --- data / ml ---
    "machine learning": ("ml",),
    "deep learning": (),
    "natural language processing": ("nlp",),
    "computer vision": (),
    "reinforcement learning": (),
    "data science": (),
    "data engineering": (),
    "data analysis": ("data analytics",),
    "etl": (),
    "large language models": ("llm", "llms"),
    "generative ai": ("genai", "gen ai"),
    "tensorflow": (),
    "pytorch": (),
    "keras": (),
    "scikit-learn": ("sklearn", "scikit learn"),
    "pandas": (),
    "numpy": (),
    "spark": ("apache spark", "pyspark"),
    "hadoop": (),
    "kafka": ("apache kafka",),
    "airflow": ("apache airflow",),
    "dbt": (),
    "tableau": (),
    "power bi": ("powerbi",),
    "looker": (),
    "statistics": (),
    # --- databases ---
    "postgresql": ("postgres", "postgre"),
    "mysql": (),
    "sql server": ("mssql", "sqlserver"),
    "oracle": (),
    "sqlite": (),
    "mongodb": ("mongo",),
    "redis": (),
    "cassandra": (),
    "dynamodb": (),
    "elasticsearch": ("elastic search",),
    "snowflake": (),
    "bigquery": (),
    "redshift": (),
    "neo4j": (),
    "clickhouse": (),
    "pgvector": (),
    # --- cloud / devops ---
    "aws": ("amazon web services",),
    "azure": ("microsoft azure",),
    "gcp": ("google cloud", "google cloud platform"),
    "docker": (),
    "kubernetes": ("k8s",),
    "terraform": (),
    "ansible": (),
    "jenkins": (),
    "github actions": (),
    "gitlab ci": ("gitlab",),
    "ci/cd": ("cicd", "ci cd"),
    "helm": (),
    "prometheus": (),
    "grafana": (),
    "circleci": (),
    "cloudformation": (),
    "serverless": (),
    "lambda": ("aws lambda",),
    "openshift": (),
    # --- practices / tooling ---
    "rest": ("rest api", "rest apis", "restful", "restful api"),
    "graphql": (),
    "grpc": (),
    "microservices": (),
    "unit testing": ("unit tests",),
    "test automation": (),
    "tdd": (),
    "agile": (),
    "scrum": (),
    "kanban": (),
    "jira": (),
    "git": (),
    "linux": (),
    "unix": (),
    "distributed systems": (),
    "system design": (),
    "object-oriented programming": ("oop", "object oriented", "object-oriented"),
    "functional programming": (),
    "data structures": (),
    "algorithms": (),
    "websockets": ("websocket",),
    # --- mobile ---
    "ios": (),
    "android": (),
    "flutter": (),
    "swiftui": (),
    "jetpack compose": (),
}


def _build_skill_index() -> tuple[dict[str, str], re.Pattern]:
    alias_to_canon: dict[str, str] = {}
    for canon, aliases in _SKILL_DEFS.items():
        for form in (canon, *aliases):
            alias_to_canon[form.lower()] = canon
    # longest forms first so compound/multi-word aliases win over their prefixes ("react.js"
    # is tried before "react"; regex alternation takes the first matching branch).
    forms = sorted(alias_to_canon, key=len, reverse=True)
    # escape each whitespace-separated part and join with \s+ (re.escape escapes the space itself
    # on this Python, so building the alternation from a raw " ".replace is unsafe).
    alt = "|".join(r"\s+".join(re.escape(p) for p in f.split(" ")) for f in forms)
    # boundaries treat +/#/./internal-alnum as part of a tech token so "go" can't match inside
    # "google" and "python" can't match "python3", while:
    #   - a trailing sentence period still ends a token: "React." matches "react"
    #     (`(?!\.\w)` only blocks a dot that CONTINUES a token, e.g. the "node" in "node.js")
    #   - whole symbol tokens like "c++", ".net", "node.js" still match via their longer alias.
    regex = re.compile(
        rf"(?<![A-Za-z0-9+#.])(?:{alt})(?![A-Za-z0-9+#])(?!\.\w)",
        re.IGNORECASE,
    )
    return alias_to_canon, regex


_ALIAS_TO_CANON, _SKILL_RE = _build_skill_index()


def canonical_skill(name: str) -> str:
    """Fold a skill name to its canonical vocabulary form ("ReactJS" -> "react").

    Query-side skills are canonicalized through the SAME table used to extract jd_skills, so a
    profile's "React.js" and a posting's "React" compare equal. Unknown skills pass through
    lowercased (they simply won't intersect the vocabulary-bounded jd_skills).
    """
    key = re.sub(r"\s+", " ", (name or "").strip().lower())
    return _ALIAS_TO_CANON.get(key, key)


def extract_jd_skills(title: str, description: str | None) -> list[str]:
    """Curated skills the posting names, matched against the controlled vocabulary (docs/04 §3.1).

    Unlike jd_keywords this is NOT frequency-ranked or truncated: every vocabulary hit anywhere in
    title+description is returned (deduped, canonical, sorted). That fixes both failure modes of
    matching skills against the keyword bag — filler words masquerading as skills, and real tech
    terms falling below the keyword-bag's frequency cut on long descriptions.
    """
    text = f"{title or ''}\n{description or ''}"
    hits: set[str] = set()
    for m in _SKILL_RE.finditer(text):
        form = re.sub(r"\s+", " ", m.group(0).lower())
        canon = _ALIAS_TO_CANON.get(form)
        if canon:
            hits.add(canon)
    return sorted(hits)
