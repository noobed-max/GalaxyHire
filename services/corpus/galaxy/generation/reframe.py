"""Reframe fact-guard + lint (docs/05 §3, §5).

The reframer (optional, LLM) may reword non-verbatim bullets to match a job's vocabulary, but
"match the vocabulary" is one step from keyword injection — so every rewrite passes a mechanical
fact-diff check that never trusts the prompt: all numbers, dates, org names, and named
technologies in the OUTPUT must already appear in the INPUT, else the rewrite is rejected.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import replace

import structlog

from galaxy.generation.models import LintIssue, LintResult, ResumeDoc, SelectedProject

log = structlog.get_logger(__name__)

# an injected async reframer: (original_bullet, job_keywords) -> proposed_rewrite
Reframer = Callable[[str, list[str]], Awaitable[str]]

_NUMBER_RE = re.compile(r"\b\d[\d,.%kKmMbB+]*\b")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
# ALL-CAPS acronyms (AWS, API, SQL) and symbol/internal-cap tech tokens (C++, C#, .NET, Node.js)
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,}\b")
_TECH_TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:\+\+|#|\.[A-Za-z][A-Za-z0-9]*)+")
# a capitalized word that follows a lowercase word/comma is a mid-sentence proper noun (org name),
# NOT a sentence-initial common word — this avoids flagging "Built"/"Engineered" at a sentence start
_PROPER_NOUN_RE = re.compile(r"(?<=[a-z0-9,] )[A-Z][a-zA-Z0-9]+")


def _facts(text: str) -> set[str]:
    facts: set[str] = set()
    facts |= {n.lower() for n in _NUMBER_RE.findall(text)}
    facts |= set(_YEAR_RE.findall(text))
    facts |= {t.lower() for t in _ACRONYM_RE.findall(text)}
    facts |= {t.lower() for t in _TECH_TOKEN_RE.findall(text)}
    facts |= {t.lower() for t in _PROPER_NOUN_RE.findall(text)}
    return facts


def reframe_is_faithful(original: str, rewritten: str) -> bool:
    """True iff the rewrite introduces no new numbers/dates/org/tech facts (docs/05 §3)."""
    orig_facts = _facts(original)
    new_facts = _facts(rewritten)
    introduced = new_facts - orig_facts
    # allow generic filler capitalized words that are also present as lowercase in the original
    orig_lower = original.lower()
    introduced = {f for f in introduced if f not in orig_lower}
    return not introduced


def safe_reframe(original: str, rewritten: str) -> str:
    """Return the rewrite only if faithful; otherwise keep the original (docs/05 §3)."""
    if not rewritten or not rewritten.strip():
        return original
    return rewritten if reframe_is_faithful(original, rewritten) else original


async def reframe_projects(
    projects: list[SelectedProject],
    verbatim_ids: set[str],
    job_keywords: list[str],
    reframer: Reframer,
) -> list[SelectedProject]:
    """Optionally reword non-verbatim project bullets via an injected reframer (docs/05 §3, step ③).

    This is the wiring that makes `safe_reframe` a live guard rather than a dead helper: every
    proposed rewrite must pass the fact-diff check, and a project the user marked `verbatim` is
    never touched. A reframer that errors on a bullet leaves that bullet verbatim — reframing is
    best-effort polish, never a correctness dependency.
    """
    out: list[SelectedProject] = []
    for proj in projects:
        if proj.project_id in verbatim_ids:
            out.append(proj)
            continue
        new_bullets: list[str] = []
        for bullet in proj.bullets:
            try:
                candidate = await reframer(bullet, job_keywords)
            except Exception as exc:  # noqa: BLE001 — never let reframing break tailoring
                log.warning("reframe.reframer_error", error=str(exc))
                candidate = bullet
            new_bullets.append(safe_reframe(bullet, candidate))
        out.append(replace(proj, bullets=new_bullets))
    return out


# --- lint (docs/05 §5) -----------------------------------------------------

_LLM_TELLS = [
    "leverage", "spearheaded", "synergize", "utilize", "in today's fast-paced",
    "results-driven", "detail-oriented", "passionate about", "cutting-edge", "seamlessly",
]


def lint(doc: ResumeDoc) -> LintResult:
    """ATS-safety + AI-risk lint. Our template is controlled, so ATS-safety mostly passes; we
    still flag AI 'tells' for the user to fix (we never silently rewrite — docs/05 §5)."""
    res = LintResult()

    # ATS-safety: contact must be in the body (we always put it there), single column (we always
    # emit single column). We assert the structural invariants rather than guess.
    if not doc.contact.get("email"):
        res.ats_safety.append(LintIssue("missing_contact_email", "No email in contact block", "warn"))
    if not doc.summary and not doc.projects and not doc.experience:
        res.ats_safety.append(LintIssue("empty_resume", "Resume has no content", "fail"))

    # AI-risk: telltale LLM phrasing across all prose
    blob = " ".join(
        [doc.summary, *(b for p in doc.projects for b in p.bullets),
         *(b for e in doc.experience for b in (e.get("bullets") or []))]
    ).lower()
    for tell in _LLM_TELLS:
        if tell in blob:
            res.ai_risk.append(
                LintIssue("llm_phrasing", f"Reads AI-generated: '{tell}'. Consider rewording.", "warn")
            )

    # uniform bullet cadence (all bullets nearly the same length) is an AI tell
    bullets = [b for p in doc.projects for b in p.bullets]
    if len(bullets) >= 4:
        lengths = [len(b) for b in bullets]
        spread = (max(lengths) - min(lengths)) / (sum(lengths) / len(lengths) + 1)
        if spread < 0.15:
            res.ai_risk.append(
                LintIssue("uniform_bullets", "Bullets are uniformly sized (an AI tell).", "warn")
            )
    return res
