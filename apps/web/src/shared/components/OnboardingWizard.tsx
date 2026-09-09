import { useState } from "react";
import { motion } from "framer-motion";
import Icon from "./Icon";
import type { ApiFetch } from "../../types";

export function OnboardingWizard({ api, onFinish, onOpenSettings }: { api: ApiFetch; onFinish: (draft: string) => void; onOpenSettings: () => void }) {
  const [step, setStep] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [rawResume, setRawResume] = useState("");
  const [role, setRole] = useState("");
  const [location, setLocation] = useState("");
  const [remotePref, setRemotePref] = useState("any");
  const [market, setMarket] = useState("remote");
  const [provider, setProvider] = useState("ollama");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [ollamaUrl, setOllamaUrl] = useState("http://localhost:11434");
  const [jobDraft, setJobDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const steps = ["Your résumé", "Your preferences", "How it works", "Try a job"];
  const keyField: Record<string, string> = {
    openai: "openai_api_key",
    anthropic: "anthropic_key",
    opencode: "opencode_api_key",
    gemini: "gemini_api_key",
    groq: "groq_api_key",
    deepseek: "deepseek_api_key",
    nvidia: "nvidia_api_key",
    xai: "xai_api_key",
    kimi: "kimi_api_key",
    mistral: "mistral_api_key",
    together: "together_api_key",
    fireworks: "fireworks_api_key",
    cerebras: "cerebras_api_key",
    perplexity: "perplexity_api_key",
    huggingface: "huggingface_api_key",
  };
  const modelField: Record<string, string> = {
    openai: "openai_model",
    anthropic: "anthropic_model",
    opencode: "opencode_model",
    gemini: "gemini_model",
    groq: "groq_model",
    deepseek: "deepseek_model",
    nvidia: "nvidia_model",
    xai: "xai_model",
    kimi: "kimi_model",
    mistral: "mistral_model",
    together: "together_model",
    fireworks: "fireworks_model",
    cerebras: "cerebras_model",
    perplexity: "perplexity_model",
    huggingface: "huggingface_model",
  };
  const modelHints: Record<string, string[]> = {
    openai: ["gpt-4o-mini", "gpt-4o"],
    anthropic: ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
    opencode: ["muse-spark-1.3-contributor"],
    gemini: ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
    groq: ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "openai/gpt-oss-120b"],
    deepseek: ["deepseek-chat", "deepseek-reasoner"],
    nvidia: ["z-ai/glm-5.1", "meta/llama-3.1-70b-instruct"],
    xai: ["grok-4", "grok-3", "grok-3-mini"],
    kimi: ["kimi-k2-turbo-preview", "kimi-k2.5", "moonshot-v1-128k"],
    mistral: ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"],
    together: ["openai/gpt-oss-120b", "meta-llama/Llama-3.3-70B-Instruct-Turbo", "moonshotai/Kimi-K2-Instruct"],
    fireworks: ["accounts/fireworks/models/llama-v3p1-70b-instruct", "accounts/fireworks/models/qwen2p5-72b-instruct"],
    cerebras: ["llama-3.3-70b", "llama3.1-8b", "gpt-oss-120b"],
    perplexity: ["sonar", "sonar-pro", "sonar-reasoning"],
    huggingface: ["openai/gpt-oss-120b", "meta-llama/Llama-3.1-8B-Instruct"],
  };
  const providerNotes: Record<string, string> = {
    ollama: "Runs locally through your own Ollama server. Best for privacy; install models separately.",
    gemini: "Good default for fast, affordable tailoring. Uses Google's OpenAI-compatible Gemini endpoint.",
    groq: "Great for fast scouting, parsing, and drafts. Uses Groq's OpenAI-compatible endpoint.",
    openai: "Strong general default for generation and scoring if you already use OpenAI.",
    anthropic: "Strong writing and reasoning option for polished resumes and cover letters.",
    opencode: "OpenCode Zen / Go access with its own endpoint, request format, and reasoning settings.",
    deepseek: "Useful when you want lower-cost reasoning-style evaluation.",
    nvidia: "Advanced NIM route for users with NVIDIA API access.",
    xai: "Grok models through xAI's OpenAI-compatible endpoint.",
    kimi: "Moonshot/Kimi models through the OpenAI-compatible Kimi API.",
    mistral: "Mistral's hosted models; good European provider option.",
    together: "Open-source model hosting for Llama, DeepSeek, Kimi, Qwen, and more.",
    fireworks: "Fast open-source model hosting with OpenAI-compatible access.",
    cerebras: "Very fast inference route for supported models.",
    perplexity: "Search-grounded models, useful for research-style answers.",
    huggingface: "Hugging Face router for supported hosted inference providers.",
  };
  const tourPages = [
    { name: "Home", detail: "Describe the work you want, start a job search, and see the most useful next step." },
    { name: "Find jobs", detail: "Review jobs, filter by level and location, and hide anything that is not relevant." },
    { name: "Tailor & apply", detail: "Paste a job to create a truthful résumé and cover letter, then hand the application to the browser extension." },
    { name: "Applications", detail: "Keep track of what you submitted, follow-ups, interviews, rejections, and offers." },
    { name: "Your profile", detail: "Review the facts GalaxyHire uses when it matches jobs and prepares documents." },
    { name: "Add experience", detail: "Import a résumé, add projects, achievements, or notes." },
  ];

  const saveResume = async () => {
    if (!file && !rawResume.trim()) {
      setErr("Upload a resume file or paste resume text.");
      return;
    }
    setBusy(true);
    setErr(null);
    const fd = new FormData();
    if (file) fd.append("file", file);
    else fd.append("raw", rawResume.trim());
    try {
      const r = await api(`/api/v1/ingest`, { method: "POST", body: fd });
      if (!r.ok) {
        const detail = await r.json().then(d => d.detail).catch(() => "");
        throw new Error(detail || `Resume import returned ${r.status}`);
      }
      window.dispatchEvent(new CustomEvent("profile-refresh"));
      window.dispatchEvent(new CustomEvent("graph-refresh"));
      setStep(1);
    } catch (e) {
      const message = e instanceof Error ? e.message : "Resume import failed";
      setErr(message === "Failed to fetch" ? "Could not reach the local backend. Restart GalaxyHire and try again." : message);
    } finally {
      setBusy(false);
    }
  };

  const savePreferences = async () => {
    setBusy(true);
    setErr(null);
    const trimmedRole = role.trim();
    const payload: Record<string, any> = {
      job_market_focus: market,
      remote_preference: remotePref,
      llm_provider: provider,
      free_sources_enabled: true,
    };
    if (trimmedRole) {
      payload.onboarding_target_role = trimmedRole;
      payload.desired_position = trimmedRole;
    }
    // Optional: an explicit location overrides whatever the CV auto-detected.
    // Left blank, discovery uses the location parsed from the résumé.
    if (location.trim()) payload.job_location = location.trim();
    if (provider === "ollama") payload.ollama_url = ollamaUrl;
    if (provider === "opencode") {
      payload.opencode_base_url = "https://opencode.ai/zen/go/v1";
      payload.opencode_protocol = "openai";
      payload.opencode_endpoint_type = "responses";
      payload.opencode_reasoning_effort = "medium";
    }
    const field = keyField[provider];
    if (field && apiKey.trim()) payload[field] = apiKey.trim();
    const modelKey = modelField[provider];
    if (modelKey && model.trim()) payload[modelKey] = model.trim();
    try {
      const r = await api(`/api/v1/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) throw new Error(`Preferences returned ${r.status}`);
      setStep(2);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Preferences failed to save");
    } finally {
      setBusy(false);
    }
  };

  const progress = (
    <div className="row gap-2" style={{ flexWrap: "wrap" }}>
      {steps.map((label, idx) => (
        <button
          key={label}
          className="btn btn-ghost"
          onClick={() => idx <= step && setStep(idx)}
          style={{
            borderColor: idx === step ? "var(--accent)" : idx < step ? "var(--green)" : "var(--line)",
            background: idx === step ? "var(--accent-soft)" : idx < step ? "var(--green-soft)" : "var(--paper-3)",
            color: idx === step ? "var(--ink)" : idx < step ? "var(--green-ink)" : "var(--ink-3)",
            fontSize: 12,
            minHeight: 34,
          }}
        >
          {idx < step ? <Icon name="check" size={13} /> : <span className="mono">{idx + 1}</span>} {label}
        </button>
      ))}
    </div>
  );

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      style={{ position: "fixed", inset: 0, zIndex: 50, background: "rgba(var(--cream-rgb),0.94)", display: "grid", placeItems: "center", padding: 22 }}
    >
      <motion.section
        initial={{ y: 16, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 10, opacity: 0 }}
        className="card"
        style={{ width: "min(960px, 100%)", maxHeight: "min(760px, 94vh)", overflow: "auto", padding: 24, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 22 }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <div>
            <div className="eyebrow">Welcome to GalaxyHire</div>
            <h2 style={{ fontSize: 30, fontWeight: 800, marginTop: 6 }}>Set up your private job workspace</h2>
            <p style={{ color: "var(--ink-2)", fontSize: 13.5, lineHeight: 1.55, marginTop: 8 }}>
              Add your résumé, choose what you are looking for, and try one real job. You remain in control of every application.
            </p>
          </div>
          {progress}
          <div style={{ background: "var(--paper-3)", border: "1px solid var(--line)", borderRadius: 8, padding: 14, color: "var(--ink-2)", fontSize: 13, lineHeight: 1.55 }}>
            <b style={{ color: "var(--ink)" }}>{steps[step]}</b>
            <div style={{ marginTop: 4 }}>
              {step === 0 && "Your résumé gives GalaxyHire factual experience it can use. You can edit the imported profile afterward."}
              {step === 1 && "Tell GalaxyHire what you want and choose the AI service used for matching and document preparation."}
              {step === 2 && "Every page is part of the same local-first workflow: find jobs, understand fit, tailor, apply, and learn from outcomes."}
              {step === 3 && "Paste a real posting now, or go to Tailor & apply and add one later."}
            </div>
          </div>
          <button className="btn btn-ghost" onClick={() => onFinish("")} style={{ alignSelf: "flex-start" }}>
            Skip setup
          </button>
        </div>

        <div style={{ minWidth: 0 }}>
          {err && <div style={{ color: "var(--bad)", background: "var(--bad-soft)", border: "1px solid var(--bad)", borderRadius: 8, padding: "9px 11px", fontSize: 12, marginBottom: 12 }}>{err}</div>}

          {step === 0 && (
            <div className="col gap-4">
              <label className="card" style={{ padding: 18, cursor: "pointer", borderStyle: "dashed", background: "var(--paper)" }}>
                <input type="file" accept=".pdf,.docx,.txt,.md" style={{ display: "none" }} onChange={e => setFile(e.target.files?.[0] || null)} />
                <div className="row gap-3">
                  <Icon name="upload" size={20} />
                  <div>
                    <div style={{ fontWeight: 800 }}>{file ? file.name : "Upload resume"}</div>
                    <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 2 }}>PDF, DOCX, TXT, or Markdown</div>
                  </div>
                </div>
              </label>
              <textarea
                className="field-input"
                value={rawResume}
                onChange={e => setRawResume(e.target.value)}
                placeholder="Or paste resume text"
                rows={8}
                style={{ lineHeight: 1.55, resize: "vertical" }}
              />
              <button className="btn btn-accent" onClick={saveResume} disabled={busy} style={{ justifyContent: "center", padding: "12px 16px" }}>
                <Icon name="arrow-right" size={14} color="#fff" /> {busy ? "Importing..." : "Continue"}
              </button>
            </div>
          )}

          {step === 1 && (
            <div className="col gap-4">
              <div>
                <label className="eyebrow">Target role</label>
                <input className="field-input" value={role} onChange={e => setRole(e.target.value)} placeholder="e.g. Registered Nurse, Electrician, Backend Engineer, Chef" style={{ marginTop: 7 }} />
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div>
                  <label className="eyebrow">Location <span style={{ opacity: 0.6, textTransform: "none", fontWeight: 400 }}>(optional — auto-detected from your résumé)</span></label>
                  <input className="field-input" value={location} onChange={e => setLocation(e.target.value)} placeholder="e.g. Berlin, Toronto, Lagos, Mumbai" style={{ marginTop: 7 }} />
                </div>
                <div>
                  <label className="eyebrow">Work type</label>
                  <select className="field-input" value={remotePref} onChange={e => setRemotePref(e.target.value)} style={{ marginTop: 7 }}>
                    <option value="any">Any</option>
                    <option value="remote">Remote</option>
                    <option value="hybrid">Hybrid</option>
                    <option value="onsite">Onsite</option>
                  </select>
                </div>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div>
                  <label className="eyebrow">Market</label>
                  <select className="field-input" value={market} onChange={e => setMarket(e.target.value)} style={{ marginTop: 7 }}>
                    <option value="remote">Remote first</option>
                    <option value="india">India</option>
                    <option value="us">United States</option>
                    <option value="global">Global</option>
                  </select>
                </div>
                <div>
                  <label className="eyebrow">LLM Provider</label>
                  <select className="field-input" value={provider} onChange={e => { const next = e.target.value; setProvider(next); setApiKey(""); setModel(modelHints[next]?.[0] || ""); }} style={{ marginTop: 7 }}>
                    <option value="ollama">Ollama</option>
                    <option value="gemini">Gemini</option>
                    <option value="groq">Groq</option>
                    <option value="xai">Grok / xAI</option>
                    <option value="kimi">Kimi / Moonshot</option>
                    <option value="mistral">Mistral</option>
                    <option value="together">Together</option>
                    <option value="fireworks">Fireworks</option>
                    <option value="cerebras">Cerebras</option>
                    <option value="perplexity">Perplexity</option>
                    <option value="huggingface">Hugging Face</option>
                    <option value="openai">OpenAI</option>
                    <option value="anthropic">Anthropic</option>
                    <option value="opencode">OpenCode</option>
                    <option value="deepseek">DeepSeek</option>
                    <option value="nvidia">NVIDIA</option>
                  </select>
                </div>
              </div>
              <div style={{ background: "var(--paper-3)", border: "1px solid var(--line)", borderRadius: 8, padding: 12, fontSize: 12.5, lineHeight: 1.5, color: "var(--ink-2)" }}>
                <b style={{ color: "var(--ink)" }}>{provider === "ollama" ? "Local mode" : provider.toUpperCase()}</b>
                <div style={{ marginTop: 4 }}>{providerNotes[provider]}</div>
              </div>
              {provider === "ollama" ? (
                <div>
                  <label className="eyebrow">Ollama URL</label>
                  <input className="field-input" value={ollamaUrl} onChange={e => setOllamaUrl(e.target.value)} style={{ marginTop: 7 }} />
                </div>
              ) : (
                <div style={{ display: "grid", gap: 12 }}>
                  <div>
                    <label className="eyebrow">API key</label>
                    <input className="field-input" type="password" value={apiKey} onChange={e => setApiKey(e.target.value)} placeholder="Optional for now" style={{ marginTop: 7 }} />
                  </div>
                  <div>
                    <label className="eyebrow">Default model</label>
                    <select className="field-input" value={model} onChange={e => setModel(e.target.value)} style={{ marginTop: 7 }}>
                      {(modelHints[provider] || []).map(m => <option key={m} value={m}>{m}</option>)}
                    </select>
                  </div>
                </div>
              )}
              <div className="row gap-2" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
                <button className="btn" onClick={onOpenSettings}><Icon name="settings" size={13} /> Advanced settings</button>
                <button className="btn btn-accent" onClick={savePreferences} disabled={busy} style={{ minWidth: 170, justifyContent: "center" }}>
                  <Icon name="arrow-right" size={14} color="#fff" /> {busy ? "Saving..." : "Continue"}
                </button>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="col gap-4">
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 10 }}>
                {tourPages.map(page => (
                  <div key={page.name} style={{ border: "1px solid var(--line)", borderRadius: 8, background: "var(--paper)", padding: 12 }}>
                    <div style={{ fontWeight: 800, fontSize: 13 }}>{page.name}</div>
                    <div style={{ color: "var(--ink-2)", fontSize: 12, lineHeight: 1.45, marginTop: 5 }}>{page.detail}</div>
                  </div>
                ))}
              </div>
              <button className="btn btn-accent" onClick={() => setStep(3)} style={{ justifyContent: "center", padding: "12px 16px" }}>
                <Icon name="arrow-right" size={14} color="#fff" /> Continue
              </button>
            </div>
          )}

          {step === 3 && (
            <div className="col gap-4">
              <div>
                <label className="eyebrow">Job URL or description</label>
                <textarea className="field-input" value={jobDraft} onChange={e => setJobDraft(e.target.value)} rows={12} style={{ marginTop: 7, lineHeight: 1.55, resize: "vertical" }} />
              </div>
              <div className="row gap-2" style={{ flexWrap: "wrap" }}>
                <button className="btn btn-accent" onClick={() => onFinish(jobDraft)} disabled={!jobDraft.trim()} style={{ justifyContent: "center", padding: "12px 16px", flex: "1 1 220px" }}>
                  <Icon name="spark" size={14} color="#fff" /> Try it on this job
                </button>
                <button className="btn" onClick={() => onFinish("")} style={{ justifyContent: "center", flex: "1 1 220px" }}>
                  Open Customize
                </button>
              </div>
            </div>
          )}
        </div>
      </motion.section>
    </motion.div>
  );
}
