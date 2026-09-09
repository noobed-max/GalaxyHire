// Pure selection logic for the approval-doc-pane: no React imports, unit-testable.
import type { DocSelection } from "../../../api/docPane";

export type ProfilePoint = {
  id: string;
  text: string;
  star?: boolean;
  note?: string;
  /** Provenance carried by the profile snapshot.  These are intentionally
   * optional because older snapshots predate source/tag metadata. */
  tag_ids?: string[];
  source_tag_ids?: string[];
  tag_id?: string;
  source_resume_ids?: string[];
  [key: string]: unknown;
};
export type ProfileEntry = {
  id: string;
  points?: ProfilePoint[];
  role_variants?: { title: string; company?: string; period?: string; resume_id?: string; tag_id?: string }[];
  title_variants?: { title: string; resume_id?: string; tag_id?: string }[];
  [key: string]: unknown;
};
export type ProfileSkill = {
  id?: string;
  n?: string;
  cat?: string;
  category?: string;
  tag_ids?: string[];
  source_tag_ids?: string[];
  tag_id?: string;
  source_resume_ids?: string[];
};
export type ProfileData = {
  n?: string;
  s?: string;
  skills?: ProfileSkill[];
  exp?: ProfileEntry[];
  projects?: ProfileEntry[];
  identity?: Record<string, unknown>;
  certifications?: unknown[];
  education?: unknown[];
  achievements?: unknown[];
};

export const DEFAULT_SECTIONS = ["skills", "experience", "projects"];

export function emptySelection(): DocSelection {
  return {
    version: 1,
    sections: [...DEFAULT_SECTIONS],
    experience_order: [],
    experience_on: [],
    projects_order: [],
    projects_on: [],
    points_on: [],
    skills_on: [],
    entity_titles: {},
  };
}

/**
 * Saved selections are user data and older rows may not contain fields added
 * by later versions of the pane.  Normalize at every boundary so a missing
 * entity_titles map (or a malformed legacy array) can never make the UI throw
 * or accidentally erase the other fields on the next debounce save.
 */
export function normalizeSelection(raw?: Partial<DocSelection> | null): DocSelection {
  const source = raw || {};
  const strings = (value: unknown): string[] => Array.isArray(value)
    ? value.map(item => String(item || "").trim()).filter(Boolean)
    : [];
  const titles: Record<string, string> = {};
  if (source.entity_titles && typeof source.entity_titles === "object") {
    for (const [id, title] of Object.entries(source.entity_titles as Record<string, unknown>)) {
      const cleanId = String(id || "").trim();
      const cleanTitle = String(title || "").trim();
      if (cleanId && cleanTitle) titles[cleanId] = cleanTitle;
    }
  }
  return {
    version: Number(source.version) || 1,
    sections: strings(source.sections).length ? strings(source.sections) : [...DEFAULT_SECTIONS],
    experience_order: strings(source.experience_order),
    experience_on: strings(source.experience_on),
    projects_order: strings(source.projects_order),
    projects_on: strings(source.projects_on),
    points_on: strings(source.points_on),
    skills_on: strings(source.skills_on),
    entity_titles: titles,
  };
}

const ID_KEYS = ["experience_on", "projects_on", "points_on", "skills_on"] as const;

export function isEmptySelection(selection: DocSelection | null | undefined): boolean {
  if (!selection) return true;
  return ID_KEYS.every(key => !selection[key] || selection[key].length === 0);
}

function without(list: string[], id: string): string[] {
  return list.filter(x => x !== id);
}

export function togglePoint(selection: DocSelection, pointId: string): DocSelection {
  const on = selection.points_on.includes(pointId);
  const next = on ? without(selection.points_on, pointId) : [...selection.points_on, pointId];
  return { ...selection, points_on: Array.from(new Set(next)) };
}

export function toggleAllPoints(
  selection: DocSelection,
  pointIds: string[],
  targetState?: boolean,
): DocSelection {
  const currentSet = new Set(selection.points_on);
  const allSelected = pointIds.length > 0 && pointIds.every(id => currentSet.has(id));
  const shouldTurnOn = targetState !== undefined ? targetState : !allSelected;

  for (const id of pointIds) {
    if (shouldTurnOn) {
      currentSet.add(id);
    } else {
      currentSet.delete(id);
    }
  }
  return { ...selection, points_on: Array.from(currentSet) };
}

export function deduplicateSelection(selection: DocSelection): DocSelection {
  if (!selection) return emptySelection();
  const dedup = (arr: string[] | undefined): string[] => {
    if (!Array.isArray(arr)) return [];
    return Array.from(new Set(arr));
  };

  const normalized = normalizeSelection(selection);
  return {
    ...normalized,
    points_on: dedup(normalized.points_on),
    skills_on: dedup(normalized.skills_on),
    experience_on: dedup(normalized.experience_on),
    projects_on: dedup(normalized.projects_on),
    experience_order: dedup(normalized.experience_order),
    projects_order: dedup(normalized.projects_order),
    sections: dedup(normalized.sections),
    entity_titles: { ...normalized.entity_titles },
  };
}

export function toggleSkill(selection: DocSelection, skillId: string): DocSelection {
  const on = selection.skills_on.includes(skillId);
  return { ...selection, skills_on: on ? without(selection.skills_on, skillId) : [...selection.skills_on, skillId] };
}

export function toggleEntry(
  selection: DocSelection,
  kind: "experience" | "projects",
  entryId: string,
): DocSelection {
  const onKey = kind === "experience" ? "experience_on" : "projects_on";
  const on = selection[onKey].includes(entryId);
  return { ...selection, [onKey]: on ? without(selection[onKey], entryId) : [...selection[onKey], entryId] };
}

export function moveEntry(
  selection: DocSelection,
  kind: "experience" | "projects",
  entryId: string,
  delta: -1 | 1,
): DocSelection {
  const orderKey = kind === "experience" ? "experience_order" : "projects_order";
  // The order array is authoritative only for ids it contains: seed it with the
  // current visible order so a first move captures the full sequence.
  const current = selection[orderKey];
  const merged = [...current];
  for (const id of allOrderIds(selection, kind)) {
    if (!merged.includes(id)) merged.push(id);
  }
  const idx = merged.indexOf(entryId);
  const target = idx + delta;
  if (idx < 0 || target < 0 || target >= merged.length) return selection;
  merged.splice(idx, 1);
  merged.splice(target, 0, entryId);
  return { ...selection, [orderKey]: merged };
}

function allOrderIds(selection: DocSelection, kind: "experience" | "projects"): string[] {
  const toggled = kind === "experience" ? selection.experience_on : selection.projects_on;
  return [...toggled];
}

export interface KnownIds {
  skills: string[];
  experience: string[];
  projects: string[];
  points: string[];
}

export function pruneStaleIds(
  selection: DocSelection,
  known: KnownIds,
): { selection: DocSelection; dropped: number } {
  selection = normalizeSelection(selection);
  const keepKnown = (list: string[], valid: string[]): [string[], number] => {
    const validSet = new Set(valid);
    const kept = list.filter(id => validSet.has(id));
    return [kept, list.length - kept.length];
  };
  const [pointsOn, dP] = keepKnown(selection.points_on, known.points);
  const [skillsOn, dS] = keepKnown(selection.skills_on, known.skills);
  const [expOn, dE] = keepKnown(selection.experience_on, known.experience);
  const [projOn, dPr] = keepKnown(selection.projects_on, known.projects);
  const validEntities = new Set([...known.experience, ...known.projects]);
  const entityTitles = Object.fromEntries(
    Object.entries(selection.entity_titles || {}).filter(([id]) => validEntities.has(id)),
  );
  const dropped = dP + dS + dE + dPr + Object.keys(selection.entity_titles || {}).length - Object.keys(entityTitles).length;
  return {
    selection: { ...selection, points_on: pointsOn, skills_on: skillsOn, experience_on: expOn, projects_on: projOn, entity_titles: entityTitles },
    dropped,
  };
}

// ── density estimate (My-Resume-Maker-style one-page fit heuristic) ─────────

export function entryPoints(entry: ProfileEntry): ProfilePoint[] {
  return Array.isArray(entry?.points) ? entry.points.filter(p => p && p.id) : [];
}

/** The canonical title is the first stable row title; a saved choice wins only
 * when it is still one of the source variants. */
export function entityTitle(entry: ProfileEntry, selection?: Pick<DocSelection, "entity_titles">): string {
  const fallback = String(entry.role || entry.title || "Entry").trim() || "Entry";
  const variants = Array.from(new Set([
    fallback,
    ...(Array.isArray(entry.role_variants) ? entry.role_variants : []).map(v => String(v?.title || "").trim()),
    ...(Array.isArray(entry.title_variants) ? entry.title_variants : []).map(v => String(v?.title || "").trim()),
  ].filter(Boolean)));
  const chosen = selection?.entity_titles?.[String(entry.id || "")];
  return chosen && variants.includes(chosen) ? chosen : variants[0] || fallback;
}

export function pickedEntries(profile: ProfileData, selection: DocSelection, kind: "experience" | "projects"): ProfileEntry[] {
  const rows = kind === "experience" ? profile.exp || [] : profile.projects || [];
  const onKey = kind === "experience" ? "experience_on" : "projects_on";
  const onSet = new Set(selection[onKey]);
  const pickedRows = rows.filter(row => {
    const rowId = String(row.id || "");
    if (onSet.has(rowId)) return true;
    return entryPoints(row).some(p => selection.points_on.includes(p.id));
  });
  const orderKey = kind === "experience" ? "experience_order" : "projects_order";
  const order = selection[orderKey];
  if (!order.length) return pickedRows;
  const pos = new Map(order.map((id, i) => [id, i]));
  return [...pickedRows].sort((a, b) =>
    (pos.has(String(a.id)) ? pos.get(String(a.id))! : order.length)
    - (pos.has(String(b.id)) ? pos.get(String(b.id))! : order.length));
}

export function pickedSkills(profile: ProfileData, selection: DocSelection): ProfileSkill[] {
  if (!selection.skills_on.length) return [];
  const onSet = new Set(selection.skills_on);
  return (profile.skills || []).filter(s => s.id && onSet.has(s.id));
}

export function estimateLines(profile: ProfileData, selection: DocSelection): number {
  if (isEmptySelection(selection)) return 0;
  let lines = 4 + 1.5 * DEFAULT_SECTIONS.length;
  for (const kind of ["experience", "projects"] as const) {
    for (const row of pickedEntries(profile, selection, kind)) {
      lines += 1.4; // entry title
      const points = entryPoints(row).filter(p => selection.points_on.includes(p.id));
      const visible = points.length ? points : entryPoints(row);
      lines += visible.reduce((sum, p) => sum + Math.ceil(Math.max(1, p.text.length) / 100), 0);
    }
  }
  const joined = pickedSkills(profile, selection).map(s => s.n || "").filter(Boolean).join(", ");
  if (joined) lines += Math.ceil(joined.length / 100);
  return Math.ceil(lines);
}
