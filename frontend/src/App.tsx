import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "./api";
import Sidebar from "./components/Sidebar";
import ChatView from "./components/ChatView";
import DocPanel from "./components/DocPanel";
import WorkbenchPanel from "./components/WorkbenchPanel";
import { useChat, type UIMessage } from "./hooks/useChat";
import type { Equipment, ProviderInfo, Session, ToolInfo } from "./types";

type Panel = null | "docs" | "workbench";

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
          if (kind === "plan") continue; // plan renders via the timeline instead
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
    const session = await newSession();
    chat.send(`诊断 ${equipment.id} ${equipment.name} 的运行状态：请检索知识库、分析振动数据，给出结论并生成维修工单`, null, "plan_execute");
  };

  return (
    <div className="app">
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
          <ChatView chat={chat} onStop={chat.stop} model={model} orchestrator={orchestrator} sessionId={activeId} />
        ) : (
          <Welcome
            onNewSession={newSession}
            onOpenWorkbench={() => setPanel("workbench")}
            toolNames={tools.map((t) => t.name)}
          />
        )}
      </main>
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
      <div className="welcome-logo">⚒</div>
      <h1>ForgeOps</h1>
      <p className="welcome-sub">
        工业设备智能运维 Agent · 自研 AgentForge 引擎（Plan-and-Execute / MCP / RAG / 沙箱工具）
      </p>
      <div className="welcome-cards">
        <div className="welcome-card" onClick={onOpenWorkbench}>
          <h3>🏭 设备诊断</h3>
          <p>打开设备台账，一键诊断：知识检索 → 振动数据分析 → 结论 → 自动生成维修工单</p>
        </div>
        <div className="welcome-card" onClick={onNewSession}>
          <h3>🧠 规划模式</h3>
          <p>侧栏切换到「规划模式」，体验 Plan-and-Execute 编排与决策链路 Trace</p>
        </div>
        <div className="welcome-card" onClick={onNewSession}>
          <h3>🧮 沙箱计算</h3>
          <p>输入「计算 128*365+42」，观察 python_repl 沙箱实时执行</p>
        </div>
      </div>
      <p className="welcome-tools">已启用工具：{toolNames.join(" · ") || "…"}</p>
    </div>
  );
}
