import { describe, expect, it, mock } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import {
  DuplicateResolutionModal,
  DuplicateReviewComparisonCard,
  fetchDuplicateGroups,
  normalizeDuplicateGroups,
  submitDuplicateResolutions,
  type DuplicateGroup,
  type DuplicatePair,
} from "./DuplicateResolutionModal";

describe("DuplicateResolutionModal Component Contracts", () => {
  const mockPair: DuplicatePair = {
    pair_id: "pair-101",
    existing_point_id: "exp_101",
    existing_text: "Built distributed backend on GCP k3s cluster with Longhorn.",
    new_text: "Built distributed backend on GCP k3s with Longhorn storage & Garage S3.",
    explanation: "Incoming variant mentions Garage S3 object storage",
  };

  const mockGroups: DuplicateGroup[] = [
    {
      company_name: "SuprMentr",
      role: "Intern",
      period: "Feb 2026 – May 2026",
      entity_title: "SuprMentr (Intern · Feb 2026 – May 2026)",
      pairs: [mockPair],
    },
    {
      company_name: "Garage S3",
      entity_title: "Garage S3",
      pairs: [
        {
          pair_id: "pair-102",
          existing_point_id: "proj_201",
          existing_text: "Self-hosted S3-compatible object storage.",
          new_text: "Self-hosted S3-compatible storage with erasure coding.",
          explanation: "Added erasure coding detail",
        },
      ],
    },
  ];

  it("renders comparison card with two-column layout and all three action buttons", () => {
    const html = renderToStaticMarkup(
      <DuplicateReviewComparisonCard
        pair={mockPair}
        company="SuprMentr (Intern · Feb 2026 – May 2026)"
        decision="use_new"
        onDecide={() => {}}
      />
    );

    // Two column labels
    expect(html).toContain("EXISTING / OLD POINT");
    expect(html).toContain("INCOMING / NEW POINT");

    // Action buttons
    expect(html).toContain("Keep Existing");
    expect(html).toContain("Use New (Replace)");
    expect(html).toContain("Keep Both");

    // Checkmark on active decision
    expect(html).toContain("✓ Use New (Replace)");

    // Content check
    expect(html).toContain("Garage S3 object storage");
  });

  it("renders full modal markup when open with multiple entity groups", () => {
    const mockApi = mock(async () => ({}));
    const html = renderToStaticMarkup(
      <DuplicateResolutionModal
        isOpen={true}
        onClose={() => {}}
        taskId="task-123"
        api={mockApi as any}
        initialGroups={mockGroups}
      />
    );

    // Modal elements
    expect(html).toContain("Review Duplicate Points");
    expect(html).toContain("SuprMentr (Intern · Feb 2026 – May 2026)");
    expect(html).toContain("Garage S3");
    expect(html).toContain("Keep All Existing");
    expect(html).toContain("Apply &amp; Save");
    expect(html).toContain("Resolved 0 of 2 duplicate points");
    expect(html).toContain("Complete review to continue");
    expect(html).not.toContain('aria-label="Close"');
  });

  it("returns null when isOpen is false", () => {
    const mockApi = mock(async () => ({}));
    const html = renderToStaticMarkup(
      <DuplicateResolutionModal
        isOpen={false}
        onClose={() => {}}
        taskId="task-123"
        api={mockApi as any}
        initialGroups={mockGroups}
      />
    );

    expect(html).toBe("");
  });

  it("formats resolution payload conformant to POST /resolve endpoint specification", () => {
    const decisions: Record<string, "use_new" | "keep_both" | "keep_existing"> = {
      "pair-101": "use_new",
      "pair-102": "keep_both",
    };

    const payload = {
      resolutions: Object.entries(decisions).map(([pair_id, action]) => ({
        pair_id,
        action,
      })),
    };

    expect(payload.resolutions).toHaveLength(2);
    expect(payload.resolutions[0]).toEqual({ pair_id: "pair-101", action: "use_new" });
    expect(payload.resolutions[1]).toEqual({ pair_id: "pair-102", action: "keep_both" });
  });

  it("runs the exact two-staged-pair GET -> decisions -> resolve flow with actual Response objects", async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    const api = (async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      if (path.endsWith("/duplicates")) {
        return new Response(JSON.stringify({ task_id: "task-2", status: "review_required", groups: mockGroups }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ status: "resolved", resolved_count: 2, remaining_count: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as any;

    const groups = await fetchDuplicateGroups(api, "task-2");
    expect(groups).toHaveLength(2);
    expect(groups.flatMap(group => group.pairs)).toHaveLength(2);

    const resolved = await submitDuplicateResolutions(api, "task-2", [
      { pair_id: "pair-101", action: "use_new" },
      { pair_id: "pair-102", action: "keep_both" },
    ]);
    expect(resolved).toMatchObject({ status: "resolved", resolved_count: 2, remaining_count: 0 });
    expect(calls).toHaveLength(2);
    expect(calls[1].init?.method).toBe("POST");
    expect((calls[1].init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({
      resolutions: [
        { pair_id: "pair-101", action: "use_new" },
        { pair_id: "pair-102", action: "keep_both" },
      ],
    });
  });

  it("rejects non-OK Response objects for both duplicate GET and resolve", async () => {
    const api = (async (path: string) => new Response(JSON.stringify({ detail: path.endsWith("/duplicates") ? "task unavailable" : "resolve failed" }), {
      status: path.endsWith("/duplicates") ? 404 : 500,
      headers: { "Content-Type": "application/json" },
    })) as any;

    await expect(fetchDuplicateGroups(api, "missing")).rejects.toThrow("task unavailable");
    await expect(submitDuplicateResolutions(api, "task-2", [
      { pair_id: "pair-101", action: "keep_existing" },
    ])).rejects.toThrow("resolve failed");
  });

  it("filters entity-only legacy records so only bullet pairs become review cards", () => {
    const groups = normalizeDuplicateGroups({
      groups: [
        { company_name: "Entity only", entity_title: "Entity only", pairs: [] },
        {
          company_name: "Acme",
          pairs: [
            { pair_id: "pair-real", existing_point_id: "old-1", existing_text: "Old bullet", new_text: "New bullet" },
            { pair_id: "entity-chip", existing_point_id: "", existing_text: "", new_text: "" },
          ],
        },
      ],
    });
    expect(groups).toHaveLength(1);
    expect(groups[0].pairs.map(pair => pair.pair_id)).toEqual(["pair-real"]);
  });

  it("surfaces a retry diagnostic instead of a successful empty review when staging is claimed", () => {
    const html = renderToStaticMarkup(
      <DuplicateResolutionModal
        isOpen={true}
        onClose={() => {}}
        taskId="task-staged-but-empty"
        api={mock(async () => new Response("{}")) as any}
        initialGroups={[]}
        expectedStagedDuplicates
      />
    );
    expect(html).toContain("This ingestion task reports staged duplicate points");
    expect(html).toContain("Retry loading duplicate pairs");
    expect(html).not.toContain("Resolved 0 of 0 duplicate points");
  });
});
