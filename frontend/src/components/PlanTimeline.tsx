import type { UIPlan, UIStep } from "../hooks/useChat";
import type { WorkOrder } from "../types";

export function PlanTimeline({ plan, steps }: { plan: UIPlan; steps: UIStep[] }) {
  return (
    <div className="plan-timeline">
      <div className="plan-header">🧠 执行计划{plan.thought ? ` · ${plan.thought}` : ""}</div>
      <ol className="plan-steps">
        {plan.steps.map((ps, i) => {
          const state = steps.find((s) => s.step_id === ps.id);
          const cls = state?.running ? "running" : state?.summary ? "done" : "pending";
          return (
            <li key={ps.id} className={`plan-step ${cls}`}>
              <span className="plan-step-dot">{cls === "done" ? "✓" : i + 1}</span>
              <div className="plan-step-body">
                <div className="plan-step-title">
                  {ps.title}
                  {state?.running && <span className="plan-step-badge">执行中…</span>}
                  {typeof state?.elapsed_ms === "number" && (
                    <span className="plan-step-badge ok">{(state.elapsed_ms / 1000).toFixed(1)}s</span>
                  )}
                </div>
                {state?.summary && (
                  <div className="plan-step-summary">{state.summary.slice(0, 160)}{state.summary.length > 160 ? "…" : ""}</div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

const PRIORITY_COLOR: Record<string, string> = {
  P1: "#f85149", P2: "#d29922", P3: "#4f8ff7", P4: "#8b949e",
};

export function WorkOrderCard({ toolCall }: { toolCall: { arguments: Record<string, unknown>; output?: string } }) {
  // Prefer the parsed arguments; fall back to the tool output envelope.
  const args = toolCall.arguments as Partial<WorkOrder> | undefined;
  if (!args || !args.code && !args.title) return null;
  return (
    <div className="wo-card">
      <div className="wo-header">
        <span className="wo-priority" style={{ background: PRIORITY_COLOR[args.priority ?? "P3"] }}>
          {args.priority ?? "P3"}
        </span>
        <span className="wo-code">{(toolCall.output?.match(/WO-\d{6}/) ?? [""])[0]}</span>
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
