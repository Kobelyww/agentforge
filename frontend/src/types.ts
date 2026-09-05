// Shared API types mirroring the backend payloads.

export interface Session {
  id: string;
  title: string;
  provider: string;
  model: string;
  summary: string;
  created_at: string;
  updated_at: string;
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface ChatMessage {
  id: string;
  seq: number;
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  tool_calls: ToolCall[] | null;
  tool_call_id: string | null;
  name: string | null;
  tokens: number;
  latency_ms: number;
  meta: Record<string, unknown>;
  created_at: string;
}

export interface ToolInfo {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  timeout: number;
}

export interface ProviderInfo {
  name: string;
  model: string;
  default: boolean;
}

export interface DocInfo {
  id: string;
  name: string;
  source: string;
  mime: string;
  size: number;
  chunk_count: number;
  created_at: string;
}

export interface SearchHit {
  chunk_id: string;
  document_id: string;
  document_name: string;
  seq: number;
  score: number;
  text: string;
}

export interface Equipment {
  id: string;
  name: string;
  model: string;
  location: string;
  rated_rpm: number;
  rotational_hz: number;
  sensor_file: string;
  status: string;
  open_work_orders?: number;
}

export interface WorkOrder {
  code: string;
  equipment_id: string;
  title: string;
  fault_type: string;
  confidence: number;
  priority: string;
  actions: string[];
  parts: string[];
  estimated_hours: number;
  status: string;
  created_at: string;
}

export interface PlanStep {
  id: string;
  title: string;
  instruction: string;
}

export interface UIPlan {
  thought: string;
  steps: PlanStep[];
  success_criteria: string;
}

// SSE agent events (subset the UI consumes)
export type AgentEvent =
  | { type: "open"; session_id: string }
  | { type: "user_message"; message: ChatMessage }
  | { type: "text_delta"; text: string }
  | { type: "iteration"; index: number; max: number }
  | { type: "phase"; phase: string }
  | { type: "plan_created"; thought: string; steps: PlanStep[]; success_criteria: string }
  | { type: "step_started"; step_id: string; title: string; index: number; total: number; instruction: string }
  | { type: "step_completed"; step_id: string; title: string; index: number; summary: string; elapsed_ms: number }
  | { type: "tool_start"; call_id: string; name: string; arguments: Record<string, unknown> }
  | {
      type: "tool_end";
      call_id: string;
      name: string;
      ok: boolean;
      output: string;
      error: string | null;
      latency_ms: number;
    }
  | { type: "assistant_message"; message: ChatMessage }
  | { type: "approval_required"; approval_id: string; action: string; payload: Record<string, unknown>; message: string }
  | { type: "critic_verdict"; pass: boolean; issues: string[]; revised?: boolean }
  | { type: "memory_recalled"; memories: string[] }
  | { type: "error"; message: string }
  | { type: "done"; session_id: string; elapsed_ms: number; final_text?: string };

// Oscilloscope telemetry
export interface WaveformData {
  equipment_id: string;
  time_s: number[];
  vibration_mm_s: number[];
  rms_mm_s: number;
  iso10816_status: string;
  rotational_hz: number;
}

// Trace API
export interface TraceStep {
  step_id: string;
  title: string;
  instruction: string;
  summary: string;
  iterations?: number;
  tools: { name: string; ok: boolean; content: string; latency_ms: number }[];
}

export interface SessionTrace {
  session_id: string;
  title: string;
  orchestrator: "plan_execute" | "react";
  user_task: string;
  plan: { thought: string; steps: PlanStep[]; success_criteria: string } | null;
  steps: TraceStep[];
  final: string | null;
  totals: { tool_calls: number; tokens_est: number; messages: number };
}
