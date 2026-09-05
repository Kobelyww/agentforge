import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Markdown, remarkGfm } from "../lazyMarkdown";
import type { useChat } from "../hooks/useChat";
import ToolCallCard from "./ToolCallCard";
import PlanGraph from "./PlanGraph";
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
  planning: "📋 PLANNING · 任务规划中",
  executing: "⚙️ EXECUTING · 分步执行中",
  synthesizing: "🧩 SYNTHESIZING · 汇总结论中",
  critiquing: "🧐 CRITIC · 质量审核中",
  critiquing_revise: "🔁 REVISING · 审核未通过，修订中",
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
        <span className="phase-label">{chat.phase ? PHASE_LABEL[chat.phase] ?? chat.phase : ""}</span>
        {chat.busy && <span className="busy-bars"><i /><i /><i /></span>}
        <button className="btn-ghost small" onClick={() => setTraceOpen(true)}>🔍 决策链路 TRACE</button>
      </div>

      <div className="messages">
        {chat.plan && chat.steps.length > 0 && (
          <PlanGraph plan={chat.plan} steps={chat.steps} />
        )}

        {chat.messages.map((m, idx) => (
          <motion.div
            key={m.id}
            className={`msg msg-${m.role}`}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25, delay: idx === chat.messages.length - 1 ? 0 : 0.02 }}
          >
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
          </motion.div>
        ))}

        {chat.approval && (
          <motion.div
            className="approval-card"
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ type: "spring", stiffness: 280, damping: 22 }}
          >
            <div className="approval-header">⚠ HUMAN-IN-THE-LOOP · 需要人工批准</div>
            <div className="approval-message">{chat.approval.message}</div>
            <pre className="approval-payload">{JSON.stringify(chat.approval.payload, null, 2)}</pre>
            <div className="approval-actions">
              <button className="btn-primary" onClick={() => onDecide(chat.approval!.approval_id, "approved")}>
                ✓ 批准执行
              </button>
              <button className="btn-stop" onClick={() => onDecide(chat.approval!.approval_id, "rejected")}>
                ✗ 拒绝
              </button>
            </div>
          </motion.div>
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

const PRIORITY_COLOR: Record<string, string> = {
  P1: "#f85149", P2: "#d29922", P3: "#4f8ff7", P4: "#8b949e",
};

function WorkOrderCard({ toolCall }: { toolCall: { arguments: Record<string, unknown>; output?: string } }) {
  const args = toolCall.arguments as {
    code?: string; equipment_id?: string; title?: string; fault_type?: string;
    confidence?: number; priority?: string; actions?: string[]; parts?: string[];
    estimated_hours?: number;
  } | undefined;
  if (!args || (!args.code && !args.title)) return null;
  const codeMatch = toolCall.output?.match(/WO-\d{6}/);
  return (
    <div className="wo-card">
      <div className="wo-header">
        <span className="wo-priority" style={{ background: PRIORITY_COLOR[args.priority ?? "P3"] }}>
          {args.priority ?? "P3"}
        </span>
        <span className="wo-code">{codeMatch ? codeMatch[0] : "WO-PENDING"}</span>
        <span className="wo-equipment">{args.equipment_id}</span>
      </div>
      <div className="wo-title">{args.title}</div>
      <div className="wo-meta">
        故障类型 <code>{args.fault_type}</code> · 置信度{" "}
        <b>{typeof args.confidence === "number" ? (args.confidence * 100).toFixed(0) + "%" : "-"}</b>
        {typeof args.estimated_hours === "number" && <> · 预计停机 <b>{args.estimated_hours}h</b></>}
      </div>
      {Array.isArray(args.actions) && args.actions.length > 0 && (
        <ul className="wo-actions">
          {args.actions.map((a, i) => (
            <li key={i}>{a}</li>
          ))}
        </ul>
      )}
      {Array.isArray(args.parts) && args.parts.length > 0 && (
        <div className="wo-parts">备件：{args.parts.join("、")}</div>
      )}
    </div>
  );
}
