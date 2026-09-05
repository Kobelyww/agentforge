import { useEffect, useState } from "react";
import { useElapsed } from "../hooks/useClock";

interface ReadyInfo {
  status: string;
  providers: string[];
  tools: string[];
  chunks: number;
}

/**
 * HUD 状态栏：实时 UTC 时钟、系统资源计数（/readyz 轮询）、LIVE 指示灯。
 */

export default function StatusRail({ sessionId }: { sessionId: string | null }) {
  const clock = useElapsed(1000);
  const [info, setInfo] = useState<ReadyInfo | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = () =>
      fetch("/readyz")
        .then((r) => r.json())
        .then((d) => alive && setInfo(d))
        .catch(() => undefined);
    poll();
    const t = window.setInterval(poll, 15000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);

  return (
    <div className="status-rail">
      <span className="rail-brand glow-text">⚒ FORGEOPS</span>
      <span className="rail-sep">/</span>
      <span className="rail-item">AGENT OS v0.1</span>
      <span className="rail-spacer" />
      <span className="rail-item">EQUIPMENT-KB <b>{info?.chunks ?? "—"}</b></span>
      <span className="rail-item">TOOLS <b>{info?.tools?.length ?? "—"}</b></span>
      <span className="rail-item">LLM <b>{info?.providers?.length ?? "—"}</b></span>
      <span className="rail-item rail-dim">SID <b>{sessionId ? `${sessionId.slice(0, 8)}…` : "—"}</b></span>
      <span className="rail-spacer" />
      <span className="rail-live">● LIVE</span>
      <span className="rail-clock">{clock}</span>
    </div>
  );
}
