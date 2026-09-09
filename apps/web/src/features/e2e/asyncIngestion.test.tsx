import { describe, expect, it } from "bun:test";
import { readFileSync } from "node:fs";

// Ingestion session contract state types
export interface IngestTaskState {
  taskId: string | null;
  status: "idle" | "processing" | "completed" | "failed" | "review_required";
  stage: "reading" | "extracting" | "deduping" | "indexing" | "completed" | "failed" | "idle";
  stageNumber: number;
  stageMessage: string;
  thoughts: string[];
  filename: string;
  elapsedSeconds: number;
  error: string | null;
  hasStagedDuplicates: boolean;
}

export function initialIngestTaskState(): IngestTaskState {
  return {
    taskId: null,
    status: "idle",
    stage: "idle",
    stageNumber: 0,
    stageMessage: "",
    thoughts: [],
    filename: "",
    elapsedSeconds: 0,
    error: null,
    hasStagedDuplicates: false,
  };
}

export function ingestTaskReducer(
  state: IngestTaskState,
  action:
    | { type: "TASK_STARTED"; taskId: string; filename: string }
    | { type: "STATUS_UPDATE"; payload: Partial<IngestTaskState> }
    | { type: "THOUGHT_APPENDED"; thought: string }
    | { type: "TASK_COMPLETED"; hasStagedDuplicates?: boolean }
    | { type: "TASK_FAILED"; error: string }
    | { type: "RESET" }
): IngestTaskState {
  switch (action.type) {
    case "TASK_STARTED":
      return {
        ...state,
        taskId: action.taskId,
        filename: action.filename,
        status: "processing",
        stage: "reading",
        stageNumber: 1,
        stageMessage: "Reading document & extracting text...",
        thoughts: [],
        error: null,
      };
    case "STATUS_UPDATE":
      return {
        ...state,
        ...action.payload,
      };
    case "THOUGHT_APPENDED":
      if (state.thoughts.includes(action.thought)) return state;
      return {
        ...state,
        thoughts: [...state.thoughts, action.thought],
      };
    case "TASK_COMPLETED":
      return {
        ...state,
        status: action.hasStagedDuplicates ? "review_required" : "completed",
        stage: "completed",
        stageNumber: 4,
        hasStagedDuplicates: Boolean(action.hasStagedDuplicates),
      };
    case "TASK_FAILED":
      return {
        ...state,
        status: "failed",
        stage: "failed",
        error: action.error,
      };
    case "RESET":
      return initialIngestTaskState();
    default:
      return state;
  }
}

describe("R1: Asynchronous Ingestion & Session Persistence E2E Contracts", () => {
  it("ingestion state reducer handles full 4-stage progression and thought streaming", () => {
    let state = initialIngestTaskState();
    expect(state.status).toBe("idle");

    // 1. Task start
    state = ingestTaskReducer(state, {
      type: "TASK_STARTED",
      taskId: "task-uuid-101",
      filename: "alex_resume.pdf",
    });
    expect(state.taskId).toBe("task-uuid-101");
    expect(state.status).toBe("processing");
    expect(state.stageNumber).toBe(1);

    // 2. Thought streaming
    state = ingestTaskReducer(state, {
      type: "THOUGHT_APPENDED",
      thought: "Detected 2 work experiences and 5 technical skills.",
    });
    expect(state.thoughts).toHaveLength(1);
    expect(state.thoughts[0]).toContain("Detected 2 work experiences");

    // Deduplicate identical thoughts
    state = ingestTaskReducer(state, {
      type: "THOUGHT_APPENDED",
      thought: "Detected 2 work experiences and 5 technical skills.",
    });
    expect(state.thoughts).toHaveLength(1);

    // 3. Stage 2 transition
    state = ingestTaskReducer(state, {
      type: "STATUS_UPDATE",
      payload: {
        stage: "extracting",
        stageNumber: 2,
        stageMessage: "Analyzing experiences with AI...",
      },
    });
    expect(state.stageNumber).toBe(2);

    // 4. Stage 3 transition
    state = ingestTaskReducer(state, {
      type: "STATUS_UPDATE",
      payload: {
        stage: "deduping",
        stageNumber: 3,
        stageMessage: "Cross-referencing existing profile...",
      },
    });
    expect(state.stageNumber).toBe(3);

    // 5. Completion
    state = ingestTaskReducer(state, {
      type: "TASK_COMPLETED",
      hasStagedDuplicates: true,
    });
    expect(state.status).toBe("review_required");
    expect(state.hasStagedDuplicates).toBe(true);
  });

  it("handles failure transitions gracefully with diagnostic error", () => {
    let state = initialIngestTaskState();
    state = ingestTaskReducer(state, {
      type: "TASK_STARTED",
      taskId: "err-task",
      filename: "bad.pdf",
    });
    state = ingestTaskReducer(state, {
      type: "TASK_FAILED",
      error: "Malformed PDF trailer or encrypted file.",
    });
    expect(state.status).toBe("failed");
    expect(state.error).toBe("Malformed PDF trailer or encrypted file.");
  });

  it("verifies backend migration 010 schema contract for ingestion tasks", () => {
    const migration010 = readFileSync(
      new URL("../../../../../apps/api/data/sqlite/migrations/010_ingestion_tasks.sql", import.meta.url),
      "utf8"
    );
    expect(migration010).toContain("CREATE TABLE IF NOT EXISTS ingestion_tasks");
    expect(migration010).toContain("task_id");
    expect(migration010).toContain("stage");
    expect(migration010).toContain("thoughts_json");
    expect(migration010).toContain("stage_number");
  });
});
