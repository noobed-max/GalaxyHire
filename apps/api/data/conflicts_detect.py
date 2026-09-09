"""Near-duplicate profile-point detection: same work, different wording.

Pure logic — the embedding function is INJECTED (data may not import profile,
and thresholds assume the local ONNX MiniLM geometry; the hash fallback's
cosine space is different, so callers pass semantic_advisory=True when
degraded, which demotes embedding links to judge-only). Three cheapest-first
stages:
  0. deterministic loose keys (catches "RoboDyne Labs" vs "RoboDyne Labs
     (2021-2025)" — a trailing date in the company field defeats the strict
     dedupe key) — certain, zero cost;
  1. token-set Jaccard on header tokens >= 0.80 — near-exact variants;
  2. embedding cosine >= 0.95 — semantic near-identical ("feed fan-out in Go"
     vs "async feed system with Kafka" with confirming geometry); cosine in
     [EMBED_GRAY_LOW, 0.95) is judge/advisory territory.
Only bullet children are candidates.  Cross-kind links are forbidden, and
bullets from different canonical parent entities never pair.  Nothing here
deletes profile content — groups only constrain builds.
"""

from __future__ import annotations

import re
from collections import defaultdict

EMBED_MIN_COSINE = 0.95
EMBED_GRAY_LOW = 0.72
JACCARD_THRESHOLD = 0.80

_URL_EMAIL_RE = re.compile(r"https?://\S+|www\.\S+|\S+@\S+\.\S+", re.I)
_PROJECT_URL_RE = re.compile(r"(?:https?://|www\.|github\.com/)[^\s)]+", re.I)
_MD_RE = re.compile(r"\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`|\[([^\]]+)\]\([^)]+\)")
_DATE_TAIL_RE = re.compile(
    r"[\(]?\b(?:19|20)\d{2}\b[^)]*\)?$"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(?:19|20)\d{2}\b.*$",
    re.I,
)
_TOKEN_RE = re.compile(r"[a-z0-9+#.\-]{2,}")
_LEGAL_COMPANY_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "llc",
    "ltd", "limited", "plc", "gmbh", "ag", "sa",
}


def norm_text(text: str) -> str:
    t = str(text or "")
    t = _MD_RE.sub(lambda m: next(g for g in m.groups() if g is not None), t)
    t = _URL_EMAIL_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).lower().strip(" .;:-")


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text)


def loose_key(text: str) -> str:
    """Alnum key after stripping trailing date decorations from headers, so
    "RoboDyne Labs" and "RoboDyne Labs (2021-2025)" collapse together."""
    t = norm_text(text)
    t = _DATE_TAIL_RE.sub("", t).strip(" .;:-")
    return _alnum(t)


def canonical_parent_key(kind: str, header: str) -> str:
    """Canonical scope key for a bullet's parent entity.

    This mirrors the conservative entity normalization used at the profile
    merge boundary without importing ``profile`` into the data layer.  It is
    still exact (no fuzzy matching): only dates, punctuation, and a trailing
    legal-company suffix are ignored.
    """
    text = norm_text(header)
    if kind == "project":
        text = _PROJECT_URL_RE.sub(" ", text)
    text = _DATE_TAIL_RE.sub("", text).strip(" .,:;|·•-–—")
    tokens = re.findall(r"[a-z0-9+#]+", text)
    if kind == "experience":
        while len(tokens) > 1 and tokens[-1] in _LEGAL_COMPANY_SUFFIXES:
            tokens.pop()
    return "".join(tokens)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall(norm_text(text)))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(self, x: tuple[str, str]) -> tuple[str, str]:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: tuple[str, str], b: tuple[str, str]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def detect(points: list[dict], embed_fn=None, judge=None,
           semantic_advisory: bool = False) -> list[dict]:
    """Detect alternate wordings among bullet children of one entity.

    ``points`` is intentionally accepted as a generic list for callers that
    already have units, but entity rows and skills are not candidates here.
    A candidate must carry ``parent_id`` (and may carry the canonical
    ``parent_key``); this prevents a company/project title from becoming a
    conflict member.  Bullet pairs are only considered when their canonical
    parent identity matches.  Returns groups with ``>= 2`` bullet members.
    judge(text_a, text_b) -> bool adjudicates gray-zone pairs; with
    semantic_advisory=True (degraded embedder) even strong cosine links need
    the judge to confirm."""
    # Older snapshots and callers may still pass the pre-point rows.  Filtering
    # at the detector boundary is important: every rescan and every automatic
    # sync then converges to bullet-only memberships, even before the profile
    # has been rewritten.
    points = [
        p for p in (points or [])
        if isinstance(p, dict) and str(p.get("kind") or "") in {"experience", "project"}
        and str(p.get("id") or "").strip()
        and str(p.get("parent_id") or "").strip()
        and str(p.get("text") or "").strip()
    ]
    if len(points) < 2:
        return []

    uf = _UnionFind()
    reasons: dict[frozenset, tuple[str, float]] = {}

    def link(a: dict, b: dict, reason: str, score: float) -> None:
        ka, kb = (a["kind"], a["id"]), (b["kind"], b["id"])
        if ka == kb or ka[0] != kb[0]:
            return
        pair = frozenset((ka, kb))
        uf.union(ka, kb)
        prev = reasons.get(pair)
        if prev is None or score > prev[1]:
            reasons[pair] = (reason, score)

    def _related(a: dict, b: dict) -> bool:
        """True when the two units should NOT be paired as duplicates:
        - one is the other's parent or parent-child level mismatch
        - or both are bullets with different parent_ids (cross-company/project)
        """
        ap = a.get("parent_key") or a.get("parent_id") or ""
        bp = b.get("parent_key") or b.get("parent_id") or ""
        if ap and ap == str(b.get("id") or ""):
            return True
        if bp and bp == str(a.get("id") or ""):
            return True
        if bool(ap) != bool(bp):
            return True
        return bool(ap and bp and ap != bp)

    # Stage 0 — deterministic loose keys on bullet text
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for p in points:
        # Child bullets index only their text.  Headers are entity identity and
        # must never independently create a duplicate group.
        sources = (p.get("text") or "",)
        for source in sources:
            key = loose_key(source)
            if len(key) >= 6:
                by_key[(p["kind"], key)].append(p)
    for bucket in by_key.values():
        for i, a in enumerate(bucket):
            for b in bucket[i + 1:]:
                if _related(a, b):
                    continue
                link(a, b, "exact", 1.0)

    # Stage 1 — token-set Jaccard on bullet text.  Headers are entity identity,
    # so comparing them would flag every pair of siblings under one job/project.
    # A single shared token is noise; require real overlap.
    text_tokens = {id(p): _tokens(p.get("text") or "") for p in points}
    for i, a in enumerate(points):
        for b in points[i + 1:]:
            if a["kind"] != b["kind"] or _related(a, b):
                continue
            ta, tb = text_tokens[id(a)], text_tokens[id(b)]
            shared = ta & tb
            if len(shared) >= 2:
                score = jaccard(ta, tb)
                if score >= JACCARD_THRESHOLD:
                    link(a, b, "fuzzy", score)

    # Stage 2 — embedding cosine, cheapest info last
    if embed_fn is not None:
        normed = [norm_text(str(p.get("text") or "")) for p in points]
        try:
            vectors = embed_fn(normed)
        except Exception:
            vectors = []
        if vectors and len(vectors) == len(points):
            for i, a in enumerate(points):
                for j in range(i + 1, len(points)):
                    b = points[j]
                    if a["kind"] != b["kind"]:
                        continue
                    # Bullet comparisons require matching canonical parent
                    # identity (same company/role or same project).
                    if _related(a, b):
                        continue
                    cos = _cosine(vectors[i], vectors[j])
                    if cos >= EMBED_MIN_COSINE:
                        if semantic_advisory:
                            if judge and judge(a.get("text") or "", b.get("text") or ""):
                                link(a, b, "embedding", cos)
                        else:
                            link(a, b, "embedding", cos)
                    elif EMBED_GRAY_LOW <= cos < EMBED_MIN_COSINE and judge \
                            and judge(a.get("text") or "", b.get("text") or ""):
                        link(a, b, "embedding", cos)

    # Clusters → groups
    clusters: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for p in points:
        clusters[uf.find((p["kind"], p["id"]))].append(p)
    groups: list[dict] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        member_pairs = [(m["kind"], m["id"]) for m in members]
        best_reason, best_score = "embedding", 0.0
        for i, ka in enumerate(member_pairs):
            for kb in member_pairs[i + 1:]:
                pair = frozenset((ka, kb))
                if pair in reasons and reasons[pair][1] > best_score:
                    best_reason, best_score = reasons[pair]
        groups.append({
            "reason": best_reason, "score": best_score,
            "members": [{"kind": k, "id": i} for k, i in member_pairs],
        })
    return groups


def flatten_profile_units(profile: dict) -> list[dict]:
    """Return only selectable bullet children as detector candidates.

    Skills and experience/project rows are entity identity, not duplicate
    points.  ``parent_key`` lets legacy rows with harmless role/company/title
    formatting differences share a detector scope while the stored member IDs
    remain the real bullet IDs.
    """
    units: list[dict] = []
    if not isinstance(profile, dict):
        return units
    for key, kind, _text_key in (("exp", "experience", "d"), ("projects", "project", "impact")):
        for row in profile.get(key) or []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            parent_id = str(row["id"])
            role = str(row.get("role") or "").strip()
            company = str(row.get("co") or row.get("company") or "").strip()
            header = " ".join(
                str(part or "").strip()
                for part in ((row.get("title") or "") if kind == "project" else role, company)
                if str(part or "").strip()
            )
            scope_header = f"role {role} company {company}" if kind == "experience" else header
            for point in row.get("points") or []:
                if isinstance(point, dict) and point.get("id"):
                    units.append({"kind": kind, "id": str(point["id"]), "parent_id": parent_id,
                                  "parent_key": canonical_parent_key(kind, scope_header),
                                  "text": str(point.get("text") or ""), "header": header})
    return units


def sync_detection(units: list[dict], store, db_path: str | None = None,
                   embed_fn=None, semantic_advisory: bool = False,
                   judge=None) -> dict:
    """Detect + persist bullet-only groups over ``units``.

    Stale memberships are pruned before looking up existing members.  This is
    deliberately done on every sync: old installations may contain entity-row
    memberships and those rows must disappear from conflict state without
    touching profile content.  Existing valid memberships are still skipped,
    preserving dismissed groups and making rescans idempotent.
    """
    units = [
        u for u in (units or [])
        if isinstance(u, dict) and str(u.get("kind") or "") in {"experience", "project"}
        and str(u.get("id") or "").strip()
        and str(u.get("parent_id") or "").strip()
        and str(u.get("text") or "").strip()
    ]
    valid_pairs = {(str(u["kind"]), str(u["id"])) for u in units}
    prune_stale = getattr(store, "prune_stale", None)
    if prune_stale is not None:
        try:
            prune_stale(valid_pairs, db_path=db_path)
        except TypeError:
            # Small in-memory test doubles from older callers may not accept
            # the keyword; they still get detector filtering below.
            prune_stale(valid_pairs)
    existing_members = {
        (m["point_kind"], m["point_id"])
        for g in store.list_groups(db_path=db_path)
        for m in g["members"]
    }
    fresh = [u for u in units if (u["kind"], u["id"]) not in existing_members]
    groups_detected = 0
    groups_created = 0
    if len(fresh) >= 2:
        detected = detect(fresh, embed_fn=embed_fn, judge=judge,
                          semantic_advisory=semantic_advisory)
        groups_detected = len(detected)
        for group in detected:
            members = [(m["kind"], m["id"]) for m in group["members"]]
            created = store.create_group(group["reason"], group["score"], members, db_path=db_path)
            if created:
                groups_created += 1
    return {"groups_detected": groups_detected, "groups_created": groups_created}
