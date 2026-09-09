import { describe, expect, it } from "bun:test";
import { __wsTest, activityEntryFromWSMessage, nextProgressFromAgentEvent } from "./useWS";

describe("WebSocket progress parsing", () => {
  it("starts and finishes the corpus search event family", () => {
    const start = nextProgressFromAgentEvent(
      __wsTest.emptyProgress(),
      "scan_start",
      "Searching the corpus for 'software engineer'",
      10,
    );
    expect(start).toMatchObject({ active: true, mode: "scan" });
    expect(start.current).toContain("software engineer");
    expect(nextProgressFromAgentEvent(start, "scan_stop", "Search cancelled", 20).active).toBe(false);
  });

  it("starts scan progress from eval_start totals", () => {
    const next = nextProgressFromAgentEvent(__wsTest.emptyProgress(), "eval_start", "Evaluating 52 leads via ollama", 10);
    expect(next).toMatchObject({ active: true, mode: "scan", total: 52, completed: 0, updatedAt: 10 });
  });

  it("increments scan progress on scored events", () => {
    const start = nextProgressFromAgentEvent(__wsTest.emptyProgress(), "eval_start", "Evaluating 2 leads", 10);
    const next = nextProgressFromAgentEvent(start, "eval_scored", "Scored Backend Engineer = 82/100", 20);
    expect(next.completed).toBe(1);
    expect(next.current).toContain("Backend Engineer");
  });

  it("clears scan progress on eval_done", () => {
    const active = { ...__wsTest.emptyProgress(), active: true, mode: "scan" as const, total: 3, completed: 2 };
    expect(nextProgressFromAgentEvent(active, "eval_done", "done", 30).active).toBe(false);
  });

  it("starts reevaluation progress", () => {
    const next = nextProgressFromAgentEvent(__wsTest.emptyProgress(), "reeval_start", "Re-evaluating 8 job leads", 10);
    expect(next).toMatchObject({ active: true, mode: "reevaluate", total: 8 });
  });

  it("extracts reevaluation totals from bracketed progress", () => {
    const next = nextProgressFromAgentEvent(__wsTest.emptyProgress(), "reeval_scored", "[3/10] Re-scored Engineer = 90/100", 10);
    expect(next.total).toBe(10);
    expect(next.completed).toBe(1);
  });

  it("leaves unrelated agent events unchanged", () => {
    const prev = { ...__wsTest.emptyProgress(), updatedAt: 1 };
    expect(nextProgressFromAgentEvent(prev, "heartbeat", "noop", 99)).toBe(prev);
  });

  it("turns ingestion progress and failures into useful activity", () => {
    expect(activityEntryFromWSMessage({
      type: "ingest_progress",
      task_id: "task-1",
      stage_number: 2,
      message: "Extracting profile with AI",
      progress_percent: 50,
    })).toEqual({
      msg: "Profile import Step 2 · 50% — Extracting profile with AI",
      kind: "agent",
      src: "profile",
    });
    expect(activityEntryFromWSMessage({
      type: "ingest_failed",
      task_id: "task-1",
      error: "Provider rejected the request",
    })?.msg).toContain("Profile import failed");
  });

  it("hides transport heartbeats and labels workflow events", () => {
    expect(activityEntryFromWSMessage({
      type: "heartbeat",
      status: "alive",
      beat: 1,
      uptime_seconds: 15,
      timestamp: "now",
    })).toBeNull();
    expect(activityEntryFromWSMessage({
      type: "agent",
      event: "gen_done",
      msg: "Resume ready",
    })).toEqual({
      msg: "Documents ready — Resume ready",
      kind: "agent",
      src: "documents",
    });
  });
});
