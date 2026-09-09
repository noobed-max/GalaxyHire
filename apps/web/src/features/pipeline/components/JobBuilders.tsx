import { useCallback, useEffect, useRef, useState } from "react";
import { openGeneratedDocument } from "../../../shared/lib/openExternal";
import Icon from "../../../shared/components/Icon";
import type { ApiFetch, KeywordCoverage, Lead } from "../../../types";
import { isAbortLikeError } from "../../../api/client";
import { GENERATION_TIMEOUT_MS } from "../../../api/generation";
import type { DocSelection } from "../../../api/docPane";
import { deduplicateSelection, isEmptySelection } from "../doc-pane/selectionState";
import type { PointSuggestion } from "../doc-pane/Pickers";
import { DocPane } from "../doc-pane/DocPane";

/**
 * Resume + cover letter builders for one job — the left half of the old ApprovalDrawer,
 * lifted verbatim so cart and (previously) drawer share one implementation.
 *
 * Everything here is per-job: tag-scoped doc selection, template pick, Build/Package
 * mode, PDF preview, version history, generate + full-pipeline runs. Details, feedback
 * and follow-up live in JobDetailsPanel; applying lives in ApplyHandoff.
 */
export function JobBuilders({ j: initialLead, api }: {
  j: Lead; api: ApiFetch;
}) {
  type DocKind = "resume" | "cover";
  type VersionEntry = { version: number; resume?: string; cover_letter?: string };
  const [generating, setGenerating] = useState(false);
  const [activeDoc, setActiveDoc] = useState<DocKind>("resume");
  const [pdfBlobUrl, setPdfBlobUrl] = useState<string | null>(null);
  const [pdfLoadErr, setPdfLoadErr] = useState<string | null>(null);
  const [pdfPreviewAttempt, setPdfPreviewAttempt] = useState(0);
  const [generateErr, setGenerateErr] = useState<string | null>(null);
  const [versions, setVersions] = useState<VersionEntry[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [versionErr, setVersionErr] = useState<string | null>(null);
  const [generatedLead, setGeneratedLead] = useState<Lead | null>(null);
  type TemplateOption = { id: string; name: string; is_default: boolean };
  const [templates, setTemplates] = useState<TemplateOption[]>([]);
  const [templateId, setTemplateId] = useState<string>("");

  // Profile/tag scoping: pick the profile this application targets, then use
  // (select) or regenerate from the documents filed under it.
  type Tag = { id: string; name: string };
  type Doc = { id: string; kind: "resume" | "cover_letter"; topic: string; tag_id: string | null; tag_name?: string };
  const [tags, setTags] = useState<Tag[]>([]);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [profileTag, setProfileTag] = useState<string>("");
  const [selectBusy, setSelectBusy] = useState(false);
  const [selectErr, setSelectErr] = useState<string | null>(null);
  const tagDocs = profileTag ? docs.filter(d => d.tag_id === profileTag) : docs;
  const tagResumes = tagDocs.filter(d => d.kind === "resume");
  const tagCoverLetters = tagDocs.filter(d => d.kind === "cover_letter");

  // AI pick ("Generate Resume"): the model chooses evidence + proposes ATS rewrites.
  // Applied into DocPane by token; suggestions render beside their target point.
  const [suggesting, setSuggesting] = useState(false);
  const [suggestErr, setSuggestErr] = useState<string | null>(null);
  const [suggestToken, setSuggestToken] = useState(0);
  const [suggestSelection, setSuggestSelection] = useState<Partial<DocSelection> | null>(null);
  const [suggestItems, setSuggestItems] = useState<PointSuggestion[]>([]);

  // Contact-line toggles (My-Resume-Maker socials): which identity fields may appear.
  type ContactKind = "email" | "phone" | "linkedin" | "github" | "website";
  const [identity, setIdentity] = useState<Record<string, unknown>>({});
  const [contactOn, setContactOn] = useState<Record<ContactKind, boolean>>({
    email: true, phone: true, linkedin: true, github: true, website: true,
  });
  const [showLocation, setShowLocation] = useState(true);
  useEffect(() => {
    api("/api/v1/profile").then(r => r.json()).then(d => {
      const id = (d && typeof d === "object" && (d as { identity?: unknown }).identity) || {};
      if (id && typeof id === "object") setIdentity(id as Record<string, unknown>);
    }).catch(() => {});
  }, [api]);
  const availableContacts: { kind: ContactKind; label: string; value: string }[] = [
    identity.email ? { kind: "email", label: "Email", value: String(identity.email) } : null,
    identity.phone ? { kind: "phone", label: "Phone", value: String(identity.phone) } : null,
    identity.linkedin_url ? { kind: "linkedin", label: "LinkedIn", value: String(identity.linkedin_url) } : null,
    identity.github_url ? { kind: "github", label: "GitHub", value: String(identity.github_url) } : null,
    identity.website_url ? { kind: "website", label: "Website", value: String(identity.website_url) } : null,
  ].filter((x): x is { kind: ContactKind; label: string; value: string } => x !== null);
  const hasCity = Boolean(identity.city);

  // Left pane mode: hand-build the resume content, or review the generated
  // package. Leads that already have assets open in package view.

  useEffect(() => {
    api("/api/v1/tags").then(r => r.json()).then(d => setTags(d.tags || [])).catch(() => {});
    api("/api/v1/documents").then(r => r.json()).then(d => setDocs(d.documents || [])).catch(() => {});
  }, [api]);

  const attachDocument = async (doc: Doc) => {
    setSelectBusy(true);
    setSelectErr(null);
    try {
      const body = doc.kind === "resume"
        ? { resume_document_id: doc.id }
        : { cover_letter_document_id: doc.id };
      const r = await api(`/api/v1/leads/${j.job_id}/select-document`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body), timeoutMs: GENERATION_TIMEOUT_MS,
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `Server returned ${r.status}`);
      if (data.lead) setGeneratedLead(data.lead as Lead);
      window.dispatchEvent(new CustomEvent("leads-refresh"));
    } catch (err) {
      setSelectErr(err instanceof Error ? err.message : String(err));
    } finally {
      setSelectBusy(false);
    }
  };

  const generateControllerRef = useRef<AbortController | null>(null);
  const j = generatedLead ? { ...initialLead, ...generatedLead } : initialLead;

  useEffect(() => () => {
    generateControllerRef.current?.abort();
  }, []);

  // Keep the generate-time snapshot (generatedLead) in sync with live status /
  // feedback updates. generatedLead's fields spread LAST into `j`, so without this
  // the builders mask fresh updates. Mirrors ApplyJobView's onLeadUpdated. No-op
  // when generatedLead is null (j is then initialLead, kept fresh by the parent).
  useEffect(() => {
    if (!initialLead?.job_id) return;
    const onLeadUpdated = (event: Event) => {
      const updated = (event as CustomEvent<Lead>).detail;
      if (updated?.job_id === initialLead.job_id) {
        setGeneratedLead(prev => (prev ? { ...prev, ...updated } : prev));
      }
    };
    window.addEventListener("lead-updated", onLeadUpdated);
    return () => window.removeEventListener("lead-updated", onLeadUpdated);
  }, [initialLead?.job_id]);

  // Load the saved resume templates so the user can pick which one this job's
  // resume should mimic. Defaults to the template marked default (empty id ->
  // backend resolves default -> legacy setting -> built-in layout).
  useEffect(() => {
    let alive = true;
    api("/api/v1/templates")
      .then(r => r.json())
      .then(d => {
        if (!alive) return;
        const items: TemplateOption[] = d.templates || [];
        setTemplates(items);
        const fallback = items.find(t => t.is_default) || items[0];
        if (fallback) setTemplateId(prev => prev || fallback.id);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [api]);

  const resumeReady = Boolean(j.resume_asset || j.asset);
  const coverReady = Boolean(j.cover_letter_asset);
  const currentVersion = versions[0]?.version ?? j.resume_version ?? null;
  const selectedVersionRecord = selectedVersion
    ? versions.find(v => v.version === selectedVersion)
    : null;
  const activeReady = selectedVersionRecord
    ? Boolean(activeDoc === "resume" ? selectedVersionRecord.resume : selectedVersionRecord.cover_letter)
    : activeDoc === "resume" ? resumeReady : coverReady;
  const activeDocPath = activeReady
    ? `/api/v1/leads/${j.job_id}/pdf?kind=${activeDoc === "resume" ? "resume" : "cover_letter"}${selectedVersionRecord ? `&version=${selectedVersionRecord.version}` : ""}`
    : null;
  const selectedProjects = j.selected_projects || [];
  const coverage = (j.keyword_coverage || j.source_meta?.keyword_coverage || {}) as KeywordCoverage;
  const missingTerms: string[] = Array.isArray(coverage.missing_terms) ? coverage.missing_terms : [];
  const incorporatedTerms: string[] = Array.isArray(coverage.incorporated_terms) ? coverage.incorporated_terms : [];
  const coveredTerms: string[] = Array.isArray(coverage.covered_terms) ? coverage.covered_terms : [];
  const coveragePct = typeof coverage.coverage_pct === "number" ? coverage.coverage_pct : null;
  const keywordVerification = coverage.verification?.status;
  const hasCoverage = missingTerms.length > 0 || incorporatedTerms.length > 0 || coveredTerms.length > 0;
  const visibleGenerateErr = generateErr && !/request\s+cancel(?:led|ed)|abort/i.test(generateErr)
    ? generateErr
    : null;

  const loadVersions = useCallback(async (signal?: AbortSignal) => {
    setVersionErr(null);
    try {
      const r = await api(`/api/v1/leads/${j.job_id}/versions`, { signal });
      if (!r.ok) throw new Error(`Server returned ${r.status}`);
      const items = await r.json() as VersionEntry[];
      setVersions(items);
      setSelectedVersion(prev => {
        if (prev && items.some(item => item.version === prev)) return prev;
        return items[0]?.version ?? null;
      });
    } catch (err) {
      setVersionErr(err instanceof Error ? err.message : "Version history failed to load");
    }
  }, [api, j.job_id]);

  const refreshLead = useCallback(async (signal?: AbortSignal) => {
    const r = await api(`/api/v1/leads/${initialLead.job_id}`, { signal });
    if (!r.ok) throw new Error(`Lead refresh returned ${r.status}`);
    const lead = await r.json() as Lead;
    setGeneratedLead(lead);
    return lead;
  }, [api, initialLead.job_id]);

  useEffect(() => {
    const controller = new AbortController();
    loadVersions(controller.signal);
    return () => controller.abort();
  }, [loadVersions, j.resume_asset, j.cover_letter_asset, j.resume_version]);

  // Tauri WebView blocks localhost iframe previews, so fetch the PDF as a blob.
  useEffect(() => {
    if (!activeDocPath) { setPdfBlobUrl(null); setPdfLoadErr(null); return; }
    let revoke: string | null = null;
    let alive = true;
    const controller = new AbortController();
    const previewTimer = window.setTimeout(() => {
      if (!alive) return;
      controller.abort();
      setPdfLoadErr("PDF preview timed out. The package exists, but the embedded preview did not respond.");
      setPdfBlobUrl(null);
    }, 12000);
    setPdfLoadErr(null);
    setPdfBlobUrl(null);
    api(activeDocPath, { signal: controller.signal, timeoutMs: 12000 })
      .then(r => { if (!r.ok) throw new Error(`Server returned ${r.status}`); return r.blob(); })
      .then(blob => {
        if (!alive) return;
        if (!blob.size) throw new Error("Generated PDF was empty");
        window.clearTimeout(previewTimer);
        const url = URL.createObjectURL(blob);
        revoke = url;
        setPdfBlobUrl(url);
      })
      .catch(err => {
        if (!alive) return;
        if (isAbortLikeError(err)) return;
        setPdfLoadErr(String(err));
        setPdfBlobUrl(null);
      });
    return () => {
      alive = false;
      window.clearTimeout(previewTimer);
      controller.abort();
      if (revoke) URL.revokeObjectURL(revoke);
    };
  }, [activeDocPath, api, pdfPreviewAttempt]);

  // Clear generating flag when the lead actually receives its generated documents.
  useEffect(() => {
    if (generating && resumeReady && coverReady) setGenerating(false);
  }, [resumeReady, coverReady, generating]);

  const generatePdf = async (rawSelection?: DocSelection) => {
    if (generating) return;
    const selection = rawSelection ? deduplicateSelection(rawSelection) : undefined;
    setGenerating(true);
    setGenerateErr(null);
    setPdfBlobUrl(null);
    setPdfLoadErr(null);
    setActiveDoc("resume");
    generateControllerRef.current?.abort();
    const controller = new AbortController();
    generateControllerRef.current = controller;
    try {
      const params = new URLSearchParams();
      if (templateId) params.set("template_id", templateId);
      if (profileTag) params.set("tag_id", profileTag);
      const kinds = (Object.keys(contactOn) as ContactKind[]).filter(k => contactOn[k]);
      for (const k of kinds) params.append("contact", k);
      params.set("show_location", showLocation ? "true" : "false");
      const qs = params.toString();
      const payload = selection && !isEmptySelection(selection) ? JSON.stringify({ selection }) : null;
      const r = await api(`/api/v1/leads/${j.job_id}/generate${qs ? `?${qs}` : ""}`, {
        method: "POST",
        signal: controller.signal,
        timeoutMs: GENERATION_TIMEOUT_MS,
        headers: payload ? { "Content-Type": "application/json" } : undefined,
        body: payload,
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `Server returned ${r.status}`);
      if (body.lead) setGeneratedLead(body.lead as Lead);
      await refreshLead(controller.signal).catch(() => null);
      window.dispatchEvent(new CustomEvent("leads-refresh"));
      await loadVersions();
      setPdfPreviewAttempt(n => n + 1);
    } catch (err) {
      if (controller.signal.aborted || isAbortLikeError(err)) {
        setGenerating(false);
        return;
      }
      setGenerateErr(err instanceof Error ? err.message : String(err));
      setGenerating(false);
    } finally {
      if (generateControllerRef.current === controller) generateControllerRef.current = null;
    }
  };


  const suggestPicks = async () => {
    if (suggesting || generating) return;
    setSuggesting(true);
    setSuggestErr(null);
    try {
      const qs = profileTag ? `?tag_id=${encodeURIComponent(profileTag)}` : "";
      const r = await api(`/api/v1/leads/${j.job_id}/suggest-selection${qs}`, { timeoutMs: GENERATION_TIMEOUT_MS });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `Server returned ${r.status}`);
      setSuggestSelection({
        skills_on: body.skills_on || [],
        experience_on: body.experience_on || [],
        projects_on: body.projects_on || [],
        points_on: body.points_on || [],
      });
      setSuggestItems(Array.isArray(body.suggestions) ? body.suggestions : []);
      setSuggestToken(t => t + 1);
    } catch (err) {
      setSuggestErr(err instanceof Error ? err.message : "AI selection failed");
    } finally {
      setSuggesting(false);
    }
  };

  const openPdf = () => { if (pdfBlobUrl) openGeneratedDocument(pdfBlobUrl); };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, minHeight: 0 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <div>
          <div className="eyebrow">Application Package</div>
          <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 3 }}>Resume and cover letter are generated separately for this role.</div>
        </div>
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <select
            value={profileTag}
            onChange={e => setProfileTag(e.target.value)}
            disabled={generating}
            title="Profile / tag — scopes which resume & cover letter data is used"
            style={{
              padding: "5px 10px", borderRadius: 0, fontSize: 11, fontWeight: 700,
              border: "2px solid var(--hard)", background: profileTag ? "var(--yellow)" : "var(--paper)", color: "var(--ink)",
              cursor: generating ? "not-allowed" : "pointer", maxWidth: 200,
            }}
          >
            <option value="">Profile / tag: any</option>
            {tags.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
          {templates.length > 0 && (
            <select
              value={templateId}
              onChange={e => setTemplateId(e.target.value)}
              disabled={generating}
              title="Resume template to mimic for this job"
              style={{
                padding: "5px 10px", borderRadius: 8, fontSize: 11, fontWeight: 700,
                border: "1px solid var(--line)", background: "var(--paper)", color: "var(--ink-2)",
                cursor: generating ? "not-allowed" : "pointer", maxWidth: 220,
              }}
            >
              {templates.map(t => (
                <option key={t.id} value={t.id}>{t.name}{t.is_default ? " (default)" : ""}</option>
              ))}
            </select>
          )}
          {pdfBlobUrl && (
            <button onClick={openPdf} title="Open PDF in system viewer" style={{
              display: "flex", alignItems: "center", gap: 5,
              padding: "5px 12px", borderRadius: 8, fontSize: 11, fontWeight: 700,
              border: "1px solid var(--teal)", background: "var(--teal-soft)", color: "var(--teal)", cursor: "pointer",
            }}>
              <Icon name="download" size={12} color="var(--teal)" /> Open PDF
            </button>
          )}
        </div>
      </div>
      {(tagResumes.length > 0 || tagCoverLetters.length > 0) && (
        <div className="card col gap-2" style={{ padding: 12, background: "var(--paper-2)" }}>
          <div className="eyebrow">{profileTag ? `Filed under profile: ${tags.find(t => t.id === profileTag)?.name ?? ""}` : "Your uploaded documents"}</div>
          <div style={{ fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.5 }}>
            Use one as-is for this application, or Generate below to rebuild from your profile{profileTag ? " (your own cover letter becomes the rewrite base)" : ""}.
          </div>
          <div className="docs-grid">
            {[...tagResumes, ...tagCoverLetters].map(d => (
              <div key={d.id} className="doc-box card" style={{ padding: 10, minHeight: 0 }}>
                <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 6 }}>
                  <span className={`pill mono ${d.kind === "resume" ? "doc-kind-resume" : "doc-kind-cl"}`} style={{ fontSize: 9 }}>
                    {d.kind === "resume" ? "RESUME" : "COVER"}
                  </span>
                </div>
                <div style={{ fontWeight: 700, fontSize: 12, marginTop: 6, lineHeight: 1.3, wordBreak: "break-word" }}>{d.topic}</div>
                <button className="btn btn-primary" style={{ marginTop: 8, fontSize: 11, padding: "4px 10px" }}
                  disabled={selectBusy || generating}
                  onClick={() => attachDocument(d)}>
                  {selectBusy ? "Attaching…" : "Use for this job"}
                </button>
              </div>
            ))}
          </div>
          {selectErr && <div style={{ color: "var(--bad)", fontSize: 12 }}>{selectErr}</div>}
        </div>
      )}
      <div className="row gap-2" style={{ background: "var(--paper-3)", padding: 5, borderRadius: 10, flexShrink: 0 }}>
        {[
          ["resume", "Resume", resumeReady],
          ["cover", "Cover Letter", coverReady],
        ].map(([kind, label, ready]) => (
          <button key={kind as string} onClick={() => setActiveDoc(kind as DocKind)} style={{
            flex: 1, padding: "8px 10px", borderRadius: 7, border: "none", cursor: "pointer",
            background: activeDoc === kind ? "var(--card)" : "transparent",
            color: activeDoc === kind ? "var(--ink)" : "var(--ink-3)",
            fontSize: 12, fontWeight: 700, boxShadow: activeDoc === kind ? "var(--shadow-xs)" : "none",
            display: "flex", justifyContent: "center", alignItems: "center", gap: 7,
          }}>
            {label}
            <span className="dot" style={{ color: ready ? "var(--ok)" : "var(--ink-4)" }} />
          </button>
        ))}
      </div>
      {activeDoc === "resume" ? (
        <>
          {(availableContacts.length > 0 || hasCity) && (
            <div className="card col gap-2" style={{ padding: 10, background: "var(--paper-2)" }}>
              <div className="eyebrow">Contact line — pick what appears on the resume</div>
              <div className="row gap-2" style={{ flexWrap: "wrap" }}>
                {availableContacts.map(c => (
                  <label key={c.kind} className="row" style={{ alignItems: "center", gap: 5, cursor: "pointer", fontSize: 11.5 }} title={c.value}>
                    <input
                      type="checkbox"
                      checked={contactOn[c.kind]}
                      onChange={() => setContactOn(prev => ({ ...prev, [c.kind]: !prev[c.kind] }))}
                    />
                    {c.label}
                  </label>
                ))}
                {hasCity && (
                  <label className="row" style={{ alignItems: "center", gap: 5, cursor: "pointer", fontSize: 11.5 }} title={String(identity.city)}>
                    <input type="checkbox" checked={showLocation} onChange={() => setShowLocation(v => !v)} />
                    Location
                  </label>
                )}
              </div>
            </div>
          )}
          <div className="row gap-2" style={{ flexWrap: "wrap" }}>
            <button onClick={() => suggestPicks()} disabled={suggesting || generating} style={{
              padding: "8px 18px", borderRadius: 8, fontSize: 12, fontWeight: 800,
              border: "1px solid var(--purple)", background: "var(--purple-soft)", color: "var(--purple-ink)",
              cursor: suggesting || generating ? "wait" : "pointer",
            }}>{suggesting ? "Asking AI…" : "Generate Resume"}</button>
            <button onClick={() => generatePdf()} disabled={generating || suggesting} style={{
              padding: "8px 18px", borderRadius: 8, fontSize: 12, fontWeight: 700,
              border: "1px solid var(--line)", background: "var(--paper)", color: "var(--ink-2)",
              cursor: generating || suggesting ? "not-allowed" : "pointer",
            }}>{generating ? "Rendering…" : "Render PDF"}</button>
          </div>
          <div style={{ fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.5 }}>
            Generate Resume asks the AI to pick this job's evidence and propose ATS wording
            improvements — review the picks and suggestions below, edit anything, then Render PDF.
          </div>
          {suggestErr && <div style={{ color: "var(--bad)", fontSize: 12 }}>{suggestErr}</div>}
          <DocPane
            j={j}
            api={api}
            tags={tags}
            profileTag={profileTag}
            generating={generating}
            onGenerateWithSelection={sel => generatePdf(sel)}
            suggestRequest={suggestToken > 0 ? { token: suggestToken, selection: suggestSelection || {}, suggestions: suggestItems } : null}
          />
        </>
      ) : (
        <>
          <div style={{ fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.5 }}>
            {profileTag && tagCoverLetters.length > 0
              ? "Your own cover letter becomes the rewrite base — retargeted, never reinvented."
              : "Written fresh for this role from your profile and the job description."}
          </div>
          <div className="row gap-2" style={{ flexWrap: "wrap" }}>
            <button onClick={() => { setActiveDoc("cover"); generatePdf(); }} disabled={generating} style={{
              padding: "8px 18px", borderRadius: 8, fontSize: 12, fontWeight: 800,
              border: "1px solid var(--purple)", background: "var(--purple-soft)", color: "var(--purple-ink)",
              cursor: generating ? "wait" : "pointer",
            }}>{generating ? "Generating..." : coverReady ? "Regenerate Cover Letter" : "Generate Cover Letter"}</button>
          </div>
        </>
      )}
      {versions.length > 1 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          <div className="eyebrow">Version history</div>
          <select
            className="field-input"
            value={selectedVersion ?? ""}
            onChange={e => setSelectedVersion(Number(e.target.value))}
            style={{ fontSize: 12, padding: "8px 10px" }}
          >
            {versions.map(version => (
              <option key={version.version} value={version.version}>
                v{version.version}{version.version === currentVersion ? " (current)" : ""}
              </option>
            ))}
          </select>
        </div>
      )}
      {versionErr && <div style={{ color: "var(--bad)", fontSize: 12 }}>{versionErr}</div>}
      {selectedProjects.length > 0 && (
        <div className="row gap-2" style={{ flexWrap: "wrap" }}>
          <span className="eyebrow" style={{ marginRight: 2 }}>Projects used</span>
          {selectedProjects.map((p, i) => (
            <span key={i} className="pill" style={{ background: "var(--green-soft)", color: "var(--green-ink)", border: "1px solid var(--green)" }}>{p}</span>
          ))}
        </div>
      )}
      {hasCoverage && (
        <div style={{ background: "var(--blue-soft)", border: "1px solid var(--blue)", borderRadius: 10, padding: "10px 12px" }}>
          <div className="row" style={{ justifyContent: "space-between", gap: 10, alignItems: "center", marginBottom: 7 }}>
            <span className="eyebrow" style={{ color: "var(--blue-ink)" }}>Coverage</span>
            <div className="row gap-2">
              {keywordVerification && (
                <span className="mono" style={{ fontSize: 9.5, fontWeight: 800, color: keywordVerification === "llm_verified" ? "var(--green-ink)" : "var(--yellow-ink)" }}>
                  {keywordVerification === "llm_verified" ? "AI verified" : "local check"}
                </span>
              )}
              {coveragePct !== null && <span className="mono" style={{ fontSize: 11, fontWeight: 800, color: "var(--blue-ink)" }}>{coveragePct}% JD keywords</span>}
            </div>
          </div>
          <div style={{ fontSize: 12.3, color: "var(--ink-2)", lineHeight: 1.5 }}>
            {missingTerms.length > 0
              ? <>You're missing these terms from the JD: <b>{missingTerms.slice(0, 6).join(", ")}</b>. We've incorporated the supported matches where applicable.</>
              : <>Strong keyword coverage. We've incorporated supported JD terms where they fit the profile.</>
            }
          </div>
          {incorporatedTerms.length > 0 && (
            <div className="row gap-2" style={{ flexWrap: "wrap", marginTop: 8 }}>
              <span className="eyebrow" style={{ marginRight: 2 }}>In resume</span>
              {incorporatedTerms.slice(0, 8).map((term, i) => (
                <span key={i} className="pill" style={{ background: "var(--paper)", color: "var(--blue-ink)", border: "1px solid var(--blue)" }}>{term}</span>
              ))}
            </div>
          )}
        </div>
      )}
      {visibleGenerateErr && <div style={{ color: "var(--bad)", fontSize: 12, padding: "8px 10px", background: "var(--bad-soft)", border: "1px solid var(--bad)", borderRadius: 8 }}>{visibleGenerateErr}</div>}
      {(
        <div style={{ flex: "0 0 clamp(680px, 82vh, 980px)", minHeight: 560, background: "var(--card)", border: "1px solid var(--line)", borderRadius: 12, overflow: "hidden" }}>
          {activeReady && pdfBlobUrl && (
            <iframe
              key={pdfBlobUrl}
              src={pdfBlobUrl}
              title={activeDoc === "resume" ? "Resume" : "Cover Letter"}
              width="100%"
              style={{ height: "100%", border: "none", display: "block" }}
            />
          )}
          {generating && !pdfBlobUrl && (
            <div style={{ height: "100%", minHeight: 420, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12, color: "var(--ink-3)", fontSize: 12, padding: 24, textAlign: "center" }}>
              <div className="mono pulse">Tailoring resume and cover letter for {j.company}...</div>
              <div style={{ maxWidth: 360, lineHeight: 1.5 }}>The generator is choosing the strongest profile projects for this job description.</div>
            </div>
          )}
          {!generating && activeReady && !pdfBlobUrl && (
            <div style={{ height: "100%", minHeight: 420, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12, color: "var(--ink-3)", fontSize: 12, padding: 24, textAlign: "center" }}>
              {pdfLoadErr ? (
                <>
                  <div style={{ color: "var(--bad)", maxWidth: 460, lineHeight: 1.5 }}>Failed to load PDF preview: {pdfLoadErr}</div>
                  <button
                    onClick={() => setPdfPreviewAttempt(n => n + 1)}
                    style={{ padding: "8px 18px", borderRadius: 8, fontSize: 12, fontWeight: 700, border: "1px solid var(--blue)", background: "var(--blue-soft)", color: "var(--blue-ink)", cursor: "pointer" }}
                  >
                    Retry preview
                  </button>
                </>
              ) : (
                <>
                  <div>Loading {activeDoc === "resume" ? "resume" : "cover letter"}...</div>
                  <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-4)" }}>Preview will time out automatically if the backend does not respond.</div>
                </>
              )}
            </div>
          )}
          {!generating && !activeReady && (
            <div style={{ height: "100%", minHeight: 420, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12, color: "var(--ink-3)", fontSize: 12, padding: 24, textAlign: "center" }}>
              <Icon name="file" size={26} color="var(--ink-4)" />
              <div style={{ fontWeight: 700, color: "var(--ink-2)" }}>
                No tailored {activeDoc === "resume" ? "resume" : "cover letter"} yet.
              </div>
              <div style={{ maxWidth: 380, lineHeight: 1.5 }}>
                Generate with the buttons above to create tailored PDFs using the job description, company context, and best-matching projects.
              </div>
              <button onClick={() => generatePdf()} disabled={generating} style={{ padding: "8px 18px", borderRadius: 8, fontSize: 12, fontWeight: 700, border: "1px solid var(--purple)", background: "var(--purple-soft)", color: "var(--purple-ink)", cursor: generating ? "wait" : "pointer" }}>
                {activeDoc === "resume" ? "Render PDF" : "Generate Cover Letter"}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
