import { useState } from "react";
import type { UIToolCall } from "../hooks/useChat";

const STATUS: Record<string, { label: string; className: string }> = {
  running: { label: "运行中", className: "running" },
  ok: { label: "成功", className: "ok" },
  fail: { label: "失败", className: "fail" },
};

export default function ToolCallCard({ toolCall }: { toolCall: UIToolCall }) {
  const [open, setOpen] = useState(false);
  const status = toolCall.running ? STATUS.running : toolCall.ok ? STATUS.ok : STATUS.fail;

  return (
    <div className={`tool-card ${status.className}`}>
      <button className="tool-card-header" onClick={() => setOpen((v) => !v)}>
        <span className="tool-status-dot" />
        <span className="tool-name">🛠 {toolCall.name}</span>
        <span className="tool-status">{status.label}</span>
        {typeof toolCall.latency_ms === "number" && toolCall.latency_ms > 0 && (
          <span className="tool-latency">{(toolCall.latency_ms / 1000).toFixed(2)}s</span>
        )}
        <span className="tool-chevron">{open ? "▾" : "▸"}</span>
      </button>

      {toolCall.running && <div className="tool-progress"><div className="tool-progress-bar" /></div>}

      {open && (
        <div className="tool-card-body">
          <div className="tool-block">
            <div className="tool-block-title">调用参数</div>
            <pre>{JSON.stringify(toolCall.arguments, null, 2)}</pre>
          </div>
          {toolCall.output !== undefined && (
            <div className="tool-block">
              <div className="tool-block-title">执行结果</div>
              <pre>{toolCall.output || toolCall.error || "(空)"}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
