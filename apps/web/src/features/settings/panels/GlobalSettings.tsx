import { useEffect, useState } from "react";
import { settingsApi } from "../../../api/settings";
import type { Cfg } from "./shared";
import { ApiKeyInput, GLOBAL_MODEL_FIELD, KEY_FIELD, ModelChips, ProviderPills, SectionLabel } from "./shared";
import type { ApiFetch } from "../../../types";

type KeyStatus =
  | "ok"
  | "invalid_key"
  | "unreachable"
  | "not_configured"
  | "unchecked"
  | "model_not_found"
  | "endpoint_not_found"
  | "invalid_format"
  | "missing_model"
  | "rate_limited"
  | "server_error";

type ValidationResult = Record<
  string,
  {
    status: KeyStatus;
    latency_ms?: number;
    model?: string;
    reply?: string;
    detail?: string;
  }
>;

const ENDPOINT_PROVIDER_FIELDS = {
  custom: {
    baseUrl: "custom_base_url",
    apiKey: "custom_api_key",
    model: "custom_model",
    protocol: "custom_protocol",
    endpointType: "custom_endpoint_type",
    reasoningEffort: "custom_reasoning_effort",
    endpointLabel: "Compatible endpoint URL",
    endpointPlaceholder: "https://api.example.com/v1",
    defaultEndpointType: "chat/completions",
  },
  opencode: {
    baseUrl: "opencode_base_url",
    apiKey: "opencode_api_key",
    model: "opencode_model",
    protocol: "opencode_protocol",
    endpointType: "opencode_endpoint_type",
    reasoningEffort: "opencode_reasoning_effort",
    endpointLabel: "OpenCode endpoint URL",
    endpointPlaceholder: "https://opencode.ai/zen/go/v1",
    defaultEndpointType: "responses",
  },
} as const;

type EndpointProvider = keyof typeof ENDPOINT_PROVIDER_FIELDS;

function isEndpointProvider(provider: string): provider is EndpointProvider {
  return provider in ENDPOINT_PROVIDER_FIELDS;
}

function EndpointProviderSettings({ provider, cfg, onChange, api }: {
  provider: EndpointProvider;
  cfg: Cfg;
  onChange: (key: keyof Cfg, value: string) => void;
  api: ApiFetch;
}) {
  const fields = ENDPOINT_PROVIDER_FIELDS[provider];
  const protocol = cfg[fields.protocol] || "openai";
  const endpointType = cfg[fields.endpointType] || fields.defaultEndpointType;

  const updateProtocol = (nextProtocol: "openai" | "anthropic") => {
    onChange(fields.protocol, nextProtocol);
    if (nextProtocol === "anthropic" && endpointType !== "messages") {
      onChange(fields.endpointType, "messages");
    } else if (nextProtocol === "openai" && endpointType === "messages") {
      onChange(fields.endpointType, fields.defaultEndpointType);
    }
  };

  return (
    <>
      <div>
        <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>{fields.endpointLabel}</div>
        <input
          type="text"
          placeholder={fields.endpointPlaceholder}
          value={cfg[fields.baseUrl]}
          onChange={e => {
            const value = e.target.value;
            onChange(fields.baseUrl, value);
            const lower = value.toLowerCase();
            if (lower.includes("/responses")) {
              onChange(fields.endpointType, "responses");
              onChange(fields.protocol, "openai");
            } else if (lower.includes("/messages")) {
              onChange(fields.endpointType, "messages");
              onChange(fields.protocol, "anthropic");
            } else if (lower.includes("/chat/completions")) {
              onChange(fields.endpointType, "chat/completions");
              onChange(fields.protocol, "openai");
            }
          }}
          className="mono field-input"
          style={{ width: "100%", padding: "9px 12px", borderRadius: 9, border: "1px solid var(--line)", background: "var(--card)", fontSize: 12 }}
        />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div>
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>Endpoint Protocol</div>
          <div style={{ display: "flex", gap: 6 }}>
            {(["openai", "anthropic"] as const).map(option => {
              const active = protocol === option;
              return (
                <button
                  key={option}
                  type="button"
                  onClick={() => updateProtocol(option)}
                  className="btn"
                  style={{
                    flex: 1,
                    padding: "6px 12px",
                    fontSize: 12,
                    fontWeight: 600,
                    background: active ? "var(--purple-soft)" : "var(--card)",
                    borderColor: active ? "var(--purple)" : "var(--line)",
                    color: active ? "var(--purple-ink)" : "var(--ink-2)",
                  }}
                >
                  {option === "openai" ? "OpenAI" : "Anthropic"}
                </button>
              );
            })}
          </div>
        </div>

        <div>
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>Request Format</div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {(protocol === "anthropic"
              ? [{ id: "messages", label: "Messages" }]
              : [
                  { id: "chat/completions", label: "Chat" },
                  { id: "responses", label: "Responses" },
                ]
            ).map(format => {
              const active = endpointType === format.id;
              return (
                <button
                  key={format.id}
                  type="button"
                  onClick={() => onChange(fields.endpointType, format.id)}
                  className="btn"
                  style={{
                    flex: 1,
                    padding: "6px 10px",
                    fontSize: 11.5,
                    fontWeight: 600,
                    background: active ? "var(--accent-soft)" : "var(--card)",
                    borderColor: active ? "var(--accent)" : "var(--line)",
                    color: active ? "var(--accent)" : "var(--ink-2)",
                    whiteSpace: "nowrap",
                  }}
                >
                  {format.label}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div>
        <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>API Key</div>
        <ApiKeyInput
          value={cfg[fields.apiKey]}
          onChange={value => onChange(fields.apiKey, value)}
          onDelete={() => {
            onChange(fields.apiKey, "");
            settingsApi.deleteKey(api, fields.apiKey).catch(() => {});
          }}
          provider={provider}
        />
      </div>

      <div>
        <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>Model Name</div>
        <ModelChips provider={provider} value={cfg[fields.model]} onChange={value => onChange(fields.model, value)} api={api} cfg={cfg} />
      </div>

      <div>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Model Reasoning / Thinking</div>
          <div style={{ fontSize: 10.5, color: "var(--ink-3)", fontFamily: "var(--font-mono)" }}>
            {endpointType === "messages" ? "Anthropic thinking budget" : endpointType === "responses" ? "Responses reasoning effort" : "Chat reasoning_effort"}
          </div>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          {[
            { id: "none", label: "None (Standard)" },
            { id: "low", label: "Low" },
            { id: "medium", label: "Medium" },
            { id: "high", label: "High" },
          ].map(option => {
            const active = (cfg[fields.reasoningEffort] || "none") === option.id;
            return (
              <button
                key={option.id}
                type="button"
                onClick={() => onChange(fields.reasoningEffort, option.id)}
                className="btn"
                style={{
                  flex: 1,
                  padding: "5px 10px",
                  fontSize: 11.5,
                  fontWeight: 600,
                  background: active ? "var(--blue-soft)" : "var(--card)",
                  borderColor: active ? "var(--blue)" : "var(--line)",
                  color: active ? "var(--blue-ink)" : "var(--ink-2)",
                }}
              >
                {option.label}
              </button>
            );
          })}
        </div>
      </div>
    </>
  );
}

export function GlobalSettings({ cfg, set, onChange, prov, api }: { cfg: Cfg; set: (k: keyof Cfg) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => void; onChange: (k: keyof Cfg, v: string) => void; prov: string; api: ApiFetch }) {
  const [checking, setChecking] = useState(false);
  const [results, setResults] = useState<ValidationResult | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!results && !err) return;
    const timer = window.setTimeout(() => {
      setResults(null);
      setErr(null);
    }, 30000);
    return () => window.clearTimeout(timer);
  }, [results, err]);

  const checkKeys = async () => {
    setChecking(true);
    setErr(null);
    try {
      const r = await settingsApi.validate(api, cfg);
      if (!r.ok) throw new Error(`Server returned ${r.status}`);
      setResults(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Key validation failed");
    } finally {
      setChecking(false);
    }
  };
  const globalModelField = GLOBAL_MODEL_FIELD[prov];

  const badgeStyle = (status: KeyStatus) => {
    const isBad = ["invalid_key", "model_not_found", "endpoint_not_found", "invalid_format", "missing_model", "server_error"].includes(status);
    const isWarn = status === "rate_limited" || status === "unreachable";
    const tone = status === "ok" ? "green" : isBad ? "bad" : isWarn ? "yellow" : "paper";
    if (tone === "bad") return { background: "var(--bad-soft)", color: "var(--bad)", border: "1px solid var(--bad)" };
    if (tone === "paper") return { background: "var(--paper-3)", color: "var(--ink-3)", border: "1px solid var(--line)" };
    return { background: `var(--${tone}-soft)`, color: `var(--${tone}-ink)`, border: `1px solid var(--${tone})` };
  };

  const label = (status: KeyStatus) => ({
    ok: "ok",
    invalid_key: "invalid key",
    unreachable: "unreachable",
    not_configured: "not set",
    unchecked: "unchecked",
    model_not_found: "model not found",
    endpoint_not_found: "bad endpoint",
    invalid_format: "format error",
    missing_model: "missing model",
    rate_limited: "rate limited",
    server_error: "server error",
  }[status] || status);

  const resultEntries = results
    ? Object.entries(results).sort(([left], [right]) => {
      if (left === prov) return -1;
      if (right === prov) return 1;
      return left.localeCompare(right);
    })
    : [];

  return (
    <div>
      <SectionLabel label="Global Default" sub="fallback for any step not overridden" />
      <div style={{ padding: 16, borderRadius: 14, background: "var(--paper-2)", border: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 14 }}>
        <ProviderPills value={prov} onChange={v => onChange("llm_provider", v)} />

        {isEndpointProvider(prov) && <EndpointProviderSettings provider={prov} cfg={cfg} onChange={onChange} api={api} />}

        {!isEndpointProvider(prov) && (
          <>
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>API Key</div>
              <ApiKeyInput
                value={cfg[KEY_FIELD[prov]] as string}
                onChange={v => onChange(KEY_FIELD[prov], v)}
                onDelete={() => {
                  onChange(KEY_FIELD[prov], "");
                  settingsApi.deleteKey(api, String(KEY_FIELD[prov])).catch(() => {});
                }}
                provider={prov}
              />
            </div>

            {prov === "anthropic" && (
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>Custom Base URL (Optional)</div>
                <input
                  type="text"
                  placeholder="https://api.anthropic.com  ·  leave empty for official Anthropic"
                  value={cfg.anthropic_base_url || ""}
                  onChange={set("anthropic_base_url")}
                  className="mono field-input"
                  style={{ width: "100%", padding: "9px 12px", borderRadius: 9, border: "1px solid var(--line)", background: "var(--card)", fontSize: 12 }}
                />
              </div>
            )}

            {globalModelField && (
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>Global Model</div>
                <ModelChips provider={prov} value={cfg[globalModelField] as string} onChange={v => onChange(globalModelField, v)} api={api} cfg={cfg} />
              </div>
            )}
          </>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
          <button className="btn" onClick={checkKeys} disabled={checking} style={{ alignSelf: "flex-start", fontSize: 12 }}>
            {checking ? "Checking keys..." : "Check keys"}
          </button>
          {checking && (
            <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>Checking configured providers...</div>
          )}
          {err && <div style={{ fontSize: 12, color: "var(--bad)" }}>{err}</div>}
          {results && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
              {resultEntries.map(([provider, result]) => (
                <div
                  key={provider}
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 6,
                    padding: "10px 12px",
                    borderRadius: 10,
                    border: "1px solid var(--line)",
                    background: "var(--card)",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                    <span style={{ fontSize: 12.5, fontWeight: 700 }}>{provider}</span>
                    <span className="mono" style={{ fontSize: 10.5, padding: "2px 8px", borderRadius: 999, ...badgeStyle(result.status) }}>
                      {label(result.status)}{result.latency_ms ? ` · ${result.latency_ms}ms` : ""}
                    </span>
                  </div>
                  {result.model && (
                    <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      model: <span style={{ color: "var(--ink-1)", fontWeight: 600 }}>{result.model}</span>
                    </div>
                  )}
                  {result.reply && (
                    <div style={{ fontSize: 11, color: "var(--ink-2)", fontStyle: "italic", background: "var(--paper-2)", padding: "4px 8px", borderRadius: 6, border: "1px solid var(--line-subtle)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      "{result.reply}"
                    </div>
                  )}
                  {result.detail && result.status !== "ok" && (
                    <div style={{ fontSize: 11, color: "var(--bad)", lineHeight: 1.35, wordBreak: "break-word" }}>
                      {result.detail}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
