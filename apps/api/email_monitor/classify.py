"""Deciding what a job-related email means (§7).

Two separate questions, deliberately kept apart:

  1. **Is this about a job I applied to?** (`match_lead`) — sender and subject matched against the
     applications already in the pipeline.
  2. **What happened?** (`classify_outcome`) — rejection, interview, offer, assessment, or just an
     acknowledgement.

Deterministic rules rather than an LLM, for the same reason the rest of this codebase leans that
way: the LLM endpoint is optional configuration, and a feature that silently stops working when it
isn't set is worse than one that is slightly blunter. An LLM pass can refine a low-confidence
verdict later behind the same interface.

The ordering inside `classify_outcome` is the subtle part and is not arbitrary — see the note there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse


class Outcome(StrEnum):
    REJECTED = "rejected"
    OFFER = "offer"
    INTERVIEW = "interview"
    ASSESSMENT = "assessment"
    ACKNOWLEDGED = "acknowledged"
    UNKNOWN = "unknown"


# How an outcome moves the pipeline. `None` means "record the event, leave the status alone".
OUTCOME_TO_STATUS: dict[Outcome, str | None] = {
    Outcome.REJECTED: "rejected",
    Outcome.OFFER: "accepted",
    Outcome.INTERVIEW: "interviewing",
    # An assessment is a real next step but not an interview yet. Advancing the status would
    # overstate progress; the event still records that something happened.
    Outcome.ASSESSMENT: None,
    Outcome.ACKNOWLEDGED: None,
    Outcome.UNKNOWN: None,
}

_REJECTION = (
    r"we (?:have )?(?:decided|chosen) (?:to )?(?:move forward|proceed) with (?:other|another)",
    r"not (?:be )?(?:moving|proceeding|going) forward",
    r"not (?:been )?(?:selected|successful|shortlisted)",
    r"unfortunately[^.]{0,80}(?:not|unable|other candidates)",
    r"we(?:'| a)re unable to (?:offer|proceed|move)",
    r"(?:position|role|vacancy) has been filled",
    r"pursuing other candidates",
    r"will not be (?:progressing|continuing)",
    r"regret to inform",
    r"no longer under consideration",
)

_OFFER = (
    r"(?:pleased|delighted|happy|excited) to (?:offer|extend)",
    r"(?:job|employment|formal) offer",
    r"offer of employment",
    r"we(?:'| woul)d like to offer you",
    r"welcome (?:aboard|to the team)",
)

_INTERVIEW = (
    r"(?:schedule|set ?up|arrange|book)(?:e?d)? (?:a |an |your )?(?:call|chat|interview|screen)",
    r"invit(?:e|ing|ation) (?:you )?(?:to|for) (?:an? )?(?:interview|call|conversation)",
    r"would (?:you )?(?:like|be available) (?:to )?(?:chat|talk|meet|speak)",
    r"next (?:step|stage) (?:is|will be) (?:an? )?(?:interview|call)",
    r"(?:phone|technical|onsite|final)[- ](?:screen|interview)",
    r"availability for (?:an? )?(?:call|interview|chat)",
)

_ASSESSMENT = (
    r"(?:take[- ]home|coding|technical|online) (?:assessment|challenge|exercise|test)",
    r"complete (?:the |this |an )?(?:assessment|challenge|exercise|questionnaire)",
    r"hackerrank|codility|coderpad|karat|hackerearth",
)

_ACKNOWLEDGED = (
    r"(?:we |have )?received your application",
    r"thank(?:s| you) for (?:your interest|applying|your application)",
    r"application (?:has been )?(?:received|submitted)",
    r"we(?:'| a)re reviewing your",
)

# ATS senders. Mail from these is about *an* application even when no company name appears in the
# address, which matters because the sender is often the ATS rather than the employer.
ATS_MAIL_DOMAINS = frozenset({
    "greenhouse.io", "greenhouse-mail.io", "us.greenhouse-mail.io",
    "lever.co", "hire.lever.co",
    "ashbyhq.com", "otta.com",
    "workday.com", "myworkday.com", "myworkdayjobs.com",
    "smartrecruiters.com", "workable.com", "recruitee.com", "teamtailor.com",
    "bamboohr.com", "jazz.co", "applytojob.com", "breezy.hr",
    "icims.com", "taleo.net", "successfactors.com", "avature.net",
})

_WORD_RE = re.compile(r"[a-z0-9]+")
# Words too generic to identify a company. Matching on these alone attributes mail to the wrong job.
_STOP_COMPANY_WORDS = frozenset({
    "the", "inc", "llc", "ltd", "gmbh", "corp", "co", "company", "group", "labs", "technologies",
    "tech", "solutions", "systems", "software", "digital", "global", "international", "team",
    "careers", "jobs", "recruiting", "talent", "hr", "people", "and",
})


@dataclass
class OutcomeVerdict:
    outcome: Outcome
    matched: str = ""
    confidence: str = "low"


@dataclass
class LeadMatch:
    job_id: str
    company: str
    reason: str
    confidence: str


def classify_outcome(subject: str, body: str) -> OutcomeVerdict:
    """What this message says happened.

    **Order is load-bearing.** Rejections are checked first because they routinely contain the
    vocabulary of every other category: "we won't be scheduling an interview", "we were impressed by
    your assessment", "thank you for applying". Checking interview or acknowledgement first would
    classify a large share of rejections as progress, which is the worst possible error here — the
    user would think they were advancing when they had been turned down.

    Offer is checked second for the mirror reason: an offer email often also proposes a call.
    """
    text = f"{subject}\n{body}"

    for patterns, outcome in (
        (_REJECTION, Outcome.REJECTED),
        (_OFFER, Outcome.OFFER),
        (_INTERVIEW, Outcome.INTERVIEW),
        (_ASSESSMENT, Outcome.ASSESSMENT),
        (_ACKNOWLEDGED, Outcome.ACKNOWLEDGED),
    ):
        hit = _matches(patterns, text)
        if hit:
            # A subject-line hit is stronger evidence than one buried in a quoted thread below.
            in_subject = bool(re.search(hit, subject, re.IGNORECASE))
            return OutcomeVerdict(outcome, hit, "high" if in_subject else "medium")

    return OutcomeVerdict(Outcome.UNKNOWN)


def _matches(patterns: tuple[str, ...], text: str) -> str | None:
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return pattern
    return None


def sender_domain(address: str) -> str:
    """The domain from a From header, whether or not it has a display name."""
    match = re.search(r"[\w.+-]+@([\w.-]+)", address or "")
    return match.group(1).lower().rstrip(".") if match else ""


def is_ats_domain(domain: str) -> bool:
    """True when the domain is (or is a subdomain of) a known ATS mail domain."""
    domain = domain.lower()
    return any(domain == d or domain.endswith(f".{d}") for d in ATS_MAIL_DOMAINS)


def company_tokens(company: str) -> set[str]:
    """Identifying words from a company name, minus the ones that identify nothing.

    Without the stop list, "Acme Technologies" matches any mail from any company with "technologies"
    in its name, and the outcome gets attributed to the wrong application.
    """
    return {w for w in _WORD_RE.findall((company or "").lower()) if len(w) > 2 and w not in _STOP_COMPANY_WORDS}


def url_domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def match_lead(
    *, from_address: str, subject: str, body: str, leads: list[dict]
) -> LeadMatch | None:
    """Find which applied-to job an email is about, or None.

    Signals, strongest first:
      1. the sender's domain matches the domain the user applied through
      2. a company-identifying word appears in the sender domain or display name
      3. the sender is a known ATS *and* the company name appears in the subject or body

    Returning None is the right answer far more often than not — most mail is not about a job — and a
    wrong match silently rewrites the status of an unrelated application, so ambiguity loses.
    """
    domain = sender_domain(from_address)
    if not domain:
        return None

    haystack = f"{from_address}\n{subject}\n{body}".lower()
    ats = is_ats_domain(domain)

    best: LeadMatch | None = None
    for lead in leads:
        job_id = str(lead.get("job_id") or "")
        if not job_id:
            continue
        company = str(lead.get("company") or "")
        tokens = company_tokens(company)

        applied_domain = url_domain(str(lead.get("url") or ""))
        if applied_domain and (domain == applied_domain or domain.endswith(f".{applied_domain}")):
            return LeadMatch(job_id, company, f"sender domain matches {applied_domain}", "high")

        if tokens and any(t in domain for t in tokens):
            return LeadMatch(job_id, company, f"company name in sender domain {domain}", "high")

        if ats and tokens and any(t in haystack for t in tokens):
            # Keep looking for something stronger: several applications can share one ATS, so this
            # alone is the weakest signal we act on.
            best = best or LeadMatch(job_id, company, f"{domain} (ATS) mentions {company}", "medium")

    return best
