import { useEffect, useState } from "react";
import { api } from "../api";
import type { SessionTrace } from "../types";

export default function TracePanel({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const [trace, setTrace] = useState<SessionTrace | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getTrace(sessionId).then(setTrace).catch((e) => setError(e.message));
  }, [sessionId]);

  return (
    <div className="doc-panel trace-panel">
      <div className="doc-panel-header">
        <h2>🔍 决策链路 Trace</h2>
        <button className="btn-icon" onClick={onClose}>×</button>
      </div>
      {error && <div className="doc-error">⚠ {error}</div>}
      {!trace && !error && <div className="empty-hint">加载中…</div>}
      {trace && (
        <>
          <div className="trace-section">
            <div className="section-title">会话 · {trace.orchestrator === "plan_execute" ? "Plan-and-Execute" : "ReAct"}</div>
            <div className="trace-task">{trace.user_task || "—"}</div>
          </div>

          {trace.plan && (
            <div className="trace-section">
              <div className="section-title">计划 · {trace.plan.thought}</div>
              <ol className="trace-plan-list">
                {trace.plan.steps.map((s) => (
                  <li key={s.id}>{s.title}</li>
                ))}
              </ol>
            </div>
          )}

          {trace.steps.map((step) => (
            <div key={step.step_id} className="trace-section trace-step">
              <div className="section-title">
                步骤 {step.step_id} · {step.title}
              </div>
              {step.tools.map((tool, i) => (
                <div key={i} className={`trace-tool ${tool.ok ? "" : "fail"}`}>
                  🛠 {tool.name} · {(tool.latency_ms / 1000).toFixed(2)}s
                  <div className="trace-tool-content">{tool.content.slice(0, 140)}{tool.content.length > 140 ? "…" : ""}</div>
                </div>
              ))}
              {step.summary && <div className="trace-summary">{step.summary.slice(0, 300)}{step.summary.length > 300 ? "…" : ""}</div>}
            </div>
          ))}

          <div className="trace-section">
            <div className="section-title">总计</div>
            <div className="trace-totals">
              工具调用 {trace.totals.tool_calls} 次 · tokens≈{trace.totals.tokens_est} · 消息 {trace.totals.messages} 条
            </div>
          </div>
        </>
      )}
    </div>
  );
}
