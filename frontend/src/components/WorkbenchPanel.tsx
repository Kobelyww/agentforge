import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Equipment } from "../types";

export default function WorkbenchPanel({
  onSelectScenario,
  onClose,
}: {
  onSelectScenario: (equipment: Equipment) => void;
  onClose: () => void;
}) {
  const [equipment, setEquipment] = useState<Equipment[]>([]);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.listEquipment().then(setEquipment).catch((e) => setError(e.message));
  }, []);

  return (
    <div className="doc-panel">
      <div className="doc-panel-header">
        <h2>🏭 设备台账</h2>
        <button className="btn-icon" onClick={onClose}>×</button>
      </div>

      {error && <div className="doc-error">⚠ {error}</div>}
      <div className="doc-list">
        {equipment.map((eq) => (
          <div key={eq.id} className="doc-item equipment-item">
            <div className="doc-info">
              <div className="doc-name">
                {eq.name}
                <span className={`eq-status ${eq.status}`}>
                  {eq.status === "alarm" ? "⚠ 报警" : eq.status === "watch" ? "👀 关注" : "✓ 正常"}
                </span>
              </div>
              <div className="doc-meta">
                {eq.location} · {eq.model} · {eq.rated_rpm} r/min
                {(eq.open_work_orders ?? 0) > 0 && ` · ${eq.open_work_orders} 个未关工单`}
              </div>
            </div>
            <button className="btn-ghost small" onClick={() => onSelectScenario(eq)}>
              诊断
            </button>
          </div>
        ))}
      </div>

      <div className="doc-search">
        <div className="section-title">运维小贴士</div>
        <p className="doc-hint">
          点击设备的「诊断」按钮，Agent 将以规划模式执行：知识检索 → 振动数据分析 → 诊断结论 → 自动生成维修工单。
          你也可以直接在会话中输入「诊断 AC-017 空压机异响」。
        </p>
      </div>
      <input ref={fileRef} type="file" hidden />
    </div>
  );
}
