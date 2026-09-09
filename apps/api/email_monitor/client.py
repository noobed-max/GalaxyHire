"""IMAP access to the inbox the user applies from (§7).

**Why IMAP and not OAuth.** OAuth needs a registered client ID and secret per provider, which a
local-first app with no hosted component cannot ship — anything baked into the binary is public, and
there is no server to hold it. IMAP with an *app-specific password* works on Gmail, Outlook,
Fastmail, and self-hosted mail alike, and an app password is revocable and scoped to mail, so a
leaked one cannot touch the rest of the account. Adding OAuth later is a per-provider job and does
not change anything below the `EmailClient` interface.

**Read-only, always.** Opened with `readonly=True` and never marked as seen: this feature reports on
the user's inbox, it does not manage it, and silently marking mail read would make the app appear to
lose messages the user hadn't looked at.
"""

from __future__ import annotations

import email
import imaplib
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

from core.logging import get_logger

_log = get_logger(__name__)

DEFAULT_PORT = 993
CONNECT_TIMEOUT_S = 30
# Enough to cover a poll interval plus a missed cycle; scanning the whole mailbox every poll would be
# wasteful and slow on a large inbox.
DEFAULT_LOOKBACK_DAYS = 14
MAX_MESSAGES = 200
# Bodies are only ever fed to keyword rules, so the tail of a long thread adds nothing but memory.
MAX_BODY_CHARS = 20_000


@dataclass
class MailboxConfig:
    host: str
    username: str
    password: str
    port: int = DEFAULT_PORT
    mailbox: str = "INBOX"
    use_ssl: bool = True

    def redacted(self) -> dict:
        """Safe to log or return over the API."""
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "mailbox": self.mailbox,
            "use_ssl": self.use_ssl,
            "password_set": bool(self.password),
        }


@dataclass
class Message:
    uid: str
    from_address: str
    subject: str
    body: str
    date: datetime | None = None
    to: str = ""


class EmailError(RuntimeError):
    """Connection or auth failure, with a message meant for the user."""


def _decode(value: str | None) -> str:
    """Decode RFC 2047 headers ("=?utf-8?B?...?=") into text.

    Recruiting mail is full of encoded subjects; leaving them raw would break every keyword rule in
    `classify.py`.
    """
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeDecodeError, LookupError, ValueError):
        return value


def _body_text(msg: email.message.Message) -> str:
    """Plain-text body, falling back to a crude de-tagging of HTML.

    Many recruiting systems send HTML only. Without the fallback those messages arrive with an empty
    body and classification sees nothing but the subject.
    """
    parts: list[str] = []
    html_parts: list[str] = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True)
        except (AssertionError, ValueError):
            continue
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        (parts if ctype == "text/plain" else html_parts).append(text)

    chosen = "\n".join(parts) if parts else _strip_html("\n".join(html_parts))
    return chosen[:MAX_BODY_CHARS]


def _strip_html(html: str) -> str:
    text = re.sub(r"<\s*(script|style)[^>]*>[\s\S]*?<\/\s*\1\s*>", " ", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


class EmailClient:
    def __init__(self, config: MailboxConfig):
        self.config = config

    @contextmanager
    def _connection(self):
        try:
            if self.config.use_ssl:
                conn = imaplib.IMAP4_SSL(self.config.host, self.config.port, timeout=CONNECT_TIMEOUT_S)
            else:
                conn = imaplib.IMAP4(self.config.host, self.config.port, timeout=CONNECT_TIMEOUT_S)
        except (OSError, imaplib.IMAP4.error) as exc:
            raise EmailError(f"Could not reach {self.config.host}:{self.config.port} — {exc}") from exc

        try:
            try:
                conn.login(self.config.username, self.config.password)
            except imaplib.IMAP4.error as exc:
                # The overwhelmingly common cause, so say it rather than echoing the server's terse
                # "AUTHENTICATIONFAILED".
                raise EmailError(
                    "Login rejected. Most providers require an app-specific password for IMAP "
                    "rather than your normal account password."
                ) from exc
            yield conn
        finally:
            try:
                conn.logout()
            except (OSError, imaplib.IMAP4.error):
                pass

    def check(self) -> dict:
        """Verify the connection works, for the Settings "test" button."""
        with self._connection() as conn:
            status, data = conn.select(self.config.mailbox, readonly=True)
            if status != "OK":
                raise EmailError(f"Mailbox {self.config.mailbox!r} could not be opened.")
            count = int(data[0]) if data and data[0] else 0
            return {"ok": True, "mailbox": self.config.mailbox, "messages": count}

    def recent(self, *, since_days: int = DEFAULT_LOOKBACK_DAYS, limit: int = MAX_MESSAGES) -> list[Message]:
        """Messages from the last `since_days`, newest first.

        Fetched read-only and never flagged. IMAP's SINCE granularity is a whole day, so this can
        return slightly more than asked for — which is harmless, because everything downstream is
        idempotent on message UID.
        """
        since = (datetime.now(UTC) - timedelta(days=max(1, since_days))).strftime("%d-%b-%Y")
        out: list[Message] = []
        with self._connection() as conn:
            status, _ = conn.select(self.config.mailbox, readonly=True)
            if status != "OK":
                raise EmailError(f"Mailbox {self.config.mailbox!r} could not be opened.")
            status, data = conn.search(None, "SINCE", since)
            if status != "OK":
                raise EmailError("Mailbox search failed.")
            uids = (data[0].split() if data and data[0] else [])[-limit:]

            for uid in reversed(uids):
                # BODY.PEEK leaves \Seen untouched; a plain BODY[] would mark the user's mail read.
                status, payload = conn.fetch(uid, "(BODY.PEEK[])")
                if status != "OK" or not payload or not isinstance(payload[0], tuple):
                    continue
                try:
                    msg = email.message_from_bytes(payload[0][1])
                except (ValueError, TypeError):
                    continue
                out.append(
                    Message(
                        uid=uid.decode() if isinstance(uid, bytes) else str(uid),
                        from_address=_decode(msg.get("From")),
                        subject=_decode(msg.get("Subject")),
                        body=_body_text(msg),
                        date=_parse_date(msg.get("Date")),
                        to=_decode(msg.get("To")),
                    )
                )
        return out


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
