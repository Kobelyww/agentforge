import type { AgentEvent, DocInfo, Equipment, ProviderInfo, Session, SessionTrace, ToolInfo, WaveformData, WorkOrder } from "./types";

const BASE = "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (resp.status === 204) return undefined as T;
  if (!resp.ok) {
    let detail = `${resp.status}`;
    try {
      const body = await resp.json();
      detail = body.detail ?? body.error?.message ?? JSON.stringify(body);
    } catch {
      /* keep status code */
    }
    throw new Error(detail);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  listSessions: () => request<Session[]>("/api/sessions"),
  createSession: (title?: string) =>
    request<Session>("/api/sessions", { method: "POST", body: JSON.stringify({ title }) }),
  getSession: (id: string) => request<Session & { messages: unknown[] }>(`/api/sessions/${id}`),
  renameSession: (id: string, title: string) =>
    request<Session>(`/api/sessions/${id}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  deleteSession: (id: string) => request<void>(`/api/sessions/${id}`, { method: "DELETE" }),

  listTools: () => request<ToolInfo[]>("/api/tools"),
  listProviders: () => request<ProviderInfo[]>("/api/providers"),
  listEquipment: () => request<Equipment[]>("/api/forgeops/equipment"),
  listWorkOrders: () => request<WorkOrder[]>("/api/forgeops/workorders"),
  getTrace: (sessionId: string) => request<SessionTrace>(`/api/sessions/${sessionId}/trace`),
  getWaveform: (equipmentId: string) =>
    request<WaveformData>(`/api/forgeops/equipment/${equipmentId}/waveform?points=1600`),
  decideApproval: (approvalId: string, decision: "approved" | "rejected") =>
    request(`/api/forgeops/approvals/${approvalId}/decide`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    }),

  listDocs: () => request<DocInfo[]>("/api/documents"),
  uploadDoc: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return fetch("/api/documents", { method: "POST", body: form }).then(async (resp) => {
      if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail ?? String(resp.status));
      return resp.json() as Promise<{ document: DocInfo; chunks: number }>;
    });
  },
  deleteDoc: (id: string) => request<void>(`/api/documents/${id}`, { method: "DELETE" }),
};

/**
 * POST to the chat endpoint and parse the SSE stream into typed AgentEvents.
 * Uses fetch + ReadableStream (not EventSource) because the endpoint is POST.
 */
export async function streamChat(
  sessionId: string,
  content: string,
  model: string | null,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
  orchestrator?: string | null,
  autoApprove?: boolean,
): Promise<void> {
  const resp = await fetch(`${BASE}/api/sessions/${sessionId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      content, model,
      orchestrator: orchestrator || undefined,
      auto_approve: autoApprove,
    }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    const detail = await resp.text().catch(() => "");
    throw new Error(`chat failed: ${resp.status} ${detail.slice(0, 200)}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const dispatch = (frame: string) => {
    const eventMatch = frame.match(/^event: (.+)$/m);
    const dataMatch = frame.match(/^data: (.+)$/m);
    if (!eventMatch) return;
    let data: unknown = {};
    try {
      data = dataMatch ? JSON.parse(dataMatch[1]) : {};
    } catch {
      return;
    }
    onEvent({ type: eventMatch[1], ...(data as object) } as AgentEvent);
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index;
    while ((index = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      if (frame.trim() && !frame.startsWith(":")) dispatch(frame);
    }
  }
}
