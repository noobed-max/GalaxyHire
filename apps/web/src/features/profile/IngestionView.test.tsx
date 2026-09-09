import { describe, expect, it } from "bun:test";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { IngestionView, IngestionStepper, ThoughtPreviewBox, IngestionProgressCard } from "./IngestionView";
import { Topbar } from "../../shared/components/Topbar";
import type { ApiFetch } from "../../types";
import type { IngestionJob } from "../../api/types";

const mockApi = (async () => ({
  ok: true,
  status: 200,
  json: async () => ({ documents: [], tags: [], point_tags: [], status: "idle" }),
})) as unknown as ApiFetch;

describe("Milestone 1: Async Ingestion & Session Persistence UI", () => {
  it("keeps upload assignment separate from the document-library filter", () => {
    const source = readFileSync(new URL("./IngestionView.tsx", import.meta.url), "utf8");
    expect(source).toContain('aria-label="Assign uploaded file to profile tag"');
    expect(source).toContain('if (uploadTag) fd.append("tag_id", uploadTag);');
    expect(source).toContain("tagId: data.tag_id || uploadTag || null");
  });

  it("renders idle upload view when no ingestion task is active", () => {
    const html = renderToStaticMarkup(<IngestionView api={mockApi} />);
    expect(html).toContain("Upload a document");
    expect(html).toContain("Upload Resume");
    expect(html).toContain("Upload Cover Letter");
    expect(html).not.toContain("Resume parsing in progress");
  });

  it("renders 4-stage stepper with visual status cues", () => {
    // Stage 2 active (processing)
    const stepperHtml = renderToStaticMarkup(
      <IngestionStepper stage={2} status="processing" />
    );

    // Stage 1 completed (Done badge)
    expect(stepperHtml).toContain("Reading document &amp; extracting text...");
    expect(stepperHtml).toContain("Done");

    // Stage 2 active (Active pulse-soft badge)
    expect(stepperHtml).toContain("Analyzing experiences &amp; projects with AI...");
    expect(stepperHtml).toContain("Active");
    expect(stepperHtml).toContain("pulse-soft");

    // Stage 3 & 4 pending
    expect(stepperHtml).toContain("Cross-referencing existing profile for duplicates...");
    expect(stepperHtml).toContain("Indexing skills and tags...");
    expect(stepperHtml).toContain("Pending");
  });

  it("renders all 4 stages completed when status is completed", () => {
    const stepperHtml = renderToStaticMarkup(
      <IngestionStepper stage={4} status="completed" />
    );
    expect(stepperHtml).toContain("Done");
    expect(stepperHtml).not.toContain("Active");
    expect(stepperHtml).not.toContain("Pending");
  });

  it("renders collapsible monospace thought box with line counter and line numbers", () => {
    const thoughts = [
      "Reading document: resume.pdf (48 KB)...",
      "Extracted 4,210 characters of plain text.",
      "Analyzing experiences & projects with AI...",
    ];
    const html = renderToStaticMarkup(
      <ThoughtPreviewBox thoughts={thoughts} isProcessing={true} />
    );

    expect(html).toContain("AI Reasoning &amp; Extraction Log");
    expect(html).toContain("[ 3 lines ]");
    expect(html).toContain("01");
    expect(html).toContain("02");
    expect(html).toContain("03");
    expect(html).toContain("Reading document: resume.pdf");
    expect(html).toContain("STREAMING");
  });

  it("renders IngestionProgressCard with locked state and filename", () => {
    const job: IngestionJob = {
      taskId: "task-test-123",
      status: "processing",
      stage: 2,
      stageLabel: "Analyzing experiences & projects with AI...",
      filename: "Staff_SDE_Resume.pdf",
      startedAt: Date.now() - 15000,
      elapsedSeconds: 15,
      thoughts: ["Extracting skills", "Identifying roles"],
      hasStagedDuplicates: false,
      error: null,
    };

    const html = renderToStaticMarkup(
      <IngestionProgressCard
        job={job}
        onCancel={() => {}}
        onDismiss={() => {}}
      />
    );

    expect(html).toContain("Resume parsing in progress: Staff_SDE_Resume.pdf");
    expect(html).toContain("15s elapsed");
    expect(html).toContain("Cancel");
    expect(html).toContain("Analyzing experiences &amp; projects with AI...");
    expect(html).toContain("[ 2 lines ]");
  });

  it("renders duplicate detection warning when hasStagedDuplicates is true", () => {
    const job: IngestionJob = {
      taskId: "task-dupes-123",
      status: "completed",
      stage: 4,
      stageLabel: "Indexing skills and tags...",
      filename: "My_Resume_v2.pdf",
      startedAt: Date.now() - 30000,
      elapsedSeconds: 30,
      thoughts: ["Detected 2 duplicate pairs"],
      hasStagedDuplicates: true,
      error: null,
    };

    const html = renderToStaticMarkup(
      <IngestionProgressCard
        job={job}
        onCancel={() => {}}
        onDismiss={() => {}}
      />
    );

    expect(html).toContain("Parsing Complete: My_Resume_v2.pdf");
    expect(html).toContain("Similar / duplicate points detected against existing profile!");
    expect(html).toContain("Upload Another Resume");
  });

  it("locks upload form in IngestionView when active ingestion is processing", () => {
    const activeJob: IngestionJob = {
      taskId: "task-active-456",
      status: "processing",
      stage: 1,
      stageLabel: "Reading document & extracting text...",
      filename: "Candidate_Resume.pdf",
      startedAt: Date.now(),
      elapsedSeconds: 5,
      thoughts: ["Reading document..."],
      error: null,
    };

    const html = renderToStaticMarkup(
      <IngestionView api={mockApi} activeIngestion={activeJob} />
    );

    expect(html).toContain("Resume parsing in progress: Candidate_Resume.pdf");
    expect(html).toContain("Upload form locked");
    expect(html).toContain("Parsing In Progress...");
  });

  it("renders active ingestion indicator pill in Topbar when task is processing", () => {
    const activeJob: IngestionJob = {
      taskId: "task-topbar-789",
      status: "processing",
      stage: 3,
      stageLabel: "Cross-referencing existing profile for duplicates...",
      filename: "Jane_Doe_Resume.pdf",
      startedAt: Date.now() - 25000,
      elapsedSeconds: 25,
      thoughts: [],
      error: null,
    };

    const html = renderToStaticMarkup(
      <Topbar view="dashboard" activeIngestion={activeJob} />
    );

    expect(html).toContain("ingestion-indicator-pill");
    expect(html).toContain("[ ⟳ Parsing Jane_Doe_Resume.pdf · Stage 3/4 (25s) ]");
  });

  it("hides active ingestion indicator pill in Topbar when status is completed", () => {
    const activeJob: IngestionJob = {
      taskId: "task-done",
      status: "completed",
      stage: 4,
      stageLabel: "Done",
      filename: "Jane_Doe_Resume.pdf",
      startedAt: Date.now() - 40000,
      elapsedSeconds: 40,
      thoughts: [],
      error: null,
    };

    const html = renderToStaticMarkup(
      <Topbar view="dashboard" activeIngestion={activeJob} />
    );

    expect(html).not.toContain("ingestion-indicator-pill");
  });
});
