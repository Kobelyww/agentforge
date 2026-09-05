import { useEffect } from "react";
import { animate } from "framer-motion";

/**
 * SVG 径向仪表盘：RMS 振动烈度按 ISO 10816 分区（良好/允许/报警/危险），
 * 指针 spring 动画 + 数值滚动读数。纯 SVG + framer-motion，无图表库。
 */

const MAX_MM_S = 8;
const ARC_START = -120; // 度
const ARC_END = 120;

const ZONES = [
  { upTo: 2.8, color: "#3fb950", label: "GOOD" },
  { upTo: 4.5, color: "#d29922", label: "ALLOW" },
  { upTo: 7.1, color: "#f0883e", label: "ALARM" },
  { upTo: MAX_MM_S, color: "#f85149", label: "DANGER" },
];

function polar(cx: number, cy: number, r: number, angleDeg: number): [number, number] {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}

function zoneArc(cx: number, cy: number, r: number, from: number, to: number): string {
  const a0 = ARC_START + (from / MAX_MM_S) * (ARC_END - ARC_START);
  const a1 = ARC_START + (Math.min(to, MAX_MM_S) / MAX_MM_S) * (ARC_END - ARC_START);
  const [x0, y0] = polar(cx, cy, r, a0);
  const [x1, y1] = polar(cx, cy, r, a1);
  const large = a1 - a0 > 180 ? 1 : 0;
  return `M ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1}`;
}

export default function RadialGauge({
  rms,
  status,
  equipmentId,
}: {
  rms: number;
  status: string;
  equipmentId: string;
}) {
  const size = 190;
  const cx = size / 2;
  const cy = size / 2 + 8;
  const r = 74;
  const needleAngle = ARC_START + (Math.min(rms, MAX_MM_S) / MAX_MM_S) * (ARC_END - ARC_START);

  useEffect(() => {
    const controls = animate(0, rms, {
      duration: 1.2,
      ease: "easeOut",
      onUpdate: (v) => {
        const el = document.getElementById(`gauge-readout-${equipmentId}`);
        if (el) el.textContent = `${v.toFixed(2)}`;
      },
    });
    return () => controls.stop();
  }, [rms, equipmentId]);

  const zoneColor = ZONES.find((z) => rms <= z.upTo)?.color ?? "#f85149";

  return (
    <div className="gauge-frame">
      <div className="hud-label">📊 ISO 10816 · RMS GAUGE</div>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {/* 刻度 */}
        {Array.from({ length: 17 }, (_, i) => {
          const angle = ARC_START + (i / 16) * (ARC_END - ARC_START);
          const [x1, y1] = polar(cx, cy, r - 12, angle);
          const [x2, y2] = polar(cx, cy, r - (i % 4 === 0 ? 4 : 8), angle);
          return (
            <line
              key={i}
              x1={x1} y1={y1} x2={x2} y2={y2}
              stroke={i % 4 === 0 ? "#8b949e" : "#30363d"}
              strokeWidth={i % 4 === 0 ? 1.6 : 1}
            />
          );
        })}
        {/* ISO 分区弧 */}
        {[0, ...ZONES.map((z) => z.upTo)].slice(0, -1).map((from, i) => (
          <path
            key={i}
            d={zoneArc(cx, cy, r + 8, from, ZONES[i].upTo)}
            stroke={ZONES[i].color}
            strokeWidth={5}
            fill="none"
            opacity={0.85}
            strokeLinecap="butt"
          />
        ))}
        {/* 指针 */}
        <g transform={`rotate(${needleAngle} ${cx} ${cy})`}>
          <line
            x1={cx} y1={cy + 10} x2={cx} y2={cy - r + 16}
            stroke={zoneColor} strokeWidth={2.4}
            style={{ transition: "stroke 0.6s" }}
          />
        </g>
        <circle cx={cx} cy={cy} r={5} fill={zoneColor} />
        <text
          id={`gauge-readout-${equipmentId}`}
          x={cx} y={cy + 38}
          textAnchor="middle"
          className="gauge-readout"
          fill={zoneColor}
        >
          0.00
        </text>
        <text x={cx} y={cy + 54} textAnchor="middle" className="gauge-unit">
          mm/s · {status.toUpperCase()}
        </text>
      </svg>
    </div>
  );
}
