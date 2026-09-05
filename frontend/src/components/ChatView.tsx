import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { useChat } from "../hooks/useChat";
import ToolCallCard from "./ToolCallCard";
import { PlanTimeline, WorkOrderCard } from "./PlanTimeline";
import TracePanel from "./TracePanel";

interface Props {
  chat: ReturnType<typeof useChat>;
  onStop: () => void;
  model: string | null;
  orchestrator: string;
  sessionId: string | null;
  onDecide: (approvalId: string, decision: "approved" | "rejected") => void;
}

const PHASE_LABEL: Record<string, string> = {
  planning: "📋 规划中…",
  executing: "⚙️ 分步执行中…",
  synthesizing: "🧩 汇总结论中…",
  critiquing: "🧐 质量审核中…",
  critiquing_revise: "🔁 审核未通过，修订中…",
  critiquing_done: "",
};

export default function ChatView({ chat, onStop, model, orchestrator, sessionId, onDecide }: Props) {
  const [input, setInput] = useState("");
  const [traceOpen, setTraceOpen] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat.messages]);

  const submit = () => {
    const content = input.trim();
    if (!content || chat.busy) return;
    setInput("");
    chat.send(content, model, orchestrator);
  };

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="chat">
      <div className="chat-toolbar">
        <span className="phase-label">
          {chat.phase ? PHASE_LABEL[chat.phase] ?? chat.phase : ""}
        </span>
        <button className="btn-ghost small" onClick={() => setTraceOpen(true)}>🔍 查看决策链路</button>
      </div>

      <div className="messages">
        {chat.plan && chat.steps.length > 0 && (
          <PlanTimeline plan={chat.plan} steps={chat.steps} />
        )}

        {chat.messages.map((m) => (
          <div key={m.id} className={`msg msg-${m.role}`}>
            <div className="msg-avatar">{m.role === "user" ? "你" : "⚒"}</div>
            <div className="msg-body">
              {m.toolCalls && m.toolCalls.length > 0 && (
                <div className="msg-tools">
                  {m.toolCalls.map((tc) =>
                    tc.name === "create_work_order" ? (
                      <WorkOrderCard key={tc.call_id} toolCall={tc} />
                    ) : (
                      <ToolCallCard key={tc.call_id} toolCall={tc} />
                    ),
                  )}
                </div>
              )}
              {m.role === "assistant" ? (
                <div className="msg-content markdown">
                  <Markdown remarkPlugins={[remarkGfm]}>{m.content || (m.streaming ? "…" : "")}</Markdown>
                  {m.streaming && <span className="cursor">▍</span>}
                </div>
              ) : (
                <div className="msg-content">{m.content}</div>
              )}
              {m.meta && (m.meta as { provider?: string }).provider && (
                <div className="msg-meta">
                  {(m.meta as { provider?: string; model?: string }).provider}
                  {((m.meta as { latency_ms?: number }).latency_ms ?? 0) > 0 &&
                    ` · ${((m.meta as { latency_ms?: number }).latency_ms! / 1000).toFixed(1)}s`}
                </div>
              )}
            </div>
          </div>
        ))}
        {chat.approval && (
          <div className="approval-card">
            <div className="approval-header">⚠ 需要人工批准 · Human-in-the-Loop</div>
            <div className="approval-message">{chat.approval.message}</div>
            <pre className="approval-payload">{JSON.stringify(chat.approval.payload, null, 2)}</pre>
            <div className="approval-actions">
              <button
                className="btn-primary"
                onClick={() => onDecide(chat.approval!.approval_id, "approved")}
              >
                ✓ 批准
              </button>
              <button
                className="btn-stop"
                onClick={() => onDecide(chat.approval!.approval_id, "rejected")}
              >
                ✗ 拒绝
              </button>
            </div>
          </div>
        )}
        {chat.error && <div className="chat-error">⚠ {chat.error}</div>}
        <div ref={bottomRef} />
      </div>

      <div className="composer">
        <textarea
          value={input}
          placeholder={
            orchestrator === "plan_execute"
              ? "规划模式：描述一个任务，Agent 会先拆解计划再分步执行…"
              : "输入消息，Enter 发送，Shift+Enter 换行…（试试：计算 128*365+42）"
          }
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKey}
          disabled={chat.busy}
          rows={2}
        />
        {chat.busy ? (
          <button className="btn-stop" onClick={onStop}>■ 停止</button>
        ) : (
          <button className="btn-primary" onClick={submit} disabled={!input.trim()}>发送</button>
        )}
      </div>

      {traceOpen && sessionId && <TracePanel sessionId={sessionId} onClose={() => setTraceOpen(false)} />}
    </div>
  );
}
