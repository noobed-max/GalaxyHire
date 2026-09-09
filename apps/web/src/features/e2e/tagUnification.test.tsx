import { describe, expect, it } from "bun:test";
import { readFileSync } from "node:fs";

describe("R4: Tag Selector Unification E2E AST Contracts", () => {
  const builders = readFileSync(new URL("../pipeline/components/JobBuilders.tsx", import.meta.url), "utf8");
  const docPane = readFileSync(new URL("../pipeline/doc-pane/DocPane.tsx", import.meta.url), "utf8");

  it("JobBuilders maintains profileTag as single source of truth and feeds DocPane prop", () => {
    // profileTag state declaration
    expect(builders).toContain("const [profileTag, setProfileTag] = useState<string>(\"\")");

    // Passed as prop to DocPane
    expect(builders).toContain("profileTag={profileTag}");

    // Controls dropdown selector in header
    expect(builders).toContain("Profile / tag");
    expect(builders).toContain("Profile / tag: any");
  });

  it("DocPane eliminates internal redundant tagFilter dropdown (Flaw D fix)", () => {
    // Redundant internal state removed
    expect(docPane).not.toContain("const [tagFilter, setTagFilter]");

    // Internal dropdown removed
    expect(docPane).not.toContain("Filter by tag");

    // Empty state message uses profileTag
    expect(docPane).toContain('Nothing to show{profileTag ? " for this tag" : ""} — ingest a resume and tag some points first.');
  });

  it("JobBuilders filters uploaded resumes and cover letters using unified profileTag", () => {
    expect(builders).toContain("const tagDocs = profileTag ? docs.filter(d => d.tag_id === profileTag) : docs");
    expect(builders).toContain("const tagResumes = tagDocs.filter(d => d.kind === \"resume\")");
    expect(builders).toContain("const tagCoverLetters = tagDocs.filter(d => d.kind === \"cover_letter\")");
  });

  it("DocPane forwards profileTag into selection persistence and refetches on change", () => {
    expect(docPane).toContain("docPaneApi.getSelection(api, j.job_id, profileTag)");
    expect(docPane).toContain("docPaneApi.putSelection(api, j.job_id, profileTag, deduplicateSelection(selection))");
    expect(docPane).toContain("[api, j.job_id, profileTag]");
  });

  it("PDF generation and suggest endpoints pass profileTag", () => {
    expect(builders).toContain('if (profileTag) params.set("tag_id", profileTag)');
  });
});
