"""Email monitoring, end to end (§7).

Poll the inbox → work out which application each message is about → work out what it says happened →
move the pipeline and record the activity.

Three properties this leans on:

  * **Idempotent.** Runs overlap and re-scan the same window, so every processed message UID is
    remembered and skipped. Without that, one rejection email would append a pipeline event on every
    poll.
  * **Never worsens a known outcome.** A later acknowledgement email cannot pull a lead back out of
    "interviewing", and nothing overwrites a terminal status. Mail arrives out of order, and thread
    replies re-deliver old text.
  * **Silent on ambiguity.** An unmatched message is left alone. Guessing wrong rewrites the status
    of an unrelated application, which is worse than reporting nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from core.logging import get_logger
from email_monitor.classify import (
    OUTCOME_TO_STATUS,
    LeadMatch,
    Outcome,
    classify_outcome,
    match_lead,
)
from email_monitor.client import EmailClient, EmailError, MailboxConfig, Message

_log = get_logger(__name__)

# Settings keys. Prefixed so they can't collide with the many other settings in the same table.
SETTING_PREFIX = "email_monitor_"
KEY_ENABLED = f"{SETTING_PREFIX}enabled"
KEY_HOST = f"{SETTING_PREFIX}host"
KEY_PORT = f"{SETTING_PREFIX}port"
KEY_USERNAME = f"{SETTING_PREFIX}username"
KEY_PASSWORD = f"{SETTING_PREFIX}password"
KEY_MAILBOX = f"{SETTING_PREFIX}mailbox"
KEY_SEEN_UIDS = f"{SETTING_PREFIX}seen_uids"

# How many processed UIDs to remember. Comfortably more than a lookback window holds, and bounded so
# the settings row can't grow without limit.
MAX_SEEN = 2000

# Statuses that mail must never move a lead away from: the user set them by hand, or they're terminal.
TERMINAL_STATUSES = frozenset({"accepted", "rejected", "discarded"})


@dataclass
class PollResult:
    scanned: int = 0
    matched: int = 0
    updated: int = 0
    skipped_seen: int = 0
    outcomes: list[dict] = field(default_factory=list)
    error: str | None = None
    enabled: bool = True


def load_config(settings: dict) -> MailboxConfig | None:
    """Build a mailbox config from settings, or None when it isn't set up."""
    host = (settings.get(KEY_HOST) or "").strip()
    username = (settings.get(KEY_USERNAME) or "").strip()
    password = settings.get(KEY_PASSWORD) or ""
    if not (host and username and password):
        return None
    try:
        port = int(settings.get(KEY_PORT) or 993)
    except (TypeError, ValueError):
        port = 993
    return MailboxConfig(
        host=host,
        username=username,
        password=password,
        port=port,
        mailbox=(settings.get(KEY_MAILBOX) or "INBOX").strip() or "INBOX",
    )


def _load_seen(settings: dict) -> set[str]:
    raw = settings.get(KEY_SEEN_UIDS) or "[]"
    try:
        parsed = json.loads(raw)
        return {str(u) for u in parsed} if isinstance(parsed, list) else set()
    except (ValueError, TypeError):
        # A corrupt marker should mean "reprocess", not "crash". Duplicated events are recoverable;
        # a monitor that refuses to start is not.
        _log.warning("could not read the processed-message list; treating everything as new")
        return set()


def _store_seen(repo, seen: set[str]) -> None:
    trimmed = list(seen)[-MAX_SEEN:]
    repo.settings.save_settings({KEY_SEEN_UIDS: json.dumps(trimmed)})


class EmailMonitorService:
    def __init__(self, repo, client_factory=EmailClient):
        self.repo = repo
        self._client_factory = client_factory

    def status(self) -> dict:
        settings = self.repo.settings.get_settings()
        config = load_config(settings)
        return {
            "configured": config is not None,
            "enabled": str(settings.get(KEY_ENABLED, "false")).lower() == "true",
            "mailbox": config.redacted() if config else None,
            "processed_count": len(_load_seen(settings)),
        }

    def check(self) -> dict:
        """Test the connection for the Settings screen."""
        config = load_config(self.repo.settings.get_settings())
        if config is None:
            return {"ok": False, "error": "Email monitoring isn't configured yet."}
        try:
            return self._client_factory(config).check()
        except EmailError as exc:
            return {"ok": False, "error": str(exc)}

    def poll(self, *, since_days: int = 14, force: bool = False) -> PollResult:
        """One monitoring pass.

        `force` re-processes messages already seen, which is what makes a classification fix
        applicable to mail that arrived before it.
        """
        settings = self.repo.settings.get_settings()
        if not force and str(settings.get(KEY_ENABLED, "false")).lower() != "true":
            return PollResult(enabled=False)

        config = load_config(settings)
        if config is None:
            return PollResult(error="Email monitoring isn't configured yet.")

        try:
            messages = self._client_factory(config).recent(since_days=since_days)
        except EmailError as exc:
            # A mail server being unreachable is an ordinary condition, not a crash: the poll runs on
            # a schedule and will try again.
            _log.warning("email poll failed: %s", exc)
            return PollResult(error=str(exc))

        leads = self._applied_leads()
        seen = _load_seen(settings)
        result = PollResult(scanned=len(messages))

        for message in messages:
            if not force and message.uid in seen:
                result.skipped_seen += 1
                continue
            seen.add(message.uid)

            match = match_lead(
                from_address=message.from_address,
                subject=message.subject,
                body=message.body,
                leads=leads,
            )
            if match is None:
                continue
            result.matched += 1

            verdict = classify_outcome(message.subject, message.body)
            if verdict.outcome is Outcome.UNKNOWN:
                continue

            applied = self._apply_outcome(match, verdict.outcome, message)
            result.outcomes.append(
                {
                    "job_id": match.job_id,
                    "company": match.company,
                    "outcome": str(verdict.outcome),
                    "confidence": verdict.confidence,
                    "match_reason": match.reason,
                    "subject": message.subject[:160],
                    "status_changed": applied,
                }
            )
            if applied:
                result.updated += 1

        _store_seen(self.repo, seen)
        return result

    def _applied_leads(self) -> list[dict]:
        """Leads worth matching mail against.

        Restricted to ones the user actually engaged with. Matching against every discovered lead
        would attribute a recruiter's cold email to a job the user never applied to and mark it
        "rejected".
        """
        try:
            leads = self.repo.leads.get_all_leads()
        except Exception as exc:  # noqa: BLE001
            _log.warning("could not load leads for email matching: %s", exc)
            return []
        engaged = {"applied", "interviewing", "approved", "tailoring"}
        return [lead for lead in leads if str(lead.get("status") or "") in engaged]

    def _apply_outcome(self, match: LeadMatch, outcome: Outcome, message: Message) -> bool:
        """Record the event, and move the status when the outcome warrants it.

        Returns whether the status actually changed.
        """
        action = f"email:{outcome} ({match.confidence}) {message.subject[:80]}"
        try:
            self.repo.leads.record_event(match.job_id, action)
        except Exception as exc:  # noqa: BLE001
            _log.warning("could not record email event for %s: %s", match.job_id, exc)

        target = OUTCOME_TO_STATUS.get(outcome)
        if target is None:
            return False

        current = self._current_status(match.job_id)
        if current in TERMINAL_STATUSES:
            # Threads re-deliver old text and mail arrives out of order; nothing here should undo a
            # settled outcome.
            return False
        if current == "interviewing" and outcome is Outcome.INTERVIEW:
            return False

        try:
            self.repo.leads.update_lead_status(match.job_id, target)
            return True
        except (LookupError, ValueError) as exc:
            _log.warning("could not set %s on %s: %s", target, match.job_id, exc)
            return False

    def _current_status(self, job_id: str) -> str:
        try:
            lead = self.repo.leads.get_lead_by_id(job_id)
        except Exception:  # noqa: BLE001
            return ""
        return str((lead or {}).get("status") or "")


def create_email_monitor_service(repo=None, client_factory=EmailClient) -> EmailMonitorService:
    if repo is None:
        from data.repository import create_repository

        repo = create_repository()
    return EmailMonitorService(repo, client_factory)
