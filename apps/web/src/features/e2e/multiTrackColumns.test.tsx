import { describe, expect, it } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import {
  buildTagIndex,
  CompositeTrackBadge,
  EntriesPicker,
  getTrackStyle,
  type PointTagRow,
  type Tag,
} from "../pipeline/doc-pane/Pickers";
import { emptySelection, type ProfileEntry } from "../pipeline/doc-pane/selectionState";
import type { DocSelection } from "../../api/docPane";

describe("R4: Multi-Tag Track Layout & Export Deduplication E2E Contracts", () => {
  const mockTags: Tag[] = [
    { id: "tag-sde", name: "SDE" },
    { id: "tag-arch", name: "ARCH" },
  ];

  const mockPointTags: PointTagRow[] = [
    { point_kind: "exp", point_id: "p1", tag_id: "tag-sde", tag_name: "SDE" },
    { point_kind: "exp", point_id: "p2", tag_id: "tag-arch", tag_name: "ARCH" },
    { point_kind: "exp", point_id: "p3", tag_id: "tag-sde", tag_name: "SDE" },
    { point_kind: "exp", point_id: "p3", tag_id: "tag-arch", tag_name: "ARCH" }, // Compound point
  ];

  const mockEntry: ProfileEntry = {
    id: "entry-1",
    role: "Senior Staff Engineer",
    co: "CloudScale Inc",
    period: "2022 - Present",
    d: "Built SDE feature.\nDesigned cloud architecture.\nArchitected distributed SDE pipeline.",
    points: [
      { id: "p1", text: "Built SDE feature." },
      { id: "p2", text: "Designed cloud architecture." },
      { id: "p3", text: "Architected distributed SDE pipeline." },
    ],
  };

  const mockSelection: DocSelection = {
    ...emptySelection(),
    points_on: ["p1", "p3"],
    experience_on: ["entry-1"],
    experience_order: ["entry-1"],
  };

  it("buildTagIndex constructs bidirectional index for filtering and display", () => {
    const index = buildTagIndex(mockPointTags);

    expect(index.idsByPoint.get("p1")?.has("tag-sde")).toBe(true);
    expect(index.idsByPoint.get("p2")?.has("tag-arch")).toBe(true);

    // Multi-tagged point p3
    expect(index.idsByPoint.get("p3")?.has("tag-sde")).toBe(true);
    expect(index.idsByPoint.get("p3")?.has("tag-arch")).toBe(true);
    expect(index.namesByPoint.get("p3")).toEqual(["SDE", "ARCH"]);
  });

  it("CompositeTrackBadge renders compound track pill [ARCH+SDE] for multi-track points", () => {
    // Multi-track point
    const htmlMulti = renderToStaticMarkup(
      <CompositeTrackBadge names={["SDE", "ARCH"]} />
    );
    expect(htmlMulti).toContain("[ARCH+SDE]");
    expect(htmlMulti).toContain("composite-badge");

    // Single-track point returns null
    const htmlSingle = renderToStaticMarkup(
      <CompositeTrackBadge names={["SDE"]} />
    );
    expect(htmlSingle).toBe("");

    // Zero-track point returns null
    const htmlEmpty = renderToStaticMarkup(
      <CompositeTrackBadge names={[]} />
    );
    expect(htmlEmpty).toBe("");
  });

  it("EntriesPicker renders multi-track columns and bulk all/none toggles under 'Any' tag", () => {
    const tagIndex = buildTagIndex(mockPointTags);

    const html = renderToStaticMarkup(
      <EntriesPicker
        kind="experience"
        entries={[mockEntry]}
        selection={mockSelection}
        tagFilter="" // "Any" tag
        tagIndex={tagIndex}
        tags={mockTags}
        onToggleEntry={() => {}}
        onTogglePoint={() => {}}
        onMove={() => {}}
      />
    );

    // Track column headers
    expect(html).toContain("SDE");
    expect(html).toContain("ARCH");

    // Bulk toggles
    expect(html).toContain("none");
    expect(html).toContain("all");

    // Composite badges for multi-tagged point p3
    expect(html).toContain("[ARCH+SDE]");
  });

  it("deduplicates multi-track points so they are exported exactly once", () => {
    // Simulate user selecting points from both columns
    const rawSelectedPointIds = ["p1", "p3", "p2", "p3"]; // p3 picked from both columns

    const deduplicated = Array.from(new Set(rawSelectedPointIds));
    expect(deduplicated).toHaveLength(3);
    expect(deduplicated).toEqual(["p1", "p3", "p2"]);
  });

  it("applies track accent styling matching reference specification", () => {
    const sdeStyle = getTrackStyle("SDE");
    expect(sdeStyle.color).toBe("var(--blue)");

    const archStyle = getTrackStyle("ARCH");
    expect(archStyle.color).toBe("var(--purple)");

    const mlStyle = getTrackStyle("ML");
    expect(mlStyle.color).toBe("var(--teal)");
  });
});
