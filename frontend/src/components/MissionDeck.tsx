import { useEffect, useState } from "react";
import { api } from "../api";
import Oscilloscope from "./Oscilloscope";
import RadialGauge from "./RadialGauge";
import type { Equipment, WaveformData } from "../types";

/**
 * 遥测甲板：自动选中一台告警设备，示波器 + ISO 仪表盘并排。
 * 数据全部来自真实传感器 CSV（服务端下采样，前端 FFT）。
 */

export default function MissionDeck() {
  const [equipments, setEquipments] = useState<Equipment[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [waveform, setWaveform] = useState<WaveformData | null>(null);

  useEffect(() => {
    api.listEquipment().then((eq) => {
      setEquipments(eq);
      const alarm = eq.find((e) => e.status === "alarm") ?? eq[0];
      if (alarm) setActiveId(alarm.id);
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!activeId) return;
    api.getWaveform(activeId).then(setWaveform).catch(() => setWaveform(null));
  }, [activeId]);

  const bpfo = waveform ? waveform.rotational_hz * 3.59 : undefined; // 6205 轴承 BPFO 系数

  return (
    <div className="mission-deck">
      <div className="mission-tabs">
        {equipments.map((eq) => (
          <button
            key={eq.id}
            className={`mission-tab ${eq.id === activeId ? "active" : ""}`}
            onClick={() => setActiveId(eq.id)}
          >
            <span className={`eq-dot ${eq.status}`} />
            {eq.id}
            <span className="mission-tab-sub">{eq.name.replace(eq.id, "").trim() || eq.model}</span>
          </button>
        ))}
      </div>
      <div className="mission-body">
        <Oscilloscope data={waveform} bpfoHz={bpfo} />
        {waveform && (
          <RadialGauge
            rms={waveform.rms_mm_s}
            status={waveform.iso10816_status}
            equipmentId={waveform.equipment_id}
          />
        )}
      </div>
    </div>
  );
}
