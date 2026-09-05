import { useCallback, useRef, useState } from "react";
import { streamChat } from "../api";
import type { AgentEvent, UIPlan } from "../types";

export type { UIPlan };

export interface UIToolCall {
  call_id: string;
  name: string;
  arguments: Record<string, unknown>;
  output?: string;
  error?: string | null;
  ok?: boolean;
  latency_ms?: number;
  running: boolean;
}

export interface UIStep {
  step_id: string;
  title: string;
  summary?: string;
  elapsed_ms?: number;
  running: boolean;
}

export interface UIMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
  toolCalls?: UIToolCall[];
  meta?: Record<string, unknown>;
}

interface ChatState {
  messages: UIMessage[];
  busy: boolean;
  error: string | null;
  plan: UIPlan | null;
  steps: UIStep[];
  phase: string | null;
  approval: { approval_id: string; message: string; payload: Record<string, unknown> } | null;
}

export function useChat(sessionId: string | null, onSessionUpdated: () => void) {
  const [state, setState] = useState<ChatState>({
    messages: [], busy: false, error: null, plan: null, steps: [], phase: null, approval: null,
  });
  const abortRef = useRef<AbortController | null>(null);

  const patchLastAssistant = useCallback((fn: (m: UIMessage) => UIMessage) => {
    setState((prev) => {
      const messages = [...prev.messages];
      for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i].role === "assistant") {
          messages[i] = fn(messages[i]);
          break;
        }
      }
      return { ...prev, messages };
    });
  }, []);

  const handleEvent = useCallback(
    (event: AgentEvent) => {
      switch (event.type) {
        case "user_message":
          setState((prev) => ({
            ...prev,
            plan: null,
            steps: [],
            messages: [
              ...prev.messages,
              { id: event.message.id, role: "user", content: event.message.content },
            ],
          }));
          break;
        case "phase":
          setState((prev) => ({ ...prev, phase: event.phase }));
          break;
        case "plan_created":
          setState((prev) => ({
            ...prev,
            plan: { thought: event.thought, steps: event.steps, success_criteria: event.success_criteria },
            steps: event.steps.map((s) => ({ step_id: s.id, title: s.title, running: false })),
          }));
          break;
        case "step_started":
          setState((prev) => ({
            ...prev,
            steps: prev.steps.map((s) =>
              s.step_id === event.step_id ? { ...s, running: true } : s,
            ),
          }));
          break;
        case "step_completed":
          setState((prev) => ({
            ...prev,
            steps: prev.steps.map((s) =>
              s.step_id === event.step_id
                ? { ...s, running: false, summary: event.summary, elapsed_ms: event.elapsed_ms }
                : s,
            ),
          }));
          break;
        case "text_delta":
          patchLastAssistant((m) => ({ ...m, content: m.content + event.text }));
          break;
        case "tool_start":
          patchLastAssistant((m) => ({
            ...m,
            toolCalls: [
              ...(m.toolCalls ?? []),
              { call_id: event.call_id, name: event.name, arguments: event.arguments, running: true },
            ],
          }));
          break;
        case "tool_end":
          patchLastAssistant((m) => ({
            ...m,
            toolCalls: (m.toolCalls ?? []).map((tc) =>
              tc.call_id === event.call_id
                ? {
                    ...tc,
                    running: false,
                    ok: event.ok,
                    output: event.output,
                    error: event.error,
                    latency_ms: event.latency_ms,
                  }
                : tc,
            ),
          }));
          break;
        case "approval_required":
          setState((prev) => ({
            ...prev,
            approval: { approval_id: event.approval_id, message: event.message, payload: event.payload },
          }));
          break;
        case "critic_verdict":
          setState((prev) => ({
            ...prev,
            phase: event.pass ? "critiquing_done" : "critiquing_revise",
          }));
          break;
        case "assistant_message":
          setState((prev) => ({
            ...prev,
            messages: prev.messages.map((m) =>
              m.role === "assistant" && m.streaming
                ? { ...m, id: event.message.id, streaming: false, meta: event.message.meta }
                : m,
            ),
          }));
          break;
        case "error":
          setState((prev) => ({ ...prev, error: event.message }));
          break;
        case "done":
          setState((prev) => ({ ...prev, busy: false, phase: null, approval: null }));
          onSessionUpdated();
          break;
      }
    },
    [onSessionUpdated, patchLastAssistant],
  );

  const send = useCallback(
    async (content: string, model: string | null, orchestrator?: string | null, autoApprove?: boolean) => {
      if (!sessionId || state.busy || !content.trim()) return;
      const controller = new AbortController();
      abortRef.current = controller;
      setState((prev) => ({
        ...prev,
        busy: true,
        error: null,
        plan: null,
        steps: [],
        messages: [
          ...prev.messages,
          { id: `local-${Date.now()}`, role: "assistant", content: "", streaming: true },
        ],
      }));
      try {
        await streamChat(sessionId, content, model, handleEvent, controller.signal, orchestrator, autoApprove);
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          setState((prev) => ({ ...prev, error: (err as Error).message }));
        }
      } finally {
        setState((prev) => ({
          ...prev,
          busy: false,
          phase: null,
          messages: prev.messages.map((m) => ({ ...m, streaming: false })),
        }));
        abortRef.current = null;
      }
    },
    [handleEvent, sessionId, state.busy],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const setMessages = useCallback((messages: UIMessage[]) => {
    setState({ messages, busy: false, error: null, plan: null, steps: [], phase: null, approval: null });
  }, []);

  return { ...state, send, stop, setMessages };
}
