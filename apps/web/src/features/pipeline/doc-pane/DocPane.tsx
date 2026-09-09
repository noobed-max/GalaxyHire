import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { docPaneApi, type DocSelection, type PresetRow } from "../../../api/docPane";
import { DensityMeter } from "./DensityMeter";
import { PreviewPane } from "./PreviewPane";
import { EntriesPicker, SkillsPicker, buildTagIndex,
  type PointSuggestion, type PointTagRow, type Tag, type TagIndex } from "./Pickers";
import { emptySelection, entryPoints, isEmptySelection, moveEntry, pruneStaleIds,
  normalizeSelection, toggleAllPoints, deduplicateSelection, toggleEntry, togglePoint, toggleSkill,
  type ProfileData, type ProfileEntry } from "./selectionState";

export interface SuggestRequest {
  token: number;
  selection: Partial<DocSelection>;
  suggestions: PointSuggestion[];
}

import type { ApiFetch, Lead } from "../../../types";

const SAVE_DEBOUNCE_MS = 600;

const norm = (v: unknown) => String(v || "").replace(/\s+/g, " ").trim().toLowerCase();

/** Inline form for a hand-written experience/project entry (custom content the
 *  importer never saw). Posts to the profile endpoints, then asks the pane to reload. */
function AddEntryRow({ kind, api, onAdded }: {
  kind: "experience" | "projects"; api: ApiFetch; onAdded: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [form, setForm] = useState({ a: "", b: "", c: "", d: "" });
  if (!open) {
    return (
      <button className="btn btn-ghost" style={{ fontSize: 11, padding: "4px 10px", alignSelf: "flex-start" }}
        onClick={() => { setOpen(true); setErr(null); }}>
        + Add custom {kind === "experience" ? "experience" : "project"}
      </button>
    );
  }
  const labels = kind === "experience"
    ? (["Role", "Company", "Period", "Description (one bullet per line)"] as const)
    : (["Title", "Stack", "Repo URL", "Impact (one bullet per line)"] as const);
  const keys = ["a", "b", "c", "d"] as const;
  const submit = async () => {
    if (!form.a.trim()) { setErr(kind === "experience" ? "Role or company is required." : "Title is required."); return; }
    setBusy(true);
    setErr(null);
    try {
      const body = kind === "experience"
        ? { role: form.a, co: form.b, period: form.c, d: form.d }
        : { title: form.a, stack: form.b, repo: form.c, impact: form.d };
      const r = await api(`/api/v1/profile/${kind}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`Server returned ${r.status}`);
      setForm({ a: "", b: "", c: "", d: "" });
      setOpen(false);
      onAdded();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Add failed");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card col gap-2" style={{ padding: 10, background: "var(--paper-2)" }}>
      <div className="eyebrow">Custom {kind === "experience" ? "experience" : "project"}</div>
      {keys.map((k, i) => (
        <input
          key={k}
          value={form[k]}
          onChange={e => setForm({ ...form, [k]: e.target.value })}
          placeholder={labels[i]}
          className="field-input"
          style={{ fontSize: 12, padding: "5px 9px" }}
        />
      ))}
      {err && <div style={{ color: "var(--bad)", fontSize: 11.5 }}>{err}</div>}
      <div className="row gap-2">
        <button className="btn btn-primary" style={{ fontSize: 11, padding: "4px 10px" }} disabled={busy} onClick={() => void submit()}>
          {busy ? "Adding…" : "Add entry"}
        </button>
        <button className="btn btn-ghost" style={{ fontSize: 11, padding: "4px 10px" }} onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </div>
  );
}

export interface DocPaneProps {
  j: Lead;
  api: ApiFetch;
  tags: Tag[];
  profileTag: string;
  generating?: boolean;
  onGenerateWithSelection: (selection: DocSelection) => void;
  /** AI pick from "Generate Resume": applied once per token into the local selection. */
  suggestRequest?: SuggestRequest | null;
}

export function DocPane({ j, api, tags, profileTag, generating, onGenerateWithSelection, suggestRequest }: DocPaneProps) {
  const [profile, setProfile] = useState<ProfileData>({});
  const [pointTags, setPointTags] = useState<PointTagRow[]>([]);
  const [selection, setSelection] = useState<DocSelection>(emptySelection());
  // Hydration gate: never echo a half-loaded selection back to the server.
  const [hydrated, setHydrated] = useState(false);
  const [presets, setPresets] = useState<PresetRow[]>([]);
  const [presetName, setPresetName] = useState("");
  const [presetMsg, setPresetMsg] = useState("");
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [conflicts, setConflicts] = useState<{ id: string; status: string; picked_id: string | null; members: { point_id: string }[] }[]>([]);
  // AI suggestions from "Generate Resume" (suggest-selection): proposals beside their
  // target point. Accepting one rewrites that point through the profile-update path.
  const [suggestions, setSuggestions] = useState<PointSuggestion[]>([]);
  const [appliedSuggestToken, setAppliedSuggestToken] = useState<number>(0);
  const [mutateErr, setMutateErr] = useState<string | null>(null);
  // Bumped after every point edit/add so profile (and derived point ids) reload.
  const [refreshTick, setRefreshTick] = useState(0);

  const tagIndex = useMemo(() => buildTagIndex(pointTags), [pointTags]);

  const altWordings = useMemo(() => {
    const set = new Set<string>();
    for (const g of conflicts) {
      if (g.status === "keep_both") {
        for (const m of g.members || []) {
          if (m.point_id) set.add(m.point_id);
        }
      }
    }
    return set;
  }, [conflicts]);

  const loadPresets = useCallback(() => {
    docPaneApi.listPresets(api).then(d => setPresets(d.presets || [])).catch(() => {});
  }, [api]);

  const reloadProfile = useCallback(() => {
    api("/api/v1/profile").then(r => r.json()).then(d => setProfile(d || {})).catch(() => {});
    api("/api/v1/point-tags").then(r => r.json()).then(d => setPointTags(d.point_tags || [])).catch(() => {});
    api("/api/v1/conflicts").then(r => r.json()).then(d => setConflicts(d.groups || [])).catch(() => {});
  }, [api]);

  useEffect(() => {
    reloadProfile();
    loadPresets();
  }, [reloadProfile, loadPresets, refreshTick]);

  // Apply an AI pick once per token: replace the local selection wholesale (the pick is
  // a fresh proposal for this job, not a merge), and show its rewrite suggestions.
  useEffect(() => {
    if (!suggestRequest || suggestRequest.token === appliedSuggestToken) return;
    setAppliedSuggestToken(suggestRequest.token);
        setSelection(normalizeSelection(suggestRequest.selection));
    setSuggestions(suggestRequest.suggestions || []);
  }, [suggestRequest, appliedSuggestToken]);

  // Duplicate suppression (display only — picks persist and generation still
  // enforces groups server-side):
  //   1. conflict groups: show only the picked wording (open groups show the
  //      first member); keep_both/dismissed groups show everything;
  //   2. exact-text repeats (same kind + same normalized text) show once.
  const hiddenIds = useMemo(() => {
    const hidden = new Set<string>();
    for (const g of conflicts) {
      if (g.status === "keep_both" || g.status === "dismissed") continue;
      const ids = (g.members || []).map(m => m.point_id);
      const keeper = g.status === "picked" && g.picked_id ? g.picked_id : ids[0];
      for (const id of ids) if (id !== keeper) hidden.add(id);
    }
    const seen = new Set<string>();
    const mark = (kind: string, id: string, text: string) => {
      const key = `${kind}:${norm(text)}`;
      if (!key || key.endsWith(":")) return;
      if (seen.has(key)) hidden.add(String(id));
      else seen.add(key);
    };
    for (const s of profile.skills || []) mark("skill", s.id ?? "", s.n ?? "");
    for (const e of profile.exp || []) {
      mark("experience_entry", e.id, `${norm(e.role)} ${norm(e.co)}`);
      for (const p of e.points || []) mark("experience_point", p.id, `${e.id}:${p.text ?? ""}`);
    }
    for (const p of profile.projects || []) {
      mark("project_entry", p.id, norm(p.title));
      for (const pt of p.points || []) mark("project_point", pt.id, `${p.id}:${pt.text ?? ""}`);
    }
    return hidden;
  }, [conflicts, profile]);

  const hasContent = (tagId: string, onlyUntagged: boolean, ids: TagIndex) => {
    const rowVisible = (rowId: string) => {
      if (hiddenIds.has(rowId)) return false;
      const t = ids.idsByPoint.get(rowId);
      if (onlyUntagged) return !t || t.size === 0;
      if (!tagId) return true;
      return t?.has(tagId) || false;
    };
    if ((profile.skills || []).some(s => rowVisible(String(s.id || "")))) return true;
    for (const key of ["exp", "projects"] as const) {
      for (const e of profile[key] || []) {
        if (rowVisible(String(e.id || ""))) return true;
        if ((e.points || []).some(p => rowVisible(p.id))) return true;
      }
    }
    return false;
  };

  // Saved selection is scoped to (job, tag): refetch whenever the track changes.
  // A failed fetch must NOT arm saving: echoing an empty local selection back
  // would silently wipe what the server still holds.
  useEffect(() => {
    let alive = true;
    setHydrated(false);
    docPaneApi.getSelection(api, j.job_id, profileTag)
      .then(d => {
        if (!alive) return;
        setSelection(normalizeSelection(d.selection));
        setHydrated(true);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [api, j.job_id, profileTag]);

  // Debounced persistence: 600ms quiet period, then full-replace PUT.
  const saveTimerRef = useRef<number | null>(null);
  useEffect(() => {
    if (!hydrated) return;
    if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      docPaneApi.putSelection(api, j.job_id, profileTag, deduplicateSelection(selection))
        .then(() => setSaveErr(null))
        .catch(err => setSaveErr(err instanceof Error ? err.message : String(err)));
    }, SAVE_DEBOUNCE_MS);
    return () => {
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current);
        saveTimerRef.current = null;
      }
    };
  }, [api, j.job_id, profileTag, hydrated, selection]);

  // Re-imports change point ids: drop picks that no longer resolve so a stale
  // selection can neither ghost-show nor silently generate dropped content.
  useEffect(() => {
    if (!hydrated || !profile) return;
    const known = {
      skills: (profile.skills || []).map(s => String(s.id)).filter(Boolean),
      experience: (profile.exp || []).map(e => String(e.id)).filter(Boolean),
      projects: (profile.projects || []).map(e => String(e.id)).filter(Boolean),
      points: [...(profile.exp || []), ...(profile.projects || [])]
        .flatMap(e => Array.isArray(e.points) ? e.points.map(p => p.id) : []),
    };
    const { selection: pruned, dropped } = pruneStaleIds(selection, known);
    if (dropped > 0) setSelection(pruned);
  }, [hydrated, profile]); // eslint-disable-line react-hooks/exhaustive-deps -- prune only on fresh profile data

  const knownIds = useMemo(() => new Set([
    ...(profile.skills || []).map(s => String(s.id)),
    ...(profile.exp || []).map(e => String(e.id)),
    ...(profile.projects || []).map(e => String(e.id)),
    ...(profile.exp || []).flatMap(e => Array.isArray(e.points) ? e.points.map(p => p.id) : []),
    ...(profile.projects || []).flatMap(e => Array.isArray(e.points) ? e.points.map(p => p.id) : []),
  ]), [profile]);

  const hasAnyPick = !isEmptySelection(selection);
  const knownCount = knownIds.size;

  // Point edits go through the parent blob: the row's points are re-split from it with
  // stable content-hash ids, so unchanged lines keep their ids (and picks) while the
  // edited line earns a new one. The prune effect then drops the stale pick by itself.
  const rewriteEntryBlob = async (
    kind: "experience" | "projects",
    entry: ProfileEntry,
    texts: string[],
  ): Promise<boolean> => {
    const blob = texts.map(t => t.trim()).filter(Boolean).join("\n");
    const base = kind === "experience" ? "/api/v1/profile/experience" : "/api/v1/profile/project";
    try {
      const body = kind === "experience"
        ? { role: String(entry.role || ""), co: String(entry.co || ""), period: String(entry.period || ""), d: blob }
        : { title: String(entry.title || ""), stack: entry.stack ?? "", repo: entry.repo ?? "", impact: blob };
      const r = await api(`${base}/${encodeURIComponent(String(entry.id))}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`Server returned ${r.status}`);
      setRefreshTick(t => t + 1);
      return true;
    } catch (err) {
      setMutateErr(err instanceof Error ? err.message : "Point update failed");
      return false;
    }
  };

  const findEntry = (kind: "experience" | "projects", entryId: string): ProfileEntry | undefined =>
    ((kind === "experience" ? profile.exp : profile.projects) || []).find(e => String(e.id) === entryId);

  const handleEditPoint = async (kind: "experience" | "projects", entryId: string, pointId: string, text: string) => {
    const entry = findEntry(kind, entryId);
    if (!entry || !text.trim()) return;
    const texts = entryPoints(entry).map(p => (p.id === pointId ? text.trim() : p.text));
    await rewriteEntryBlob(kind, entry, texts);
  };

  const handleAddPoint = async (kind: "experience" | "projects", entryId: string, text: string) => {
    const entry = findEntry(kind, entryId);
    if (!entry || !text.trim()) return;
    const texts = [...entryPoints(entry).map(p => p.text), text.trim()];
    if (await rewriteEntryBlob(kind, entry, texts)) {
      // Auto-pick the freshly added point so it actually reaches the resume.
      const res = await api("/api/v1/profile").then(r => r.json()).catch(() => null);
      const rows = res ? ((kind === "experience" ? res.exp : res.projects) || []) : [];
      const row = rows.find((e: ProfileEntry) => String(e.id) === entryId);
      const fresh = (row?.points || []).find((p: { text: string }) => p.text === text.trim());
      if (fresh) setSelection(s => ({ ...s, points_on: [...s.points_on, fresh.id] }));
    }
  };

  const handleAcceptSuggestion = async (pointId: string) => {
    const sug = suggestions.find(s => s.point_id === pointId);
    if (!sug || !sug.suggested_text.trim()) return;
    const kind = sug.parent_kind === "projects" ? "projects" : "experience";
    await handleEditPoint(kind, sug.parent_id, pointId, sug.suggested_text);
    setSuggestions(prev => prev.filter(s => s.point_id !== pointId));
  };

  const savePreset = async () => {
    const name = presetName.trim();
    if (!name) { setPresetMsg("Name the preset first."); return; }
    try {
      await docPaneApi.savePreset(api, name, profileTag, deduplicateSelection(selection));
      setPresetMsg(`Saved "${name}".`);
      setPresetName("");
      loadPresets();
    } catch (err) {
      setPresetMsg(err instanceof Error ? err.message : "Preset save failed");
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ fontSize: 12, color: "var(--ink-3)", lineHeight: 1.5 }}>
        Pick exactly what this application should contain. Picked points are reproduced
        verbatim, in your order; everything else stays out.
      </div>

      {!hasContent(profileTag, false, tagIndex) && (
        <div style={{ fontSize: 12, color: "var(--ink-3)" }}>
          Nothing to show{profileTag ? " for this tag" : ""} — ingest a resume and tag some points first.
        </div>
      )}

      {/* Direct section rendering without outer tag-box duplication */}
      <SkillsPicker
        skills={profile.skills || []}
        selection={selection}
        onToggle={skillId => setSelection(s => toggleSkill(s, skillId))}
        tagFilter={profileTag}
        tagIndex={tagIndex}
        tags={tags}
        hiddenIds={hiddenIds}
      />
      <EntriesPicker
        kind="experience"
        entries={profile.exp || []}
        selection={selection}
        tagFilter={profileTag}
        tagIndex={tagIndex}
        tags={tags}
        hiddenIds={hiddenIds}
        altWordings={altWordings}
        onToggleEntry={entryId => setSelection(s => toggleEntry(s, "experience", entryId))}
        onTogglePoint={pointId => setSelection(s => togglePoint(s, pointId))}
        onTogglePoints={(pointIds, targetOn) => setSelection(s => toggleAllPoints(s, pointIds, targetOn))}
        onMove={(entryId, delta) => setSelection(s => moveEntry(s, "experience", entryId, delta))}
        entityTitles={selection.entity_titles}
        onChooseEntityTitle={(entryId, title) => setSelection(s => ({
          ...s,
          entity_titles: { ...s.entity_titles, [entryId]: title },
        }))}
        suggestions={suggestions}
        onAcceptSuggestion={handleAcceptSuggestion}
        onDismissSuggestion={pointId => setSuggestions(prev => prev.filter(s => s.point_id !== pointId))}
        onEditPoint={(entryId, pointId, text) => void handleEditPoint("experience", entryId, pointId, text)}
        onAddPoint={(entryId, text) => void handleAddPoint("experience", entryId, text)}
      />
      <EntriesPicker
        kind="projects"
        entries={profile.projects || []}
        selection={selection}
        tagFilter={profileTag}
        tagIndex={tagIndex}
        tags={tags}
        hiddenIds={hiddenIds}
        altWordings={altWordings}
        onToggleEntry={entryId => setSelection(s => toggleEntry(s, "projects", entryId))}
        onTogglePoint={pointId => setSelection(s => togglePoint(s, pointId))}
        onTogglePoints={(pointIds, targetOn) => setSelection(s => toggleAllPoints(s, pointIds, targetOn))}
        onMove={(entryId, delta) => setSelection(s => moveEntry(s, "projects", entryId, delta))}
        entityTitles={selection.entity_titles}
        onChooseEntityTitle={(entryId, title) => setSelection(s => ({
          ...s,
          entity_titles: { ...s.entity_titles, [entryId]: title },
        }))}
        suggestions={suggestions}
        onAcceptSuggestion={handleAcceptSuggestion}
        onDismissSuggestion={pointId => setSuggestions(prev => prev.filter(s => s.point_id !== pointId))}
        onEditPoint={(entryId, pointId, text) => void handleEditPoint("projects", entryId, pointId, text)}
        onAddPoint={(entryId, text) => void handleAddPoint("projects", entryId, text)}
      />

      {mutateErr && <div style={{ color: "var(--bad)", fontSize: 11.5 }}>{mutateErr}</div>}
      <AddEntryRow kind="experience" api={api} onAdded={() => setRefreshTick(t => t + 1)} />
      <AddEntryRow kind="projects" api={api} onAdded={() => setRefreshTick(t => t + 1)} />
      <DensityMeter profile={profile} selection={selection} />
      <PreviewPane profile={profile} selection={selection} />

      <div className="card col gap-2" style={{ padding: 10, background: "var(--paper-2)" }}>
        <div className="eyebrow">Presets</div>
        {presets.length > 0 && (
          <div className="row gap-2" style={{ flexWrap: "wrap" }}>
            {presets.map(preset => (
              <span key={preset.id} className="pill mono" style={{ background: "var(--paper)", border: "1px solid var(--line)", color: "var(--ink-2)", fontSize: 10, gap: 6 }}>
              <button title="Load this preset" onClick={() => setSelection(normalizeSelection(preset.selection))}
                  style={{ background: "none", border: "none", cursor: "pointer", fontWeight: 700, color: "var(--blue-ink)", fontSize: 10.5 }}>
                  {preset.name}
                </button>
                <button title="Delete preset" onClick={() => docPaneApi.deletePreset(api, preset.id).then(loadPresets).catch(() => {})}
                  style={{ background: "none", border: "none", cursor: "pointer", color: "var(--ink-4)", fontSize: 10.5 }}>x</button>
              </span>
            ))}
          </div>
        )}
        <div className="row gap-2" style={{ flexWrap: "wrap" }}>
          <input
            value={presetName}
            onChange={e => { setPresetName(e.target.value); setPresetMsg(""); }}
            placeholder="Preset name"
            className="field-input"
            style={{ fontSize: 11.5, padding: "5px 9px", maxWidth: 170 }}
          />
          <button onClick={savePreset} className="btn btn-ghost" style={{ fontSize: 11, padding: "4px 10px" }}>Save as preset</button>
          {hasAnyPick && (
            <button onClick={() => setSelection(emptySelection())} className="btn btn-ghost" style={{ fontSize: 11, padding: "4px 10px" }}>
              Clear selection
            </button>
          )}
          {knownCount === 0 && <span style={{ fontSize: 11, color: "var(--yellow-ink)" }}>Profile is empty — ingest a resume first.</span>}
        </div>
        {presetMsg && <div style={{ fontSize: 11, color: "var(--ink-3)" }}>{presetMsg}</div>}
      </div>

      {saveErr && <div style={{ color: "var(--bad)", fontSize: 11.5 }}>{saveErr}</div>}
      <button
        onClick={() => onGenerateWithSelection(deduplicateSelection(selection))}
        disabled={generating || !hasAnyPick}
        title={hasAnyPick ? "Generate the package from exactly these picks" : "Pick at least one skill or point first"}
        style={{
          padding: "9px 14px", borderRadius: 8, fontSize: 12, fontWeight: 800,
          border: "1px solid var(--purple)",
          background: "var(--purple-soft)", color: "var(--purple-ink)",
          cursor: generating || !hasAnyPick ? "not-allowed" : "pointer",
        }}
      >{generating ? "Generating..." : "Generate from selection"}</button>
    </div>
  );
}
