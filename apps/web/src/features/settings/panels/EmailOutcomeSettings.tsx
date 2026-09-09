import { useEffect, useState } from "react";
import Icon from "../../../shared/components/Icon";
import type { ApiFetch } from "../../../types";
import { SectionLabel } from "./shared";

type EmailStatus = {
  configured: boolean;
  enabled: boolean;
  processed_count: number;
  mailbox: {
    host: string;
    port: number;
    username: string;
    mailbox: string;
    password_set: boolean;
  } | null;
};

type CheckResult = { ok?: boolean; error?: string };
type PollResult = {
  scanned?: number;
  matched?: number;
  updated?: number;
  error?: string | null;
};

const EMPTY_STATUS: EmailStatus = {
  configured: false,
  enabled: false,
  processed_count: 0,
  mailbox: null,
};

const PROVIDERS = [
  { label: "Gmail", host: "imap.gmail.com", port: 993 },
  { label: "Outlook / Microsoft 365", host: "outlook.office365.com", port: 993 },
  { label: "Fastmail", host: "imap.fastmail.com", port: 993 },
  { label: "Other", host: "", port: 993 },
];

export function EmailOutcomeSettings({ api }: { api: ApiFetch }) {
  const [status, setStatus] = useState<EmailStatus>(EMPTY_STATUS);
  const [host, setHost] = useState("");
  const [port, setPort] = useState("993");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [mailbox, setMailbox] = useState("INBOX");
  const [busy, setBusy] = useState<"" | "saving" | "testing" | "checking" | "disconnecting">("");
  const [message, setMessage] = useState<{ tone: "ok" | "bad" | "neutral"; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    api("/api/v1/email/status")
      .then(response => response.json())
      .then((next: EmailStatus) => {
        if (!alive) return;
        setStatus(next);
        if (next.mailbox) {
          setHost(next.mailbox.host);
          setPort(String(next.mailbox.port || 993));
          setUsername(next.mailbox.username);
          setMailbox(next.mailbox.mailbox || "INBOX");
        }
      })
      .catch(() => setMessage({ tone: "bad", text: "Could not load email monitoring settings." }));
    return () => { alive = false; };
  }, [api]);

  const chooseProvider = (value: string) => {
    const provider = PROVIDERS.find(item => item.label === value);
    if (!provider) return;
    setHost(provider.host);
    setPort(String(provider.port));
  };

  const save = async () => {
    if (!host.trim() || !username.trim() || (!password && !status.mailbox?.password_set)) {
      setMessage({ tone: "bad", text: "Enter the mail server, email address, and an app password." });
      return;
    }
    setBusy("saving");
    setMessage(null);
    try {
      const response = await api("/api/v1/email/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          host: host.trim(),
          port: Number(port) || 993,
          username: username.trim(),
          password: password || undefined,
          mailbox: mailbox.trim() || "INBOX",
          enabled: true,
        }),
      });
      if (!response.ok) throw new Error("Settings could not be saved.");
      const next = await response.json() as EmailStatus;
      setStatus(next);
      setPassword("");
      setMessage({ tone: "ok", text: "Saved. GalaxyHire will check for application updates every 20 minutes." });
    } catch (error) {
      setMessage({ tone: "bad", text: error instanceof Error ? error.message : "Settings could not be saved." });
    } finally {
      setBusy("");
    }
  };

  const test = async () => {
    setBusy("testing");
    setMessage(null);
    try {
      const response = await api("/api/v1/email/check", { method: "POST", timeoutMs: 45_000 });
      const result = await response.json() as CheckResult;
      setMessage(result.ok
        ? { tone: "ok", text: "Connection works. GalaxyHire has read-only access." }
        : { tone: "bad", text: result.error || "Could not connect to this mailbox." });
    } catch {
      setMessage({ tone: "bad", text: "Could not connect to this mailbox." });
    } finally {
      setBusy("");
    }
  };

  const checkNow = async () => {
    setBusy("checking");
    setMessage(null);
    try {
      const response = await api("/api/v1/email/poll", { method: "POST", timeoutMs: 60_000 });
      const result = await response.json() as PollResult;
      if (result.error) throw new Error(result.error);
      setMessage({
        tone: "ok",
        text: `Checked ${result.scanned || 0} recent messages and updated ${result.updated || 0} application${result.updated === 1 ? "" : "s"}.`,
      });
    } catch (error) {
      setMessage({ tone: "bad", text: error instanceof Error ? error.message : "Mailbox check failed." });
    } finally {
      setBusy("");
    }
  };

  const disconnect = async () => {
    setBusy("disconnecting");
    setMessage(null);
    try {
      const response = await api("/api/v1/email/disconnect", { method: "POST" });
      if (!response.ok) throw new Error("Could not disconnect.");
      setStatus(await response.json() as EmailStatus);
      setPassword("");
      setMessage({ tone: "neutral", text: "Email monitoring is off and the saved password was removed." });
    } catch {
      setMessage({ tone: "bad", text: "Could not disconnect." });
    } finally {
      setBusy("");
    }
  };

  return (
    <section className="gh-email-settings">
      <SectionLabel
        label="Application email updates"
        sub="Optionally watch the inbox you apply from and update application outcomes"
      />
      <div className="gh-privacy-note">
        <Icon name="check" size={15} />
        <span>
          GalaxyHire connects directly from this device using read-only IMAP. It never marks mail
          as read, sends mail, or uploads mailbox content.
        </span>
      </div>

      <div className="gh-email-status">
        <span className={status.enabled ? "connected" : ""} />
        <div>
          <strong>{status.enabled ? "Monitoring is on" : status.configured ? "Configured but paused" : "Not connected"}</strong>
          <small>
            {status.mailbox?.username || "Connect the email account you use for job applications"}
            {status.processed_count ? ` · ${status.processed_count} messages already checked` : ""}
          </small>
        </div>
      </div>

      <div className="gh-email-grid">
        <label>
          <span>Email provider</span>
          <select
            value={PROVIDERS.find(provider => provider.host === host)?.label || "Other"}
            onChange={event => chooseProvider(event.target.value)}
          >
            {PROVIDERS.map(provider => <option key={provider.label}>{provider.label}</option>)}
          </select>
        </label>
        <label>
          <span>Email address</span>
          <input value={username} onChange={event => setUsername(event.target.value)} placeholder="you@example.com" />
        </label>
        <label className="gh-email-wide">
          <span>App password</span>
          <input
            type="password"
            value={password}
            onChange={event => setPassword(event.target.value)}
            placeholder={status.mailbox?.password_set ? "Saved — leave blank to keep it" : "Use a provider app password, not your normal password"}
            autoComplete="new-password"
          />
          <small>Create a revocable app password in your email provider’s security settings.</small>
        </label>
        <label>
          <span>IMAP server</span>
          <input value={host} onChange={event => setHost(event.target.value)} placeholder="imap.example.com" />
        </label>
        <label>
          <span>Port</span>
          <input value={port} onChange={event => setPort(event.target.value)} inputMode="numeric" />
        </label>
        <label>
          <span>Folder</span>
          <input value={mailbox} onChange={event => setMailbox(event.target.value)} placeholder="INBOX" />
        </label>
      </div>

      {message && <div className={`gh-email-message ${message.tone}`}>{message.text}</div>}

      <div className="gh-email-actions">
        <button className="btn btn-accent" onClick={save} disabled={Boolean(busy)}>
          {busy === "saving" ? "Saving…" : status.configured ? "Save & enable" : "Connect inbox"}
        </button>
        <button className="btn" onClick={test} disabled={Boolean(busy) || !status.configured}>
          {busy === "testing" ? "Testing…" : "Test connection"}
        </button>
        <button className="btn" onClick={checkNow} disabled={Boolean(busy) || !status.enabled}>
          {busy === "checking" ? "Checking…" : "Check now"}
        </button>
        {status.configured && (
          <button className="btn danger-soft" onClick={disconnect} disabled={Boolean(busy)}>
            {busy === "disconnecting" ? "Disconnecting…" : "Disconnect"}
          </button>
        )}
      </div>
    </section>
  );
}
