"""Email monitoring (§7).

Connect the address you apply from; the app watches it, works out which application each message is
about, and tracks the outcome — rejection, interview, assessment, offer — into the pipeline.

Deliberately deterministic and read-only: keyword rules rather than an LLM (the endpoint is optional
configuration, and a feature that stops working when it isn't set is worse than a blunter one), and
IMAP opened read-only with `BODY.PEEK` so nothing in the user's inbox is ever modified or marked read.
"""

from email_monitor.classify import (
    OUTCOME_TO_STATUS,
    LeadMatch,
    Outcome,
    OutcomeVerdict,
    classify_outcome,
    match_lead,
)
from email_monitor.client import EmailClient, EmailError, MailboxConfig, Message
from email_monitor.service import (
    EmailMonitorService,
    PollResult,
    create_email_monitor_service,
    load_config,
)

__all__ = [
    "OUTCOME_TO_STATUS",
    "EmailClient",
    "EmailError",
    "EmailMonitorService",
    "LeadMatch",
    "MailboxConfig",
    "Message",
    "Outcome",
    "OutcomeVerdict",
    "PollResult",
    "classify_outcome",
    "create_email_monitor_service",
    "load_config",
    "match_lead",
]
