import { describe, expect, it } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import {
  buildTagIndex,
  CompositeTrackBadge,
  AltWordingBadge,
  FormattedText,
  getTrackStyle,
  EntriesPicker,
  type PointTagRow,
  type Tag,
} from "../pipeline/doc-pane/Pickers";
import { DocPane } from "../pipeline/doc-pane/DocPane";
import {
  emptySelection,
  toggleAllPoints,
  deduplicateSelection,
  type ProfileEntry,
} from "../pipeline/doc-pane/selectionState";
import type { DocSelection } from "../../api/docPane";
import type { ApiFetch, Lead } from "../../types";

const mockLead: Lead = {
  job_id: "lead-challenger-stress-1",
  title: "Principal Infrastructure Architect",
  company: "Aether Dynamics",
  url: "https://example.com/jobs/lead-challenger-stress-1",
  platform: "manual",
  status: "approved",
  asset: "",
  score: 98,
  reason: "match",
  match_points: [],
};

const mockApi = (async () => {
  return {
    ok: true,
    json: async () => ({}),
  } as Response;
}) as unknown as ApiFetch;

describe("Milestone 4 Empirical Challenger: Stress-Test Harness & Invariant Oracles", () => {
  // Test data with multi-track compound points and diverse tag categories
  const testTags: Tag[] = [
    { id: "tag-sde", name: "SDE" },
    { id: "tag-arch", name: "ARCH" },
    { id: "tag-ml", name: "ML" },
    { id: "tag-devops", name: "CloudOps" },
  ];

  const testPointTags: PointTagRow[] = [
    { point_kind: "experience", point_id: "p-sde-only", tag_id: "tag-sde", tag_name: "SDE" },
    { point_kind: "experience", point_id: "p-arch-only", tag_id: "tag-arch", tag_name: "ARCH" },
    { point_kind: "experience", point_id: "p-ml-only", tag_id: "tag-ml", tag_name: "ML" },
    // Multi-track compound point: SDE + ARCH
    { point_kind: "experience", point_id: "p-sde-arch", tag_id: "tag-sde", tag_name: "SDE" },
    { point_kind: "experience", point_id: "p-sde-arch", tag_id: "tag-arch", tag_name: "ARCH" },
    // Multi-track compound point: ARCH + CloudOps + ML (tri-track)
    { point_kind: "experience", point_id: "p-tri-track", tag_id: "tag-arch", tag_name: "ARCH" },
    { point_kind: "experience", point_id: "p-tri-track", tag_id: "tag-devops", tag_name: "CloudOps" },
    { point_kind: "experience", point_id: "p-tri-track", tag_id: "tag-ml", tag_name: "ML" },
  ];

  const sampleExperience: ProfileEntry[] = [
    {
      id: "exp-meta",
      role: "Staff Infrastructure Engineer",
      co: "SuprMentr Labs",
      period: "2024 – 2026",
      points: [
        { id: "p-sde-only", text: "Implemented **async RPC** protocol in Rust", star: true },
        { id: "p-arch-only", text: "Designed zero-trust service mesh on `k3s`", star: false },
        { id: "p-ml-only", text: "Fine-tuned quantized LLM on private embeddings", note: "Alternative wording" },
        { id: "p-sde-arch", text: "Architected distributed event broker with Kafka & Raft", star: true },
        { id: "p-tri-track", text: "Built ML inference pipeline with automated failover", star: true, note: "Variant metric" },
        { id: "p-untagged", text: "Organized engineering hackathons and weekly tech talks" },
      ],
    },
  ];

  /* -------------------------------------------------------------------------
   * CHALLENGE 1: Single Tag Selector Unification & AST Conformance
   * ------------------------------------------------------------------------- */
  describe("Challenge 1: Single Tag Selector Unification", () => {
    const docPaneSrc = readFileSync(new URL("../pipeline/doc-pane/DocPane.tsx", import.meta.url), "utf8");
    const buildersSrc = readFileSync(new URL("../pipeline/components/JobBuilders.tsx", import.meta.url), "utf8");

    it("1.1 DocPane.tsx has NO local tagFilter useState declaration", () => {
      expect(docPaneSrc).not.toMatch(/const\s+\[tagFilter,\s*setTagFilter\]\s*=\s*useState/);
    });

    it("1.2 DocPane.tsx has NO internal <select> dropdown for tag filtering", () => {
      expect(docPaneSrc).not.toContain("Filter by tag");
      expect(docPaneSrc).not.toContain("<select");
    });

    it("1.3 DocPaneProps explicitly requires profileTag: string and respects it", () => {
      expect(docPaneSrc).toContain("profileTag: string;");
      expect(docPaneSrc).toContain("export function DocPane({ j, api, tags, profileTag,");
    });

    it("1.4 JobBuilders maintains profileTag and threads it to DocPane and generate endpoints", () => {
      expect(buildersSrc).toContain("const [profileTag, setProfileTag] = useState<string>(\"\");");
      expect(buildersSrc).toContain("profileTag={profileTag}");
      expect(buildersSrc).toContain('if (profileTag) params.set("tag_id", profileTag);');
    });

    it("1.5 DocPane renders empty state with profileTag context when filtered tag has zero matches", () => {
      const html = renderToStaticMarkup(
        <DocPane
          j={mockLead}
          api={mockApi}
          tags={testTags}
          profileTag="nonexistent-tag"
          onGenerateWithSelection={() => {}}
        />
      );
      expect(html).toContain("Nothing to show for this tag — ingest a resume and tag some points first.");
    });
  });

  /* -------------------------------------------------------------------------
   * CHALLENGE 2: Multi-Tag Track Layout & Column Grid Generation
   * ------------------------------------------------------------------------- */
  describe("Challenge 2: Multi-Tag Track Layout Columns", () => {
    const tagIndex = buildTagIndex(testPointTags);

    it("2.1 When profileTag === '' (Any), renders side-by-side columns for all active tracks plus General", () => {
      const html = renderToStaticMarkup(
        <EntriesPicker
          kind="experience"
          entries={sampleExperience}
          selection={emptySelection()}
          tagFilter=""
          tagIndex={tagIndex}
          tags={testTags}
          onToggleEntry={() => {}}
          onTogglePoint={() => {}}
          onMove={() => {}}
        />
      );

      // Verify each track column is rendered with track header
      expect(html).toContain("SDE");
      expect(html).toContain("ARCH");
      expect(html).toContain("ML");
      expect(html).toContain("CloudOps");
      expect(html).toContain("General");

      // Verify grid CSS is generated with appropriate column count
      expect(html).toContain("grid-template-columns:repeat(5, minmax(0, 1fr))");
    });

    it("2.2 When profileTag is specified (e.g. 'tag-sde'), renders single column for that track", () => {
      const html = renderToStaticMarkup(
        <EntriesPicker
          kind="experience"
          entries={sampleExperience}
          selection={emptySelection()}
          tagFilter="tag-sde"
          tagIndex={tagIndex}
          tags={testTags}
          onToggleEntry={() => {}}
          onTogglePoint={() => {}}
          onMove={() => {}}
        />
      );

      // Only SDE header is present
      expect(html).toContain("SDE");
      expect(html).not.toContain("CloudOps");
      expect(html).toContain("grid-template-columns:repeat(1, minmax(0, 1fr))");
    });

    it("2.3 Automatically creates dynamic column for untagged items under 'General'", () => {
      const html = renderToStaticMarkup(
        <EntriesPicker
          kind="experience"
          entries={sampleExperience}
          selection={emptySelection()}
          tagFilter=""
          tagIndex={tagIndex}
          tags={testTags}
          onToggleEntry={() => {}}
          onTogglePoint={() => {}}
          onMove={() => {}}
        />
      );
      expect(html).toContain("General");
      expect(html).toContain("Organized engineering hackathons");
    });
  });

  /* -------------------------------------------------------------------------
   * CHALLENGE 3: Track Accent Colors & Bulk Toggle Invariants
   * ------------------------------------------------------------------------- */
  describe("Challenge 3: Track Accent Styling & Bulk Toggle Oracles", () => {
    it("3.1 getTrackStyle returns correct theme tokens matching reference specification", () => {
      const sdeStyle = getTrackStyle("SDE");
      const archStyle = getTrackStyle("ARCH");
      const mlStyle = getTrackStyle("ML");
      const cloudStyle = getTrackStyle("CloudOps");
      const genStyle = getTrackStyle("General");
      const unknownStyle = getTrackStyle("Product");

      expect(sdeStyle.color).toContain("var(--blue)");
      expect(sdeStyle.bg).toContain("var(--blue-soft)");

      expect(archStyle.color).toContain("var(--purple)");
      expect(archStyle.bg).toContain("var(--purple-soft)");

      expect(mlStyle.color).toContain("var(--teal)");
      expect(mlStyle.bg).toContain("var(--teal-soft)");

      expect(cloudStyle.color).toContain("var(--purple)"); // 'ops' matches arch/infra/cloud/ops

      expect(genStyle.bg).toContain("var(--yellow-soft");
      expect(unknownStyle.color).toContain("var(--ink-2)");
    });

    it("3.2 Bulk toggle switches label between 'all' and 'none' based on column point selection state", () => {
      const tagIndex = buildTagIndex(testPointTags);

      // Case A: None selected -> all columns show 'all'
      const htmlNone = renderToStaticMarkup(
        <EntriesPicker
          kind="experience"
          entries={sampleExperience}
          selection={emptySelection()}
          tagFilter=""
          tagIndex={tagIndex}
          tags={testTags}
          onToggleEntry={() => {}}
          onTogglePoint={() => {}}
          onMove={() => {}}
        />
      );
      expect(htmlNone).toContain(">all</button>");
      expect(htmlNone).not.toContain(">none</button>");

      // Case B: In SDE column, points are [p-sde-only, p-sde-arch]. Select only p-sde-only (partial).
      // Partial selection must still show 'all'
      const partialSel: DocSelection = {
        ...emptySelection(),
        points_on: ["p-sde-only"],
      };
      const htmlPartial = renderToStaticMarkup(
        <EntriesPicker
          kind="experience"
          entries={sampleExperience}
          selection={partialSel}
          tagFilter=""
          tagIndex={tagIndex}
          tags={testTags}
          onToggleEntry={() => {}}
          onTogglePoint={() => {}}
          onMove={() => {}}
        />
      );
      // SDE column still shows 'all' because p-sde-arch is unselected
      expect(htmlPartial).toContain('title="Select all points in SDE"');
      expect(htmlPartial).toContain(">all</button>");

      // Case C: In SDE column, select both p-sde-only and p-sde-arch (full).
      const fullSel: DocSelection = {
        ...emptySelection(),
        points_on: ["p-sde-only", "p-sde-arch"],
      };
      const htmlFull = renderToStaticMarkup(
        <EntriesPicker
          kind="experience"
          entries={sampleExperience}
          selection={fullSel}
          tagFilter=""
          tagIndex={tagIndex}
          tags={testTags}
          onToggleEntry={() => {}}
          onTogglePoint={() => {}}
          onMove={() => {}}
        />
      );
      // SDE column now shows 'none'
      expect(htmlFull).toContain('title="Unselect all points in SDE"');
      expect(htmlFull).toContain(">none</button>");
    });

    it("3.3 toggleAllPoints idempotently selects and deselects column points without duplicate IDs", () => {
      let sel = emptySelection();
      const sdePointIds = ["p-sde-only", "p-sde-arch"];

      // Select all SDE points
      sel = toggleAllPoints(sel, sdePointIds, true);
      expect(sel.points_on.sort()).toEqual(["p-sde-arch", "p-sde-only"]);

      // Re-invoking toggleAllPoints(..., true) does NOT produce duplicate IDs
      sel = toggleAllPoints(sel, sdePointIds, true);
      expect(sel.points_on.sort()).toEqual(["p-sde-arch", "p-sde-only"]);

      // Deselect all SDE points
      sel = toggleAllPoints(sel, sdePointIds, false);
      expect(sel.points_on).toEqual([]);
    });
  });

  /* -------------------------------------------------------------------------
   * CHALLENGE 4: Composite Badges, Star Flagship, & Alt Wordings
   * ------------------------------------------------------------------------- */
  describe("Challenge 4: Composite Badges, Flagship Star, & Alternate Wording Indicators", () => {
    it("4.1 CompositeTrackBadge renders alphabetically sorted badge pills for multi-track points", () => {
      // 2 tracks: SDE and ARCH -> [ARCH+SDE]
      const badge2 = renderToStaticMarkup(<CompositeTrackBadge names={["SDE", "ARCH"]} />);
      expect(badge2).toContain("[ARCH+SDE]");
      expect(badge2).toContain('title="Also shown in the other column — counted once in the output"');

      // 3 tracks: ML, ARCH, CloudOps -> [ARCH+CloudOps+ML]
      const badge3 = renderToStaticMarkup(<CompositeTrackBadge names={["ML", "ARCH", "CloudOps"]} />);
      expect(badge3).toContain("[ARCH+CloudOps+ML]");

      // Single track: SDE -> empty (null)
      const badgeSingle = renderToStaticMarkup(<CompositeTrackBadge names={["SDE"]} />);
      expect(badgeSingle).toBe("");

      // Zero track -> empty (null)
      const badgeEmpty = renderToStaticMarkup(<CompositeTrackBadge names={[]} />);
      expect(badgeEmpty).toBe("");
    });

    it("4.2 Flagship star ★ renders for starred points with correct tooltip", () => {
      const tagIndex = buildTagIndex(testPointTags);
      const html = renderToStaticMarkup(
        <EntriesPicker
          kind="experience"
          entries={sampleExperience}
          selection={emptySelection()}
          tagFilter=""
          tagIndex={tagIndex}
          tags={testTags}
          onToggleEntry={() => {}}
          onTogglePoint={() => {}}
          onMove={() => {}}
        />
      );

      expect(html).toContain("★");
      expect(html).toContain('title="Strongest line in this block"');
    });

    it("4.3 Alt wording badge renders for variant points with Set and Map contracts", () => {
      // Set variant
      const setBadge = renderToStaticMarkup(<AltWordingBadge note="Alt wording" />);
      expect(setBadge).toContain("[Alt wording]");
      expect(setBadge).toContain('title="Alternative wording of the same work — pick one, not both"');

      // Custom note variant
      const customBadge = renderToStaticMarkup(<AltWordingBadge note="4-node k3s cluster metric" />);
      expect(customBadge).toContain("[4-node k3s cluster metric]");
    });

    it("4.4 FormattedText safely renders markdown bold <b> and code <code> elements without raw HTML tags", () => {
      const rendered = renderToStaticMarkup(
        <FormattedText text="Built **Kafka messaging** on `k3s` cluster with **Longhorn**" />
      );
      expect(rendered).toContain("<b>Kafka messaging</b>");
      expect(rendered).toContain("<b>Longhorn</b>");
      expect(rendered).toContain("<code");
      expect(rendered).toContain("k3s</code>");
    });
  });

  /* -------------------------------------------------------------------------
   * CHALLENGE 5: Cross-Column Checkbox Synchronization & Export Deduplication
   * ------------------------------------------------------------------------- */
  describe("Challenge 5: Cross-Column Checkbox Synchronization & Export Deduplication", () => {
    const tagIndex = buildTagIndex(testPointTags);

    it("5.1 Compound point checked state is synchronized across all rendered track columns", () => {
      // p-sde-arch is in both SDE and ARCH columns.
      // Initially unselected.
      const unselHtml = renderToStaticMarkup(
        <EntriesPicker
          kind="experience"
          entries={sampleExperience}
          selection={emptySelection()}
          tagFilter=""
          tagIndex={tagIndex}
          tags={testTags}
          onToggleEntry={() => {}}
          onTogglePoint={() => {}}
          onMove={() => {}}
        />
      );
      // No checkboxes are checked
      const unselCheckedCount = (unselHtml.match(/checked=""/g) || []).length;
      expect(unselCheckedCount).toBe(0);

      // Now toggle p-sde-arch ON
      const checkedSel: DocSelection = {
        ...emptySelection(),
        points_on: ["p-sde-arch"],
      };
      const checkedHtml = renderToStaticMarkup(
        <EntriesPicker
          kind="experience"
          entries={sampleExperience}
          selection={checkedSel}
          tagFilter=""
          tagIndex={tagIndex}
          tags={testTags}
          onToggleEntry={() => {}}
          onTogglePoint={() => {}}
          onMove={() => {}}
        />
      );

      // In the rendered HTML, p-sde-arch appears under SDE column AND under ARCH column.
      // Both checkboxes must be rendered checked!
      const checkedCount = (checkedHtml.match(/checked=""/g) || []).length;
      expect(checkedCount).toBe(2);
    });

    it("5.2 deduplicateSelection strips duplicate IDs across all 7 selection arrays", () => {
      const dirtySelection: DocSelection = {
        version: 1,
        points_on: ["p1", "p2", "p1", "p3", "p2", "p3", "p1"],
        skills_on: ["sk-go", "sk-rust", "sk-go", "sk-go"],
        experience_on: ["exp-1", "exp-2", "exp-1"],
        projects_on: ["proj-1", "proj-1", "proj-2"],
        experience_order: ["exp-2", "exp-1", "exp-2"],
        projects_order: ["proj-2", "proj-1", "proj-2"],
        sections: ["skills", "experience", "skills", "projects", "experience"],
      };

      const clean = deduplicateSelection(dirtySelection);

      expect(clean.points_on).toEqual(["p1", "p2", "p3"]);
      expect(clean.skills_on).toEqual(["sk-go", "sk-rust"]);
      expect(clean.experience_on).toEqual(["exp-1", "exp-2"]);
      expect(clean.projects_on).toEqual(["proj-1", "proj-2"]);
      expect(clean.experience_order).toEqual(["exp-2", "exp-1"]);
      expect(clean.projects_order).toEqual(["proj-2", "proj-1"]);
      expect(clean.sections).toEqual(["skills", "experience", "projects"]);
      expect(clean.version).toBe(1);
    });

    it("5.3 deduplicateSelection handles empty and clean selections without mutation", () => {
      const empty = emptySelection();
      const cleanEmpty = deduplicateSelection(empty);
      expect(cleanEmpty.points_on).toEqual([]);
      expect(cleanEmpty.skills_on).toEqual([]);
      expect(cleanEmpty.experience_on).toEqual([]);
      expect(cleanEmpty.projects_on).toEqual([]);
      expect(cleanEmpty.experience_order).toEqual([]);
      expect(cleanEmpty.projects_order).toEqual([]);
      expect(cleanEmpty.sections).toEqual(empty.sections);
    });
  });
});
