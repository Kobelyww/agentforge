import { Suspense, lazy, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Markdown, remarkGfm } from "./lazyMarkdown";
import { api } from "./api";
import Sidebar from "./components/Sidebar";
import ChatView from "./components/ChatView";
import DocPanel from "./components/DocPanel";
import WorkbenchPanel from "./components/WorkbenchPanel";
import StatusRail from "./components/StatusRail";
import MissionDeck from "./components/MissionDeck";
import { useChat, type UIMessage } from "./hooks/useChat";
import type { Equipment, ProviderInfo, Session, ToolInfo } from "./types";

type Panel = null | "docs" | "workbench";

// 3D 场景按需加载（three 体积大，首屏不阻塞）
const HeroSceneLazy = lazy(() => import("./components/HeroScene"));

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [model, setModel] = useState<string | null>(null);
  const [orchestrator, setOrchestrator] = useState("react");
  const [panel, setPanel] = useState<Panel>(null);

  const refreshSessions = () => api.listSessions().then(setSessions).catch(() => undefined);

  useEffect(() => {
    refreshSessions();
    api.listProviders().then((ps) => {
      setProviders(ps);
      const preferred = ps.find((p) => p.default);
      if (preferred) setModel(`${preferred.name}/${preferred.model}`);
    }).catch(() => undefined);
    api.listTools().then(setTools).catch(() => undefined);
  }, []);

  const chat = useChat(activeId, refreshSessions);

  const openSession = async (id: string) => {
    setActiveId(id);
    try {
      const detail = await api.getSession(id);
      const toolOutputs = new Map<string, { content: string; name: string }>();
      const uiMessages: UIMessage[] = [];
      for (const raw of detail.messages as never[]) {
        const msg = raw as {
          id: string; role: string; content: string;
          tool_calls: { id: string; name: string; arguments: Record<string, unknown> }[] | null;
          tool_call_id: string | null; name: string | null; meta: Record<string, unknown>;
        };
        if (msg.role === "user") {
          if ((msg.meta as { kind?: string } | null)?.kind === "step_instruction") continue;
          uiMessages.push({ id: msg.id, role: "user", content: msg.content });
        } else if (msg.role === "assistant") {
          const kind = (msg.meta as { kind?: string } | null)?.kind;
          if (kind === "plan") continue;
          uiMessages.push({
            id: msg.id, role: "assistant", content: msg.content, meta: msg.meta,
            toolCalls: (msg.tool_calls ?? []).map((c) => ({
              call_id: c.id, name: c.name, arguments: c.arguments, running: false,
            })),
          });
        } else if (msg.role === "tool" && msg.tool_call_id) {
          toolOutputs.set(msg.tool_call_id, { content: msg.content, name: msg.name ?? "" });
        }
      }
      for (const m of uiMessages) {
        for (const tc of m.toolCalls ?? []) {
          const out = toolOutputs.get(tc.call_id);
          if (out) {
            tc.output = out.content;
            tc.running = false;
            tc.ok = !out.content.startsWith("工具执行失败");
          }
        }
      }
      chat.setMessages(uiMessages);
    } catch {
      chat.setMessages([]);
    }
  };

  const newSession = async () => {
    const session = await api.createSession();
    setSessions((prev) => [session, ...prev]);
    setActiveId(session.id);
    chat.setMessages([]);
    return session;
  };

  const removeSession = async (id: string) => {
    await api.deleteSession(id);
    setSessions((prev) => prev.filter((s) => s.id !== id));
    if (activeId === id) {
      setActiveId(null);
      chat.setMessages([]);
    }
  };

  const diagnoseEquipment = async (equipment: Equipment) => {
    setPanel(null);
    setOrchestrator("plan_execute");
    await newSession();
    chat.send(
      `诊断 ${equipment.id} ${equipment.name} 的运行状态：请检索知识库、分析振动数据，给出结论并生成维修工单`,
      null, "plan_execute", false,
    );
  };

  const decideApproval = async (approvalId: string, decision: "approved" | "rejected") => {
    await api.decideApproval(approvalId, decision);
  };

  return (
    <div className="app">
      <StatusRail sessionId={activeId} />
      <div className="app-body">
        <Sidebar
          sessions={sessions}
          activeId={activeId}
          tools={tools}
          providers={providers}
          model={model}
          orchestrator={orchestrator}
          onOrchestratorChange={setOrchestrator}
          onModelChange={setModel}
          onNewSession={newSession}
          onOpenSession={openSession}
          onDeleteSession={removeSession}
          onToggleDocs={() => setPanel((p) => (p === "docs" ? null : "docs"))}
          docsOpen={panel === "docs"}
          onToggleWorkbench={() => setPanel((p) => (p === "workbench" ? null : "workbench"))}
          workbenchOpen={panel === "workbench"}
        />
        <main className="main">
          {activeId ? (
            <ChatView chat={chat} onStop={chat.stop} model={model} orchestrator={orchestrator} sessionId={activeId} onDecide={decideApproval} />
          ) : (
            <Welcome
              onNewSession={newSession}
              onOpenWorkbench={() => setPanel("workbench")}
              toolNames={tools.map((t) => t.name)}
            />
          )}
        </main>
      </div>
      {panel === "docs" && <DocPanel onClose={() => setPanel(null)} />}
      {panel === "workbench" && (
        <WorkbenchPanel onSelectScenario={diagnoseEquipment} onClose={() => setPanel(null)} />
      )}
    </div>
  );
}

function Welcome({
  onNewSession,
  onOpenWorkbench,
  toolNames,
}: {
  onNewSession: () => void;
  onOpenWorkbench: () => void;
  toolNames: string[];
}) {
  return (
    <div className="welcome">
      <div className="welcome-hero">
        <div className="hero-text">
          <div className="hero-eyebrow">// INDUSTRIAL AGENT OPERATING SYSTEM</div>
          <h1 className="glow-text">FORGEOPS</h1>
          <p className="welcome-sub">
            工业设备智能运维 Agent · 自研 AgentForge 引擎
            <br />
            <span className="hero-tags">
              PLAN-EXECUTE · MULTI-AGENT · MCP · HYBRID-RAG · SANDBOX · HITL
            </span>
          </p>
          <div className="welcome-actions">
            <button className="btn-primary" onClick={onOpenWorkbench}>🏭 打开设备遥测台</button>
            <button className="btn-ghost" onClick={onNewSession}>＋ 新建会话</button>
          </div>
        </div>
        <Suspense fallback={<div className="hero-loading">INITIALIZING 3D CORE…</div>}>
          <HeroSceneLazy />
        </Suspense>
      </div>

      <MissionDeck />

      <div className="welcome-cards">
        <motion.div
          className="welcome-card"
          onClick={onNewSession}
          whileHover={{ y: -3, borderColor: "rgba(79,143,247,0.7)" }}
          transition={{ type: "spring", stiffness: 300, damping: 20 }}
        >
          <h3>🧠 规划模式</h3>
          <p>侧栏切换到「规划模式」，体验 Plan-Execute 编排、多智能体扇出与决策链路 Trace</p>
        </motion.div>
        <motion.div
          className="welcome-card"
          onClick={onNewSession}
          whileHover={{ y: -3, borderColor: "rgba(79,143,247,0.7)" }}
          transition={{ type: "spring", stiffness: 300, damping: 20 }}
        >
          <h3>🧮 沙箱计算</h3>
          <p>输入「计算 128*365+42」，观察 python_repl 沙箱实时执行</p>
        </motion.div>
        <motion.div
          className="welcome-card"
          onClick={onNewSession}
          whileHover={{ y: -3, borderColor: "rgba(79,143,247,0.7)" }}
          transition={{ type: "spring", stiffness: 300, damping: 20 }}
        >
          <h3>📚 知识库检索</h3>
          <p>上传文档后问「检索一下手册里的轴承更换 SOP」，观察混合检索</p>
        </motion.div>
      </div>
      <p className="welcome-tools">TOOLCHAIN // {toolNames.join(" · ") || "…"}</p>
    </div>
  );
}
