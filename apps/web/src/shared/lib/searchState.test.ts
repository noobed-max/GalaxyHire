import { describe, expect, it } from "bun:test";
import { activeFilterLabels, dashboardFindRequest, roleFromBrief } from "./searchState";

describe("search state", () => {
  it("previews only the positive role from the reported search brief", () => {
    expect(roleFromBrief(
      "Software engineer, no senior, no 3+ years of experience, no SDE 2, no SDE 3",
    )).toBe("Software engineer");
  });

  it("turns inferred constraints into plain-language visible chips", () => {
    const labels = activeFilterLabels({
      max_seniority: "mid",
      max_years: 2,
      negative_phrases: ["senior", "SDE 2", "SDE 3"],
    });

    expect(labels).toContain("Up to mid-level");
    expect(labels).toContain("Up to 2 years required");
    expect(labels).toContain("Exclude senior");
    expect(labels).toContain("Exclude SDE 3");
  });

  it("marks the dashboard Find request as a fresh scrape", () => {
    expect(dashboardFindRequest("  backend engineer ", ["greenhouse"], " Berlin ")).toEqual({
      query: "backend engineer",
      portals: ["greenhouse"],
      location: "Berlin",
      force_scrape: true,
    });
  });
});
