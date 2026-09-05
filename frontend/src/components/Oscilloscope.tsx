import { useEffect, useRef } from "react";
import type { WaveformData } from "../types";

/**
 * 设备振动示波器（Canvas 2D + 手写 FFT）。
 *
 * 上半屏：真实传感器时域波形（服务端下采样回放），扫描线 + 辉光。
 * 下半屏：在前端用 TypeScript 实现的迭代 radix-2 FFT 做频谱柱状图，
 *          带峰值保持（peak-hold）衰减 —— 示波器的经典行为。
 * rAF 渲染循环，devicePixelRatio 自适应，ResizeObserver 跟随布局。
 */

const FFT_SIZE = 2048;

/** 迭代基-2 FFT（就地），输入长度须为 2 的幂。 */
function fftInPlace(re: Float32Array, im: Float32Array): void {
  const n = re.length;
  // 位反转置换
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j], re[i]];
      [im[i], im[j]] = [im[j], im[i]];
    }
  }
  // 蝶形运算
  for (let len = 2; len <= n; len <<= 1) {
    const ang = (-2 * Math.PI) / len;
    const wRe = Math.cos(ang);
    const wIm = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let curRe = 1;
      let curIm = 0;
      for (let k = 0; k < len / 2; k++) {
        const uRe = re[i + k];
        const uIm = im[i + k];
        const vRe = re[i + k + len / 2] * curRe - im[i + k + len / 2] * curIm;
        const vIm = re[i + k + len / 2] * curIm + im[i + k + len / 2] * curRe;
        re[i + k] = uRe + vRe;
        im[i + k] = uIm + vIm;
        re[i + k + len / 2] = uRe - vRe;
        im[i + k + len / 2] = uIm - vIm;
        const nextRe = curRe * wRe - curIm * wIm;
        curIm = curRe * wIm + curIm * wRe;
        curRe = nextRe;
      }
    }
  }
}

interface Props {
  data: WaveformData | null;
  bpfoHz?: number;
}

export default function Oscilloscope({ data, bpfoHz }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dataRef = useRef<WaveformData | null>(data);
  const bpfoRef = useRef<number | undefined>(bpfoHz);
  const peaksRef = useRef<Float32Array | null>(null);
  dataRef.current = data;
  bpfoRef.current = bpfoHz;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let width = 0;
    let height = 0;

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const rect = canvas.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);

    const draw = (ms: number) => {
      const t = ms / 1000;
      const d = dataRef.current;
      ctx.clearRect(0, 0, width, height);

      const waveH = height * 0.42;
      const specY = waveH + 14;
      const specH = height - specY - 6;

      // ---------- 时域波形 ----------
      ctx.strokeStyle = "rgba(79, 143, 247, 0.18)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, waveH / 2);
      ctx.lineTo(width, waveH / 2);
      ctx.stroke();

      if (d && d.vibration_mm_s.length > 1) {
        const xs = d.time_s;
        const vs = d.vibration_mm_s;
        const t0 = xs[0];
        const t1 = xs[xs.length - 1];
        const span = Math.max(t1 - t0, 1e-6);
        const maxAbs = Math.max(0.5, ...vs.map((v) => Math.abs(v)));

        // 波形辉光曲线
        ctx.save();
        ctx.shadowColor = "rgba(79, 143, 247, 0.8)";
        ctx.shadowBlur = 6;
        ctx.strokeStyle = "#6ba1ff";
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        // 按像素密度抽点，避免过密糊成实心带
        const step = Math.max(1, Math.floor(vs.length / Math.max(width * 1.2, 300)));
        for (let i = 0; i < vs.length; i += step) {
          const x = ((xs[i] - t0) / span) * width;
          const y = waveH / 2 - (vs[i] / maxAbs) * (waveH / 2 - 6);
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.lineTo(width, waveH / 2 - (vs[vs.length - 1] / maxAbs) * (waveH / 2 - 6));
        ctx.stroke();
        ctx.restore();

        // 扫描线（回放位置）
        const sweepX = ((t * 0.22) % 1) * width;
        const grad = ctx.createLinearGradient(sweepX - 46, 0, sweepX, 0);
        grad.addColorStop(0, "rgba(107, 161, 255, 0)");
        grad.addColorStop(1, "rgba(107, 161, 255, 0.35)");
        ctx.fillStyle = grad;
        ctx.fillRect(sweepX - 46, 0, 46, waveH);
        ctx.fillStyle = "rgba(107, 161, 255, 0.9)";
        ctx.fillRect(sweepX, 0, 1.2, waveH);
      } else {
        ctx.fillStyle = "rgba(139, 148, 158, 0.7)";
        ctx.font = "10px ui-monospace, monospace";
        ctx.fillText("AWAITING SENSOR TELEMETRY…", 10, waveH / 2 - 8);
      }

      ctx.fillStyle = "rgba(139, 148, 158, 0.85)";
      ctx.font = "9px ui-monospace, monospace";
      ctx.fillText("TIME DOMAIN · vibration_mm_s", 8, 12);
      if (d) {
        ctx.textAlign = "right";
        ctx.fillText(`RMS ${d.rms_mm_s.toFixed(2)} mm/s · ${d.iso10816_status.toUpperCase()}`, width - 8, 12);
        ctx.textAlign = "left";
      }

      // ---------- 频谱（前端 FFT）----------
      ctx.fillStyle = "rgba(139, 148, 158, 0.85)";
      ctx.fillText(`FFT ${FFT_SIZE} · HANN WINDOW`, 8, specY + 12);

      if (d && d.vibration_mm_s.length > 16) {
        const n = FFT_SIZE;
        const re = new Float32Array(n);
        const im = new Float32Array(n);
        const vs = d.vibration_mm_s;
        const count = Math.min(vs.length, n);
        // Hann 窗
        for (let i = 0; i < count; i++) {
          re[i] = vs[i] * 0.5 * (1 - Math.cos((2 * Math.PI * i) / (count - 1)));
        }
        fftInPlace(re, im);

        // 频率轴：0 .. 1000 Hz
        const dt = d.time_s[Math.min(1, d.time_s.length - 1)] - d.time_s[0];
        const fs = dt > 0 ? 1 / dt : 2000;
        const binHz = fs / n;
        const maxHz = 1000;
        const bars = Math.min(140, Math.floor(maxHz / binHz));
        if (bars > 4) {
          if (!peaksRef.current || peaksRef.current.length !== bars) {
            peaksRef.current = new Float32Array(bars);
          }
          const peaks = peaksRef.current;
          const barW = width / bars;
          // 归一化：最大幅度按 6.5 mm/s 标定（AC-017 报警幅值）
          const scale = specH / 7.0;
          for (let b = 0; b < bars; b++) {
            let mag = 0;
            // 每根柱聚合若干 bin
            const binStart = Math.floor(b * (maxHz / bars) / binHz);
            const binEnd = Math.max(binStart + 1, Math.floor((b + 1) * (maxHz / bars) / binHz));
            for (let k = binStart; k < binEnd && k < n / 2; k++) {
              mag = Math.max(mag, Math.hypot(re[k], im[k]) * (4 / count) * 2);
            }
            const h = Math.min(specH - 4, mag * scale);
            peaks[b] = Math.max(peaks[b] * 0.985, h);

            const x = b * barW;
            const g = ctx.createLinearGradient(0, specY + specH, 0, specY + specH - h);
            g.addColorStop(0, "rgba(79, 143, 247, 0.85)");
            g.addColorStop(1, "rgba(137, 87, 229, 0.9)");
            ctx.fillStyle = g;
            ctx.fillRect(x + 0.5, specY + specH - h, Math.max(1, barW - 1.5), h);

            // 峰值保持帽
            ctx.fillStyle = "rgba(210, 153, 34, 0.85)";
            ctx.fillRect(x + 0.5, specY + specH - peaks[b] - 1.5, Math.max(1, barW - 1.5), 1.5);
          }

          // BPFO 特征频率标记线
          const marker = bpfoRef.current;
          if (marker && marker < maxHz) {
            const mx = (marker / maxHz) * width;
            ctx.strokeStyle = "rgba(248, 81, 73, 0.75)";
            ctx.setLineDash([4, 4]);
            ctx.beginPath();
            ctx.moveTo(mx, specY + 16);
            ctx.lineTo(mx, specY + specH);
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.fillStyle = "rgba(248, 81, 73, 0.95)";
            ctx.font = "9px ui-monospace, monospace";
            ctx.fillText(`BPFO ${marker.toFixed(0)}Hz`, mx + 4, specY + 24);
          }
        }
      } else {
        ctx.fillStyle = "rgba(139, 148, 158, 0.7)";
        ctx.fillText("SPECTRUM STANDBY", 10, specY + specH / 2);
      }

      raf = requestAnimationFrame(draw);
    };

    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
    };
  }, []);

  return (
    <div className="scope-frame">
      <div className="scope-header">
        <span className="hud-label">📳 VIBRATION OSCILLOSCOPE</span>
        <span className="scope-rec">● REC</span>
      </div>
      <canvas ref={canvasRef} className="scope-canvas" />
    </div>
  );
}
