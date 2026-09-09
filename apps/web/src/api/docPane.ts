import type { ApiFetch } from "./types";

export interface DocSelection {
  version: number;
  sections: string[];
  experience_order: string[];
  experience_on: string[];
  projects_order: string[];
  projects_on: string[];
  points_on: string[];
  skills_on: string[];
  /** Added after the first persisted-selection format; hydrate through
   * normalizeSelection before reading it. */
  entity_titles?: Record<string, string>;
}

export type PresetRow = {
  id: string;
  name: string;
  tag_id: string;
  updated_at: string;
  selection: Partial<DocSelection>;
};

const jsonHeaders = { "Content-Type": "application/json" };

function scopeQuery(jobId: string, tagId: string): string {
  const params = new URLSearchParams();
  if (jobId) params.set("job_id", jobId);
  if (tagId) params.set("tag_id", tagId);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export const docPaneApi = {
  getSelection: async (api: ApiFetch, jobId: string, tagId = ""): Promise<{ selection: DocSelection | null }> =>
    (await api(`/api/v1/doc-selections${scopeQuery(jobId, tagId)}`)).json(),
  putSelection: async (api: ApiFetch, jobId: string, tagId: string, selection: DocSelection): Promise<{ ok: boolean; updated_at?: string }> =>
    (await api("/api/v1/doc-selections", {
      method: "PUT",
      headers: jsonHeaders,
      body: JSON.stringify({ job_id: jobId, tag_id: tagId, selection }),
    })).json(),
  deleteSelection: (api: ApiFetch, jobId: string, tagId = "") =>
    api(`/api/v1/doc-selections/${encodeURIComponent(jobId)}${scopeQuery("", tagId)}`, { method: "DELETE" }),
  listPresets: async (api: ApiFetch): Promise<{ presets: PresetRow[] }> =>
    (await api("/api/v1/doc-presets")).json(),
  savePreset: async (api: ApiFetch, name: string, tagId: string, selection: DocSelection): Promise<PresetRow & { ok: boolean }> =>
    (await api("/api/v1/doc-presets", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ name, tag_id: tagId, selection }),
    })).json(),
  deletePreset: (api: ApiFetch, presetId: string) =>
    api(`/api/v1/doc-presets/${encodeURIComponent(presetId)}`, { method: "DELETE" }),
};
