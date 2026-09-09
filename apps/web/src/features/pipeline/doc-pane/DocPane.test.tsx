import { describe, expect, it } from "bun:test";
import { readFileSync } from "node:fs";

const docPane = readFileSync(new URL("./DocPane.tsx", import.meta.url), "utf8");
const builders = readFileSync(new URL("../components/JobBuilders.tsx", import.meta.url), "utf8");
const apiLayer = readFileSync(new URL("../../../api/docPane.ts", import.meta.url), "utf8");
// Backend prompt contract lives across the app boundary: web <-> apps/api.
const drafting = readFileSync(new URL("../../../../../api/generation/generators/drafting.py", import.meta.url), "utf8");

describe("approval-doc-pane contracts", () => {
  it("persists selections through the doc-pane API with a debounced PUT", () => {
    expect(docPane).toContain("docPaneApi.putSelection(api, j.job_id, profileTag, deduplicateSelection(selection))");
    expect(docPane).toContain(`SAVE_DEBOUNCE_MS = 600`);
    expect(docPane).toContain("/api/v1/profile");
    expect(docPane).toContain("/api/v1/point-tags");
    expect(docPane).toContain("getSelection(api, j.job_id, profileTag)");
    expect(docPane).toContain("entity_titles: { ...s.entity_titles, [entryId]: title }");
    expect(docPane).toContain("normalizeSelection(d.selection)");
  });

  it("unifies tag filtering on profileTag and eliminates internal tagFilter dropdown", () => {
    expect(docPane).not.toContain("const [tagFilter, setTagFilter]");
    expect(docPane).not.toContain("Filter by tag");
    expect(docPane).toContain('Nothing to show{profileTag ? " for this tag" : ""} — ingest a resume and tag some points first.');
  });

  it("refetches the saved selection when the profile tag changes", () => {
    expect(docPane).toContain("[api, j.job_id, profileTag]");
  });

  it("hands the picked selection to generation via the builders", () => {
    expect(builders).toContain("onGenerateWithSelection={sel => generatePdf(sel)}");
    expect(builders).toContain('JSON.stringify({ selection })');
    expect(builders).toContain('<DocPane');
    // Merged single-scroll layout: no Build/Package mode gate, per-doc generate buttons.
    expect(builders).toContain("Generate Resume");
    expect(builders).toContain("Generate Cover Letter");
    expect(builders).not.toContain("Generate Package");
    expect(builders).not.toContain("Run full pipeline");
  });

  it("keeps title choices in the preview/generation selection contract", () => {
    const preview = readFileSync(new URL("./PreviewPane.tsx", import.meta.url), "utf8");
    const state = readFileSync(new URL("./selectionState.ts", import.meta.url), "utf8");
    expect(preview).toContain("entityTitle(row, selection)");
    expect(state).toContain("entity_titles");
    expect(state).toContain("normalizeSelection");
  });

  it("the generator treats selected points as binding verbatim content", () => {
    expect(drafting).toContain("selection_block");
    expect(drafting).toContain("USER-SELECTED RESUME CONTENT");
    expect(drafting).toContain("VERBATIM");
    expect(drafting).toContain("You own only the SKILLS");
  });

  it("keeps the selection API on the versioned endpoints", () => {
    expect(apiLayer).toContain("/api/v1/doc-selections");
    expect(apiLayer).toContain("/api/v1/doc-presets");
    expect(apiLayer).not.toContain("/api/v0");
  });
});
