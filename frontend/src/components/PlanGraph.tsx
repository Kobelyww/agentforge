import type { UIPlan, UIStep } from "../hooks/useChat";

/**
 * 计划执行 DAG（SVG）：节点=计划步骤，贝塞尔边=执行流向，
 * 流动虚线动画（stroke-dashoffset）表达"进行中"，节点按状态脉冲发光。
 * 纯手写 SVG 布局，无图库依赖。
 */

const NODE_W = 128;
const NODE_H = 46;
const GAP_X = 56;
const GAP_Y = 14;

export default function PlanGraph({ plan, steps }: { plan: UIPlan; steps: UIStep[] }) {
  const nodes = plan.steps;
  // 简单分层布局：单列垂直排布（保留水平扩展位：分支步骤可加列）
  const width = NODE_W + 190;
  const height = Math.max(nodes.length * (NODE_H + GAP_Y) + 30, 96);

  const pos = (i: number) => ({
    x: 16,
    y: 14 + i * (NODE_H + GAP_Y),
  });

  return (
    <div className="plan-graph-frame">
      <div className="plan-header">
        🧠 EXECUTION GRAPH
        {plan.thought ? <span className="plan-thought"> · {plan.thought}</span> : null}
      </div>
      <svg
        width="100%"
        height={height}
        style={{ width: "100%", height, display: "block", minHeight: height }}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMinYMid meet"
      >
        <defs>
          <linearGradient id="edgeFlow" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#4f8ff7" stopOpacity="0.9" />
            <stop offset="100%" stopColor="#8957e5" stopOpacity="0.9" />
          </linearGradient>
        </defs>

        {nodes.map((step, i) => {
          if (i === nodes.length - 1) return null;
          const from = pos(i);
          const to = pos(i + 1);
          const x0 = from.x + NODE_W;
          const y0 = from.y + NODE_H / 2;
          const x1 = to.x;
          const y1 = to.y + NODE_H / 2;
          const mx = (x0 + x1) / 2;
          return (
            <path
              key={`edge-${step.id}`}
              d={`M ${x0} ${y0} C ${mx} ${y0}, ${mx} ${y1}, ${x1} ${y1}`}
              stroke="url(#edgeFlow)"
              strokeWidth={1.6}
              fill="none"
              strokeDasharray="7 5"
              className="plan-edge-flow"
            />
          );
        })}

        {nodes.map((step, i) => {
          const state = steps.find((s) => s.step_id === step.id);
          const cls = state?.running ? "running" : state?.summary ? "done" : "pending";
          const p = pos(i);
          return (
            <g
              key={step.id}
              className="plan-node-group"
              style={{ animationDelay: `${i * 0.12}s` }}
            >
              <rect
                x={p.x} y={p.y} width={NODE_W} height={NODE_H} rx={9}
                className={`plan-node ${cls}`}
              />
              <text x={p.x + 12} y={p.y + 19} className="plan-node-id">
                {step.id.toUpperCase()}
              </text>
              <text x={p.x + 12} y={p.y + 35} className="plan-node-title">
                {step.title.length > 11 ? `${step.title.slice(0, 11)}…` : step.title}
              </text>
              {state?.running && (
                <circle cx={p.x + NODE_W - 14} cy={p.y + NODE_H / 2} r={4} className="plan-node-pulse" />
              )}
              {state?.summary && !state.running && (
                <text x={p.x + NODE_W - 14} y={p.y + NODE_H / 2 + 4} className="plan-node-check">✓</text>
              )}
              {/* 右侧信息 */}
              <text x={p.x + NODE_W + 14} y={p.y + 19} className="plan-node-meta">
                {state?.running ? "EXECUTING…" : state?.summary ? `${((state.elapsed_ms ?? 0) / 1000).toFixed(1)}s` : "QUEUED"}
              </text>
              {state?.summary && (
                <text x={p.x + NODE_W + 14} y={p.y + 34} className="plan-node-summary">
                  {(state.summary ?? "").slice(0, 34)}…
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {plan.success_criteria && (
        <div className="plan-criteria">✓ {plan.success_criteria}</div>
      )}
    </div>
  );
}
