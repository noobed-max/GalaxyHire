import { describe, expect, it } from "bun:test";
import {
  emptySelection,
  estimateLines,
  isEmptySelection,
  moveEntry,
  pruneStaleIds,
  toggleAllPoints,
  deduplicateSelection,
  toggleEntry,
  togglePoint,
  toggleSkill,
  entityTitle,
  normalizeSelection,
} from "./selectionState";
import type { ProfileData, ProfileEntry } from "./selectionState";

describe("doc-pane selection state", () => {
  it("starts empty and detects emptiness", () => {
    const s = emptySelection();
    expect(isEmptySelection(s)).toBe(true);
    expect(s.version).toBe(1);
    expect(isEmptySelection(togglePoint(s, "p1"))).toBe(false);
    expect(isEmptySelection(null)).toBe(true);
  });

  it("toggles points and skills without mutating the original", () => {
    const s = emptySelection();
    const withPoint = togglePoint(togglePoint(togglePoint(s, "a"), "b"), "a");
    expect(withPoint.points_on).toEqual(["b"]);
    expect(s.points_on).toEqual([]);
    const withSkill = toggleSkill(s, "sk1");
    expect(withSkill.skills_on).toEqual(["sk1"]);
  });

  it("toggles entries per section", () => {
    let s = emptySelection();
    s = toggleEntry(s, "experience", "e1");
    s = toggleEntry(s, "projects", "pr1");
    expect(s.experience_on).toEqual(["e1"]);
    expect(s.projects_on).toEqual(["pr1"]);
    s = toggleEntry(s, "experience", "e1");
    expect(s.experience_on).toEqual([]);
  });

  it("moves entries within the order array", () => {
    let s = emptySelection();
    s = toggleEntry(s, "projects", "p1");
    s = toggleEntry(s, "projects", "p2");
    s = toggleEntry(s, "projects", "p3");
    // seed captures the current toggle sequence
    s = moveEntry(s, "projects", "p3", -1);
    expect(s.projects_order).toEqual(["p1", "p3", "p2"]);
    s = moveEntry(s, "projects", "p1", -1); // already first -> no-op
    expect(s.projects_order).toEqual(["p1", "p3", "p2"]);
    s = moveEntry(s, "projects", "p2", 1); // already last -> no-op
    expect(s.projects_order).toEqual(["p1", "p3", "p2"]);
  });

  it("prunes stale ids and reports how many dropped", () => {
    const s = {
      ...emptySelection(),
      skills_on: ["sk1", "ghost"],
      experience_on: ["e1", "gone"],
      projects_on: ["p1"],
      points_on: ["pt1", "pt2", "dead"],
    };
    const { selection, dropped } = pruneStaleIds(s, {
      skills: ["sk1"],
      experience: ["e1"],
      projects: ["p1"],
      points: ["pt1", "pt2"],
    });
    expect(dropped).toBe(3);
    expect(selection.skills_on).toEqual(["sk1"]);
    expect(selection.experience_on).toEqual(["e1"]);
    expect(selection.projects_on).toEqual(["p1"]);
    expect(selection.points_on).toEqual(["pt1", "pt2"]);
  });

  it("estimates one-page density from picked content", () => {
    const profile: ProfileData = {
      skills: [{ id: "s1", n: "Rust" }, { id: "s2", n: "SQL" }],
      exp: [{ id: "e1", role: "Intern", points: [{ id: "pe1", text: "x".repeat(250) }] }],
      projects: [],
    };
    // nothing picked -> zero
    expect(estimateLines(profile, emptySelection())).toBe(0);
    let sel = emptySelection();
    sel = toggleSkill(sel, "s1");
    sel = togglePoint(sel, "pe1");
    const lines = estimateLines(profile, sel);
    // headers (4 + 1.5*3 sections, no summary) + entry title 1.4 + ceil(250/100)=3 + skill line ceil(4/100)=1
    expect(lines).toBe(Math.ceil(4 + 4.5 + 1.4 + 3 + 1));
  });

  it("bulk toggles points with toggleAllPoints cleanly without duplicates", () => {
    let s = emptySelection();
    // Initially none selected -> should turn all on
    s = toggleAllPoints(s, ["p1", "p2", "p3"]);
    expect(s.points_on.sort()).toEqual(["p1", "p2", "p3"]);

    // If all are selected -> should turn all off
    s = toggleAllPoints(s, ["p1", "p2"]);
    expect(s.points_on).toEqual(["p3"]);

    // Partial selection: p1 is off, p3 is on -> should turn p1 and p3 both on
    s = toggleAllPoints(s, ["p1", "p3"]);
    expect(s.points_on.sort()).toEqual(["p1", "p3"]);

    // Explicit targetState = false -> turns specified off
    s = toggleAllPoints(s, ["p1"], false);
    expect(s.points_on).toEqual(["p3"]);

    // Explicit targetState = true with overlapping items -> enforces uniqueness
    s = toggleAllPoints(s, ["p3", "p4"], true);
    expect(s.points_on.sort()).toEqual(["p3", "p4"]);
  });

  it("deduplicates all ID arrays using deduplicateSelection", () => {
    const dirty = {
      ...emptySelection(),
      points_on: ["p1", "p2", "p1", "p3", "p2"],
      skills_on: ["s1", "s1", "s2"],
      experience_on: ["e1", "e1"],
      projects_on: ["pr1", "pr2", "pr1"],
      experience_order: ["e1", "e2", "e1"],
      projects_order: ["pr1", "pr1"],
      sections: ["skills", "skills", "experience"],
    };

    const clean = deduplicateSelection(dirty);
    expect(clean.points_on).toEqual(["p1", "p2", "p3"]);
    expect(clean.skills_on).toEqual(["s1", "s2"]);
    expect(clean.experience_on).toEqual(["e1"]);
    expect(clean.projects_on).toEqual(["pr1", "pr2"]);
    expect(clean.experience_order).toEqual(["e1", "e2"]);
    expect(clean.projects_order).toEqual(["pr1"]);
    expect(clean.sections).toEqual(["skills", "experience"]);
  });

  it("normalizes legacy saved selections and only accepts a known title variant", () => {
    const legacy = normalizeSelection({ points_on: ["p1"], entity_titles: undefined });
    expect(legacy.entity_titles).toEqual({});
    expect(legacy.sections).toEqual(["skills", "experience", "projects"]);

    const entry: ProfileEntry = {
      id: "exp-1",
      role: "Intern",
      role_variants: [{ title: "Intern" }, { title: "AI Intern" }],
      points: [],
    };
    expect(entityTitle(entry, { entity_titles: { "exp-1": "AI Intern" } })).toBe("AI Intern");
    expect(entityTitle(entry, { entity_titles: { "exp-1": "internal-id-or-stale-title" } })).toBe("Intern");
  });
});
