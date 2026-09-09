export type { ApiFetch, ContactLookup, KeywordCoverage, Lead } from "../types";

export type WSMessage =
  | { type: "heartbeat"; status: string; beat: number; uptime_seconds: number; timestamp: string }
  | { type: "agent"; event?: string; msg?: string; job_id?: string; [key: string]: unknown }
  | { type: "LEAD_UPDATED"; data: import("../types").Lead }
  | { type: "HOT_X_LEAD"; data: import("../types").Lead }
  | { type: "ingest_progress"; task_id: string; stage?: string; stage_number?: number; message?: string; progress_percent?: number }
  | { type: "ingest_thought"; task_id: string; thought?: string }
  | { type: "ingest_review_required"; task_id: string; duplicates_count?: number }
  | { type: "ingest_failed"; task_id: string; error?: string }
  | { type: "ingest_resolved"; task_id: string }
  | { type: "LEADS_REFRESH"; data?: { reason?: string } }
  | { type: "pong" };

export type IngestionPhase =
  | "reading"
  | "extracting"
  | "deduping"
  | "indexing"
  | "completed"
  | "failed"
  | "idle"
  | "extracting_text"
  | "ai_analysis"
  | "checking_duplicates"
  | "indexing_tags"
  | "done";

export type IngestionStatus =
  | "idle"
  | "processing"
  | "completed"
  | "failed"
  | "cancelled"
  | "review_required"
  | "review_needed";

export interface IngestionJob {
  taskId: string;
  status: IngestionStatus;
  stage: 1 | 2 | 3 | 4;
  stageLabel: string;
  filename: string;
  tagId?: string | null;
  startedAt: number;
  elapsedSeconds: number;
  thoughts: string[];
  hasStagedDuplicates?: boolean;
  reviewableDuplicates?: any[];
  error?: string | null;
  result?: Record<string, any> | null;
}

export interface IngestionStatusResponse {
  task_id: string | null;
  status: IngestionStatus;
  stage: IngestionPhase;
  stage_number: number;
  stage_message?: string;
  stage_label?: string;
  filename: string | null;
  tag_id?: string | null;
  elapsed_seconds: number;
  progress_percent?: number;
  thoughts: string[];
  has_staged_duplicates?: boolean;
  reviewable_duplicates?: any[];
  error?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  result?: Record<string, any> | null;
}
