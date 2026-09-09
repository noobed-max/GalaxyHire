import { describe, expect, it } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { EntriesPicker, FormattedText, CompositeTrackBadge, AltWordingBadge, getTrackStyle, buildTagIndex, type Tag, type PointTagRow } from "./Pickers";
import { DocPane } from "./DocPane";
import { emptySelection, togglePoint, toggleAllPoints, deduplicateSelection, type ProfileEntry } from "./selectionState";
import type { ApiFetch, Lead } from "../../../types";

const mockLead: Lead = {
  job_id: "lead-m4-1",
  title: "Staff Systems Engineer",
  company: "CloudScale Systems",
  url: "https://example.com/jobs/lead-m4-1",
  platform: "manual",
  status: "approved",
  asset: "",
  score: 100,
  reason: "match",
  match_points: [],
};

const mockApi = (async (path: string) => {
  if (path.includes("/profile")) {
    return {
      ok: true,
      json: async () => ({
        skills: [{ id: "sk1", n: "Go" }],
        exp: [
          {
            id: "exp-1",
            role: "Senior Backend Engineer",
            co: "SuprMentr",
            period: "Feb 2026 – May 2026",
            points: [
              { id: "pt-1", text: "Scaled **Kafka** messaging pipeline on `k3s` cluster", star: true },
              { id: "pt-2", text: "Designed resilient Longhorn storage replication", star: false },
              { id: "pt-3", text: "Trained transformer model for text extraction", note: "Alternative phrasing" },
              { id: "pt-4", text: "Mentored junior engineers across the org" },
            ],
          },
        ],
        projects: [],
      }),
    } as Response;
  }
  if (path.includes("/point-tags")) {
    return {
      ok: true,
      json: async () => ({
        point_tags: [
          { point_kind: "experience", point_id: "pt-1", tag_id: "sde", tag_name: "SDE" },
          { point_kind: "experience", point_id: "pt-1", tag_id: "arch", tag_name: "ARCH" },
          { point_kind: "experience", point_id: "pt-2", tag_id: "arch", tag_name: "ARCH" },
          { point_kind: "experience", point_id: "pt-3", tag_id: "ml", tag_name: "ML" },
        ],
      }),
    } as Response;
  }
  if (path.includes("/doc-selections")) {
    return {
      ok: true,
      json: async () => ({ selection: emptySelection() }),
    } as Response;
  }
  if (path.includes("/doc-presets") || path.includes("/conflicts")) {
    return {
      ok: true,
      json: async () => ({ presets: [], groups: [] }),
    } as Response;
  }
  return { ok: true, json: async () => ({}) } as Response;
}) as unknown as ApiFetch;

describe("Milestone 4: Single Tag Selector Unification & Multi-Tag Track Layout", () => {
  const tags: Tag[] = [
    { id: "sde", name: "SDE" },
    { id: "arch", name: "ARCH" },
    { id: "ml", name: "ML" },
  ];

  const pointTags: PointTagRow[] = [
    { point_kind: "experience", point_id: "pt-1", tag_id: "sde", tag_name: "SDE" },
    { point_kind: "experience", point_id: "pt-1", tag_id: "arch", tag_name: "ARCH" },
    { point_kind: "experience", point_id: "pt-2", tag_id: "arch", tag_name: "ARCH" },
    { point_kind: "experience", point_id: "pt-3", tag_id: "ml", tag_name: "ML" },
  ];

  const tagIndex = buildTagIndex(pointTags);

  const sampleEntries: ProfileEntry[] = [
    {
      id: "exp-1",
      role: "Senior Systems Engineer",
      co: "SuprMentr",
      period: "Feb 2026 – May 2026",
      points: [
        { id: "pt-1", text: "Scaled **Kafka** messaging on `k3s` cluster", star: true },
        { id: "pt-2", text: "Architected distributed Longhorn block storage", star: false },
        { id: "pt-3", text: "Trained recommendation engine with PyTorch", note: "Alt wording" },
        { id: "pt-4", text: "Managed cross-functional agile sprints" },
      ],
    },
  ];

  it("1. DocPane unifies on profileTag and eliminates internal tag dropdown", () => {
    // Render DocPane with profileTag = "" (Any)
    const markupAny = renderToStaticMarkup(
      <DocPane
        j={mockLead}
        api={mockApi}
        tags={tags}
        profileTag=""
        onGenerateWithSelection={() => {}}
      />
    );
    // Does NOT contain internal "Filter by tag" dropdown
    expect(markupAny).not.toContain("Filter by tag");
    expect(markupAny).not.toContain("<select");

    // Render DocPane with an empty tag filter having no content
    const markupMissingTag = renderToStaticMarkup(
      <DocPane
        j={mockLead}
        api={mockApi}
        tags={tags}
        profileTag="devops"
        onGenerateWithSelection={() => {}}
      />
    );
    expect(markupMissingTag).toContain("Nothing to show for this tag — ingest a resume and tag some points first.");
  });

  it("2. EntriesPicker renders side-by-side columns per track when tagFilter is empty", () => {
    const selection = emptySelection();
    const html = renderToStaticMarkup(
      <EntriesPicker
        kind="experience"
        entries={sampleEntries}
        selection={selection}
        tagFilter=""
        tagIndex={tagIndex}
        tags={tags}
        onToggleEntry={() => {}}
        onTogglePoint={() => {}}
        onMove={() => {}}
      />
    );

    // Should render SDE, ARCH, ML and General track headers
    expect(html).toContain("SDE");
    expect(html).toContain("ARCH");
    expect(html).toContain("ML");
    expect(html).toContain("General");
  });

  it("3. Track headers show track accent colors matching reference specification", () => {
    const sdeStyle = getTrackStyle("SDE");
    const archStyle = getTrackStyle("ARCH");
    const mlStyle = getTrackStyle("ML");
    const generalStyle = getTrackStyle("General");

    expect(sdeStyle.color).toContain("var(--blue)");
    expect(archStyle.color).toContain("var(--purple)");
    expect(mlStyle.color).toContain("var(--teal)");
    expect(generalStyle.bg).toContain("var(--yellow-soft");
  });

  it("4. Column bulk toggle shows 'all' when unselected and 'none' when all points are selected", () => {
    // All points unselected -> all buttons should display "all"
    const unselectedHtml = renderToStaticMarkup(
      <EntriesPicker
        kind="experience"
        entries={sampleEntries}
        selection={emptySelection()}
        tagFilter=""
        tagIndex={tagIndex}
        tags={tags}
        onToggleEntry={() => {}}
        onTogglePoint={() => {}}
        onMove={() => {}}
      />
    );
    expect(unselectedHtml).toContain(">all</button>");

    // Select all points in SDE column (pt-1)
    const sdeSelected = {
      ...emptySelection(),
      points_on: ["pt-1"],
    };
    const sdeHtml = renderToStaticMarkup(
      <EntriesPicker
        kind="experience"
        entries={sampleEntries}
        selection={sdeSelected}
        tagFilter=""
        tagIndex={tagIndex}
        tags={tags}
        onToggleEntry={() => {}}
        onTogglePoint={() => {}}
        onMove={() => {}}
      />
    );
    // SDE column has only pt-1, so SDE header displays "none"
    expect(sdeHtml).toContain(">none</button>");
  });

  it("5. Composite badges [ARCH+SDE] render for multi-track points and omit for single-track points", () => {
    // pt-1 belongs to both SDE and ARCH -> should render [ARCH+SDE]
    const badgePt1 = renderToStaticMarkup(<CompositeTrackBadge names={["SDE", "ARCH"]} />);
    expect(badgePt1).toContain("[ARCH+SDE]");

    // Single-track point belongs to only ML -> should omit composite badge
    const badgePt3 = renderToStaticMarkup(<CompositeTrackBadge names={["ML"]} />);
    expect(badgePt3).toBe("");
  });

  it("6. Flagship star ★ renders for starred points with correct tooltip", () => {
    const html = renderToStaticMarkup(
      <EntriesPicker
        kind="experience"
        entries={sampleEntries}
        selection={emptySelection()}
        tagFilter=""
        tagIndex={tagIndex}
        tags={tags}
        onToggleEntry={() => {}}
        onTogglePoint={() => {}}
        onMove={() => {}}
      />
    );
    expect(html).toContain("★");
    expect(html).toContain('title="Strongest line in this block"');
  });

  it("7. Alternate wording indicator [Alt wording] renders for duplicate variants", () => {
    const altBadge = renderToStaticMarkup(<AltWordingBadge note="Alt wording" />);
    expect(altBadge).toContain("[Alt wording]");
    expect(altBadge).toContain('title="Alternative wording of the same work — pick one, not both"');
  });

  it("8. FormattedText safely renders markdown bold <b> and code <code> elements", () => {
    const formatted = renderToStaticMarkup(
      <FormattedText text="Built **Kafka** messaging on `k3s` cluster" />
    );
    expect(formatted).toContain("<b>Kafka</b>");
    expect(formatted).toContain("<code");
    expect(formatted).toContain("k3s</code>");
  });

  it("9. Cross-column selection synchronization and export deduplication", () => {
    let sel = emptySelection();
    // Toggle multi-track point pt-1 (present in both SDE and ARCH)
    sel = togglePoint(sel, "pt-1");
    expect(sel.points_on).toEqual(["pt-1"]);

    // Render entries with pt-1 checked
    const html = renderToStaticMarkup(
      <EntriesPicker
        kind="experience"
        entries={sampleEntries}
        selection={sel}
        tagFilter=""
        tagIndex={tagIndex}
        tags={tags}
        onToggleEntry={() => {}}
        onTogglePoint={() => {}}
        onMove={() => {}}
      />
    );

    // In the HTML, pt-1 appears in both SDE and ARCH columns, and both checkboxes are checked
    const checkedCount = (html.match(/checked=""/g) || []).length;
    // pt-1 is checked in SDE and checked in ARCH (at least 2 checked checkboxes for bullets)
    expect(checkedCount).toBeGreaterThanOrEqual(2);

    // Now bulk-toggle ARCH column ([pt-1, pt-2])
    sel = toggleAllPoints(sel, ["pt-1", "pt-2"], true);
    // Enforces uniqueness: pt-1 is not duplicated
    expect(sel.points_on.sort()).toEqual(["pt-1", "pt-2"]);

    // Deduplicate selection test
    const dirtySelection = {
      ...sel,
      points_on: ["pt-1", "pt-2", "pt-1", "pt-3", "pt-2"],
    };
    const cleanSelection = deduplicateSelection(dirtySelection);
    expect(cleanSelection.points_on).toEqual(["pt-1", "pt-2", "pt-3"]);
  });

  it("10. Selected tag scopes to its bullets without leaking bullets from other tracks", () => {
    const html = renderToStaticMarkup(
      <EntriesPicker
        kind="experience"
        entries={sampleEntries}
        selection={emptySelection()}
        tagFilter="sde"
        tagIndex={tagIndex}
        tags={tags}
        onToggleEntry={() => {}}
        onTogglePoint={() => {}}
        onMove={() => {}}
      />
    );

    expect(html).toContain("SDE");
    expect(html).toContain("Scaled <b>Kafka</b> messaging on");
    expect(html).not.toContain("Architected distributed Longhorn block storage");
    expect(html).not.toContain("Trained recommendation engine with PyTorch");
    expect(html).not.toContain("Managed cross-functional agile sprints");
  });

  it("11. Any renders a canonical multi-tag bullet in each track column while export ids stay unique", () => {
    const html = renderToStaticMarkup(
      <EntriesPicker
        kind="experience"
        entries={sampleEntries}
        selection={emptySelection()}
        tagFilter=""
        tagIndex={tagIndex}
        tags={tags}
        onToggleEntry={() => {}}
        onTogglePoint={() => {}}
        onMove={() => {}}
      />
    );

    const canonicalBulletCount = (html.match(/<b>Kafka<\/b>/g) || []).length;
    expect(canonicalBulletCount).toBe(2);
    expect(deduplicateSelection({ ...emptySelection(), points_on: ["pt-1", "pt-1"] }).points_on).toEqual(["pt-1"]);
  });

  it("12. Shared entities expose mutually-exclusive source title variants", () => {
    const entry: ProfileEntry = {
      id: "suprmentr-exp",
      role: "Intern",
      co: "SuprMentr",
      period: "Feb 2026 – May 2026",
      role_variants: [
        { title: "Intern", tag_id: "sde" },
        { title: "AI Intern", tag_id: "ml" },
      ],
      points: [
        { id: "sde-point", text: "Built the SDE platform", tag_ids: ["sde"] },
        { id: "ml-point", text: "Engineered the AI generator", tag_ids: ["ml"] },
      ],
    };
    const html = renderToStaticMarkup(
      <EntriesPicker
        kind="experience"
        entries={[entry]}
        selection={{ ...emptySelection(), entity_titles: { "suprmentr-exp": "AI Intern" } }}
        tagFilter=""
        tagIndex={buildTagIndex([])}
        tags={[{ id: "sde", name: "SDE" }, { id: "ml", name: "ML" }]}
        onToggleEntry={() => {}}
        onTogglePoint={() => {}}
        onMove={() => {}}
      />
    );
    expect(html).toContain("AI Intern");
    expect(html).toContain('role="radiogroup"');
    expect(html).toContain('role="radio"');
    expect(html).toContain('aria-checked="true"');
    expect((html.match(/role="radio"/g) || []).length).toBe(2);
    expect(html).not.toContain("General");

    const sde = renderToStaticMarkup(
      <EntriesPicker kind="experience" entries={[entry]} selection={emptySelection()} tagFilter="sde"
        tagIndex={buildTagIndex([])} tags={[{ id: "sde", name: "SDE" }, { id: "ml", name: "ML" }]}
        onToggleEntry={() => {}} onTogglePoint={() => {}} onMove={() => {}} />
    );
    expect(sde).toContain("Built the SDE platform");
    expect(sde).not.toContain("Engineered the AI generator");
  });
});
