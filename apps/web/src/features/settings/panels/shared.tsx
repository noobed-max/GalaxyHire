import type { ApiFetch } from "../../../types";

export interface Cfg {
  llm_provider: string;
  anthropic_key: string; anthropic_model: string; anthropic_base_url: string;
  openai_api_key: string; openai_model: string;
  deepseek_api_key: string; deepseek_model: string; gemini_api_key: string; gemini_model: string;
  groq_api_key: string; groq_model: string; nvidia_api_key: string;
  nvidia_model: string; xai_api_key: string; xai_model: string; kimi_api_key: string; kimi_model: string;
  mistral_api_key: string; mistral_model: string; openrouter_api_key: string; openrouter_model: string;
  together_api_key: string; together_model: string; fireworks_api_key: string; fireworks_model: string;
  cerebras_api_key: string; cerebras_model: string; perplexity_api_key: string; perplexity_model: string;
  huggingface_api_key: string; huggingface_model: string; cohere_api_key: string; cohere_model: string;
  sambanova_api_key: string; sambanova_model: string; qwen_api_key: string; qwen_model: string;
  azure_openai_api_key: string; azure_model: string; azure_openai_endpoint: string;
  custom_api_key: string; custom_model: string; custom_base_url: string;
  custom_protocol: string; custom_endpoint_type: string; custom_reasoning_effort: string;
  opencode_api_key: string; opencode_model: string; opencode_base_url: string;
  opencode_protocol: string; opencode_endpoint_type: string; opencode_reasoning_effort: string;
  ollama_url: string;
  claude_cli_model: string; codex_cli_model: string; gemini_cli_model: string; copilot_cli_model: string;
  scout_provider: string;     scout_api_key: string;     scout_model: string;
  evaluator_provider: string; evaluator_api_key: string; evaluator_model: string;
  generator_provider: string; generator_api_key: string; generator_model: string;
  ingestor_provider: string;  ingestor_api_key: string;  ingestor_model: string;
  actuator_provider: string;  actuator_api_key: string;  actuator_model: string;
  apify_token: string; apify_actor: string; linkedin_cookie: string; x_bearer_token: string; x_search_queries: string; x_watchlist: string;
  hunter_api_key: string; proxycurl_api_key: string; contact_lookup_enabled: string;
  x_max_requests_per_scan: string; x_max_results_per_query: string; x_min_signal_score: string; x_hot_lead_threshold: string; x_enable_notifications: string;
  free_sources_enabled: string; free_source_targets: string; company_watchlist: string; free_source_max_requests: string; free_source_min_signal_score: string;
  desired_position: string; onboarding_target_role: string; job_boards: string; job_market_focus: string;
  ghost_mode: string; auto_apply: string; headed_browser: string;
}

export const EMPTY: Cfg = {
  llm_provider: "ollama",
  anthropic_key: "", anthropic_model: "claude-sonnet-4-6", anthropic_base_url: "",
  openai_api_key: "", openai_model: "gpt-4o-mini",
  deepseek_api_key: "", deepseek_model: "deepseek-chat", gemini_api_key: "", gemini_model: "gemini-2.5-flash",
  groq_api_key: "", groq_model: "llama-3.3-70b-versatile", nvidia_api_key: "",
  nvidia_model: "z-ai/glm-5.1", xai_api_key: "", xai_model: "grok-4", kimi_api_key: "", kimi_model: "kimi-k2.6",
  mistral_api_key: "", mistral_model: "mistral-large-latest", openrouter_api_key: "", openrouter_model: "openrouter/auto",
  together_api_key: "", together_model: "openai/gpt-oss-120b", fireworks_api_key: "", fireworks_model: "accounts/fireworks/models/llama-v3p1-70b-instruct",
  cerebras_api_key: "", cerebras_model: "llama-3.3-70b", perplexity_api_key: "", perplexity_model: "sonar",
  huggingface_api_key: "", huggingface_model: "openai/gpt-oss-120b", cohere_api_key: "", cohere_model: "command-a-03-2025",
  sambanova_api_key: "", sambanova_model: "Meta-Llama-3.3-70B-Instruct", qwen_api_key: "", qwen_model: "qwen-plus",
  azure_openai_api_key: "", azure_model: "gpt-4o-mini", azure_openai_endpoint: "",
  custom_api_key: "", custom_model: "", custom_base_url: "",
  custom_protocol: "openai", custom_endpoint_type: "chat/completions", custom_reasoning_effort: "none",
  opencode_api_key: "", opencode_model: "muse-spark-1.3-contributor", opencode_base_url: "https://opencode.ai/zen/go/v1",
  opencode_protocol: "openai", opencode_endpoint_type: "responses", opencode_reasoning_effort: "medium",
  ollama_url: "http://localhost:11434/v1",
  claude_cli_model: "claude-sonnet-4-6", codex_cli_model: "", gemini_cli_model: "", copilot_cli_model: "",
  scout_provider: "", scout_api_key: "", scout_model: "",
  evaluator_provider: "", evaluator_api_key: "", evaluator_model: "",
  generator_provider: "", generator_api_key: "", generator_model: "",
  ingestor_provider: "", ingestor_api_key: "", ingestor_model: "",
  actuator_provider: "", actuator_api_key: "", actuator_model: "",
  apify_token: "", apify_actor: "", linkedin_cookie: "", x_bearer_token: "", x_search_queries: "", x_watchlist: "",
  hunter_api_key: "", proxycurl_api_key: "", contact_lookup_enabled: "true",
  x_max_requests_per_scan: "5", x_max_results_per_query: "50", x_min_signal_score: "60", x_hot_lead_threshold: "80", x_enable_notifications: "false",
  free_sources_enabled: "", free_source_targets: "", company_watchlist: "", free_source_max_requests: "20", free_source_min_signal_score: "60",
  desired_position: "", onboarding_target_role: "", job_boards: "", job_market_focus: "global",
  ghost_mode: "false", auto_apply: "false", headed_browser: "false",
};

export const PROVIDERS = [
  { id: "custom",    label: "OpenAI-compatible", tone: "pink",   sub: "Any compatible /v1 endpoint" },
  { id: "anthropic", label: "Anthropic",         tone: "purple", sub: "Claude" },
  { id: "opencode",  label: "OpenCode",          tone: "blue",   sub: "Zen / Go API" },
];

// Providers that use the user's own logged-in CLI subscription (no API key).
export const SUBSCRIPTION_PROVIDERS = new Set(["claude_cli", "codex_cli", "gemini_cli", "copilot_cli"]);
export const isSubscriptionProvider = (id: string) => SUBSCRIPTION_PROVIDERS.has(id);

export const MODEL_HINTS: Record<string, string[]> = {
  anthropic: ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
  opencode:  ["muse-spark-1.3-contributor"],
  custom:    [],
};

export const KEY_FIELD: Record<string, keyof Cfg> = {
  anthropic: "anthropic_key",
  custom: "custom_api_key",
  opencode: "opencode_api_key",
};

export const GLOBAL_MODEL_FIELD: Record<string, keyof Cfg> = {
  anthropic: "anthropic_model",
  custom: "custom_model",
  opencode: "opencode_model",
};

const SECRET_MASK = "__JHM_SECRET_SET__";
const LEGACY_BULLET_MASK = "\u2022".repeat(20);
const LEGACY_MOJIBAKE_BULLET_MASK = "\u00e2\u20ac\u00a2".repeat(20);
const LEGACY_DOUBLE_ENCODED_BULLET_MASK = "\u00c3\u00a2\u00e2\u201a\u00ac\u00c2\u00a2".repeat(20);

export const SECRET_MASKS = new Set([
  SECRET_MASK,
  LEGACY_BULLET_MASK,
  LEGACY_MOJIBAKE_BULLET_MASK,
  LEGACY_DOUBLE_ENCODED_BULLET_MASK,
]);

/* helpers */
export function LabelledField({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-2)" }}>{label}</span>
        {hint && <span style={{ fontSize: 11, color: "var(--ink-3)" }}>{hint}</span>}
      </div>
      {children}
    </div>
  );
}

export function SectionLabel({ label, sub }: { label: string; sub?: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
      <span style={{ fontSize: 13, fontWeight: 700 }}>{label}</span>
      {sub && <span style={{ fontSize: 11, color: "var(--ink-3)", fontFamily: "var(--font-mono)" }}>{sub}</span>}
    </div>
  );
}

export function ProviderPills({ value, onChange, small }: { value: string; onChange: (v: string) => void; small?: boolean }) {
  return (
    <div style={{ display: "flex", gap: small ? 5 : 7, flexWrap: "wrap" }}>
      {PROVIDERS.map(p => {
        const active = value === p.id;
        return (
          <button key={p.id} onClick={() => onChange(p.id)} style={{
            padding: small ? "5px 10px" : "10px 12px", borderRadius: small ? 8 : 11, cursor: "pointer",
            background: active ? `var(--${p.tone}-soft)` : "var(--card)",
            border: `1.5px solid ${active ? `var(--${p.tone})` : "var(--line)"}`,
            display: "flex", flexDirection: "column", alignItems: "center",
            gap: small ? 2 : 5, transition: "all .15s ease", minWidth: small ? 0 : 78,
          }}>
            <div style={{ fontSize: small ? 12 : 13, fontWeight: 600, color: active ? `var(--${p.tone}-ink)` : "var(--ink-2)" }}>
              {p.label}
            </div>
            {!small && <div style={{ fontFamily: "var(--font-mono)", fontSize: 9.5, color: "var(--ink-3)" }}>{p.sub}</div>}
          </button>
        );
      })}
    </div>
  );
}

/**
 * Free-text model field. Deliberately NOT a catalog picker: provider catalogs
 * list hundreds of models (OpenRouter alone 400+) and go stale; the only truth
 * that matters is the id your endpoint actually serves. Type it exactly —
 * `claude-sonnet-4-6`, `gpt-4o-mini`, `vendor/model-id`, anything.
 */
export function ModelChips({ provider, value, onChange }: {
  provider: string; value: string; onChange: (v: string) => void; api?: ApiFetch | null; cfg?: Cfg;
}) {
  const hints = MODEL_HINTS[provider] || [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <input
        type="text"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder="Model id — e.g. gpt-4o-mini, claude-sonnet-4-6, vendor/model-id"
        className="mono field-input"
        style={{ width: "100%", fontSize: 12 }}
      />
      {hints.length > 0 && (
        <div style={{ fontSize: 10.5, color: "var(--ink-3)", fontFamily: "var(--font-mono)" }}>
          common: {hints.join(" · ")}
        </div>
      )}
    </div>
  );
}

export function ApiKeyInput({ value, onChange, provider, isStep, disabled = false, placeholder, onDelete }: {
  value: string; onChange: (v: string) => void; provider: string; isStep?: boolean; disabled?: boolean; placeholder?: string; onDelete?: () => void;
}) {
  if (provider === "ollama" || isSubscriptionProvider(provider)) return null;
  const hasKey = Boolean(SECRET_MASKS.has(value) || (value && value.trim() !== ""));
  const ph: Record<string, string> = {
    anthropic: "sk-ant-****", gemini: "AIza****", groq: "gsk_****", nvidia: "nvapi-****",
    openai: "sk-****", deepseek: "sk-****", xai: "xai-****", kimi: "sk-****",
    mistral: "****", openrouter: "sk-or-****", together: "****", fireworks: "fw_****",
    cerebras: "csk-****", perplexity: "pplx-****", huggingface: "hf_****", cohere: "co_****",
    sambanova: "****", qwen: "sk-****", azure: "Azure OpenAI key", custom: "API key", opencode: "OpenCode API key",
  };

  const handleDelete = () => {
    onChange("");
    if (onDelete) onDelete();
  };

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, width: "100%" }}>
      <div style={{ flex: 1, position: "relative" }}>
        <input
          type="password"
          value={SECRET_MASKS.has(value) ? "" : value}
          onChange={e => onChange(e.target.value)}
          disabled={disabled}
          placeholder={placeholder || (isStep ? `API key for ${provider}` : ph[provider] || "API key")}
          className="mono field-input"
          style={{
            width: "100%",
            padding: "9px 12px",
            paddingRight: SECRET_MASKS.has(value) ? 80 : 12,
            borderRadius: 9,
            border: "1px solid var(--line)",
            background: disabled ? "var(--paper-3)" : "var(--card)",
            fontSize: 12,
            opacity: disabled ? 0.75 : 1,
            cursor: disabled ? "not-allowed" : "text",
          }}
        />
        {SECRET_MASKS.has(value) && (
          <span
            className="mono"
            style={{
              position: "absolute",
              right: 8,
              top: "50%",
              transform: "translateY(-50%)",
              fontSize: 10,
              fontWeight: 700,
              padding: "2px 7px",
              borderRadius: 999,
              background: "var(--green-soft)",
              color: "var(--green-ink)",
              border: "1px solid var(--green)",
              pointerEvents: "none",
            }}
          >
            Key set
          </span>
        )}
      </div>
      {hasKey && (
        <button
          type="button"
          onClick={handleDelete}
          title="Delete stored API key"
          className="btn"
          style={{
            fontSize: 11,
            padding: "8px 12px",
            borderRadius: 9,
            background: "var(--bad-soft)",
            color: "var(--bad)",
            border: "1px solid var(--bad)",
            cursor: "pointer",
            whiteSpace: "nowrap",
            fontWeight: 700,
          }}
        >
          Delete key
        </button>
      )}
    </div>
  );
}

export interface SubStatus {
  installed: boolean;
  logged_in: boolean;
  email?: string | null;
  plan?: string | null;
  install_hint?: { name: string; cmd: string; url: string; after?: string };
}

function subBadge(tone: string, text: React.ReactNode) {
  const s = tone === "bad"
    ? { background: "var(--bad-soft)", color: "var(--bad)", border: "1px solid var(--bad)" }
    : { background: `var(--${tone}-soft)`, color: `var(--${tone}-ink)`, border: `1px solid var(--${tone})` };
  return <span className="mono" style={{ alignSelf: "flex-start", fontSize: 10.5, padding: "3px 9px", borderRadius: 999, ...s }}>{text}</span>;
}

export function SubscriptionNote({ provider, status, onSignIn, busy }: {
  provider: string;
  status?: SubStatus;
  onSignIn?: () => void;
  busy?: boolean;
}) {
  const cli = ({ claude_cli: "claude", codex_cli: "codex", gemini_cli: "gemini", copilot_cli: "copilot" } as Record<string, string>)[provider] || provider;
  const plan = ({
    claude_cli: "Claude (Pro / Max)",
    codex_cli: "ChatGPT (Plus / Pro)",
    gemini_cli: "Google account / Gemini",
    copilot_cli: "GitHub Copilot",
  } as Record<string, string>)[provider] || "subscription";

  let inner: React.ReactNode;
  if (!status) {
    inner = subBadge("yellow", `Checking for the ${cli} CLI…`);
  } else if (!status.installed) {
    const h = status.install_hint;
    inner = (
      <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
        {subBadge("bad", `${cli} CLI not installed`)}
        {h && <>
          <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>Install it, then click Sign in:</div>
          <code style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, background: "var(--paper-3)", border: "1px solid var(--line)", borderRadius: 7, padding: "6px 9px", userSelect: "all" }}>{h.cmd}</code>
          <a href={h.url} target="_blank" rel="noreferrer" style={{ fontSize: 11.5, color: "var(--accent)" }}>installation guide ↗</a>
        </>}
      </div>
    );
  } else if (!status.logged_in) {
    inner = (
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        {subBadge("yellow", `${cli} CLI found — not signed in`)}
        <button className="btn" onClick={onSignIn} disabled={busy} style={{ fontSize: 12 }}>
          {busy ? "Opening sign-in…" : "Sign in"}
        </button>
        <span style={{ fontSize: 11, color: "var(--ink-3)" }}>opens a browser to your {plan} account</span>
      </div>
    );
  } else {
    inner = subBadge("green", `Signed in${status.email ? ` as ${status.email}` : ""}${status.plan ? ` · ${status.plan} plan` : ""} — ready`);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 9, padding: "11px 13px", borderRadius: 11, background: "var(--paper-2)", border: "1px solid var(--line)" }}>
      <div style={{ fontSize: 12, color: "var(--ink-2)", lineHeight: 1.5 }}>
        Runs on <b>your {plan} subscription</b> through the <span className="mono">{cli}</span> CLI — <b>no API key</b>. Your own local automation; usage draws from your plan, not per-token billing.
      </div>
      {inner}
    </div>
  );
}
