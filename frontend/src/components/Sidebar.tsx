import type { ProviderInfo, Session, ToolInfo } from "../types";

interface Props {
  sessions: Session[];
  activeId: string | null;
  tools: ToolInfo[];
  providers: ProviderInfo[];
  model: string | null;
  orchestrator: string;
  docsOpen: boolean;
  workbenchOpen: boolean;
  onOrchestratorChange: (mode: string) => void;
  onModelChange: (m: string) => void;
  onNewSession: () => void;
  onOpenSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  onToggleDocs: () => void;
  onToggleWorkbench: () => void;
}

export default function Sidebar(props: Props) {
  const {
    sessions, activeId, tools, providers, model, orchestrator, docsOpen, workbenchOpen,
    onOrchestratorChange, onModelChange, onNewSession, onOpenSession, onDeleteSession,
    onToggleDocs, onToggleWorkbench,
  } = props;

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="brand-mark">⚒</span>
        <div>
          <div className="brand-name">ForgeOps</div>
          <div className="brand-sub">工业设备智能运维 · AgentForge 引擎</div>
        </div>
      </div>

      <button className="btn-primary" onClick={onNewSession}>＋ 新建会话</button>

      <div className="sidebar-section">
        <div className="section-title">编排模式</div>
        <div className="mode-toggle">
          <button
            className={`mode-btn ${orchestrator === "react" ? "active" : ""}`}
            onClick={() => onOrchestratorChange("react")}
            title="经典 ReAct 循环：适合问答与简单任务"
          >
            ⚡ ReAct
          </button>
          <button
            className={`mode-btn ${orchestrator === "plan_execute" ? "active" : ""}`}
            onClick={() => onOrchestratorChange("plan_execute")}
            title="规划-执行-汇总：适合复杂诊断任务"
          >
            🧠 规划模式
          </button>
        </div>

        <div className="section-title" style={{ marginTop: 12 }}>模型</div>
        <select className="model-select" value={model ?? ""} onChange={(e) => onModelChange(e.target.value)}>
          {providers.map((p) => (
            <option key={p.name} value={`${p.name}/${p.model}`}>
              {p.name} · {p.model}{p.default ? "（默认）" : ""}
            </option>
          ))}
        </select>
        <div className="section-title" style={{ marginTop: 12 }}>工具 ({tools.length})</div>
        <div className="tool-tags">
          {tools.map((t) => (
            <span key={t.name} className="tool-tag" title={t.description}>{t.name}</span>
          ))}
        </div>
        <button className="btn-ghost" style={{ marginTop: 12, width: "100%" }} onClick={onToggleWorkbench}>
          {workbenchOpen ? "关闭设备台账" : "🏭 设备台账"}
        </button>
        <button className="btn-ghost" style={{ marginTop: 8, width: "100%" }} onClick={onToggleDocs}>
          {docsOpen ? "关闭知识库面板" : "📚 知识库管理"}
        </button>
      </div>

      <div className="sidebar-section sessions">
        <div className="section-title">历史会话</div>
        {sessions.length === 0 && <div className="empty-hint">暂无会话</div>}
        {sessions.map((s) => (
          <div
            key={s.id}
            className={`session-item ${s.id === activeId ? "active" : ""}`}
            onClick={() => onOpenSession(s.id)}
          >
            <span className="session-title">{s.title}</span>
            <button
              className="session-delete"
              title="删除"
              onClick={(e) => {
                e.stopPropagation();
                onDeleteSession(s.id);
              }}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </aside>
  );
}
