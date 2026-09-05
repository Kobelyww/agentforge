"""ForgeOps domain tools.

- ``sensor_analysis``: real numeric analysis (FFT / RMS) over packaged sensor
  CSVs via numpy — the agent reasons over measured data, not prose.
- ``create_work_order``: the structured-output guardrail — the model must emit
  schema-valid work order arguments or the tool feeds a validation error back
  for self-correction; valid orders are persisted for business systems.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

from agentforge.persistence.db import Database
from agentforge.persistence.models import Approval, Memory, WorkOrder
from agentforge.tools.base import Tool, ToolContext, ToolResult, validate_args

_DATA_DIR = Path(__file__).resolve().parent / "data"
_ALARMS = {"good": 2.8, "allow": 4.5, "alarm": 7.1}  # ISO 10816-3 (rigid, medium machines)

logger = logging.getLogger("agentforge.forgeops")

# Keep references to in-flight webhook tasks so the event loop does not GC them.
_webhook_tasks: set[asyncio.Task] = set()


def _fire_webhook(url: str, payload: dict) -> None:
    """Best-effort outbound notification; failures are logged, never raised."""
    import httpx

    async def _post() -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
        except Exception:  # noqa: BLE001 - integration failures must not break the loop
            logger.warning("webhook delivery to %s failed", url, exc_info=True)

    task = asyncio.create_task(_post())
    _webhook_tasks.add(task)
    task.add_done_callback(_webhook_tasks.discard)


async def _maybe_await(value: Any) -> None:
    """Support sync and async emit callables."""
    if asyncio.iscoroutine(value):
        await value


def load_equipment() -> list[dict]:
    return json.loads((_DATA_DIR / "equipment.json").read_text(encoding="utf-8"))


class SensorAnalysisTool(Tool):
    name = "sensor_analysis"
    description = (
        "分析设备振动传感器数据（CSV 时序）。operation=spectrum_peaks 返回 FFT 主峰频率及占比；"
        "operation=rms 返回整体振动烈度与 ISO 10816 状态判定。用于设备故障诊断。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "equipment_id": {"type": "string", "description": "设备编号，如 AC-017"},
            "operation": {"type": "string", "enum": ["spectrum_peaks", "rms"], "description": "分析类型"},
            "top_n": {"type": "integer", "description": "频谱返回的峰值数量，默认 3"},
        },
        "required": ["equipment_id", "operation"],
    }

    def __init__(self, *, timeout: float = 15.0) -> None:
        self.timeout = timeout

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        if error := validate_args(args, self.parameters):
            return ToolResult(ok=False, output="", error=f"invalid arguments: {error}")

        equipment_id = str(args["equipment_id"]).upper()
        operation = args["operation"]
        top_n = int(args.get("top_n", 3))

        equipment = next((e for e in load_equipment() if e["id"] == equipment_id), None)
        if equipment is None:
            known = ", ".join(e["id"] for e in load_equipment())
            return ToolResult(ok=False, output="", error=f"unknown equipment {equipment_id!r} (known: {known})")

        csv_path = _DATA_DIR / "sensors" / equipment["sensor_file"]
        if not csv_path.is_file():
            return ToolResult(ok=False, output="", error=f"no sensor data for {equipment_id}")

        try:
            raw = np.genfromtxt(csv_path, delimiter=",", names=True)
        except Exception as exc:
            return ToolResult(ok=False, output="", error=f"failed to parse sensor CSV: {exc}")

        t = np.asarray(raw["time_s"], dtype=np.float64)
        x = np.asarray(raw["vibration_mm_s"], dtype=np.float64)
        duration = float(t[-1] - t[0]) if len(t) > 1 else 0.0
        fs = (len(t) - 1) / duration if duration > 0 else 0.0
        rms = float(np.sqrt(np.mean(x**2)))

        status = (
            "good" if rms <= _ALARMS["good"]
            else "allow" if rms <= _ALARMS["allow"]
            else "alarm" if rms <= _ALARMS["alarm"]
            else "danger"
        )

        if operation == "rms":
            payload = {
                "equipment_id": equipment_id,
                "operation": "rms",
                "duration_s": round(duration, 3),
                "rms_mm_s": round(rms, 3),
                "iso10816_status": status,
                "limits_mm_s": _ALARMS,
            }
            return ToolResult(ok=True, output=json.dumps(payload, ensure_ascii=False), meta=payload)

        # spectrum_peaks — Hann window; ×4/N compensates the window's 0.5
        # coherent gain so amplitudes read as true sinusoid amplitudes.
        windowed = (x - x.mean()) * np.hanning(len(x))
        spectrum = np.abs(np.fft.rfft(windowed)) * 4.0 / len(x)
        freqs = np.fft.rfftfreq(len(x), d=1.0 / fs) if fs > 0 else np.array([])

        peaks: list[dict] = []
        order = np.argsort(-spectrum)
        for idx in order:
            f = float(freqs[idx])
            if len(peaks) >= max(top_n, 1):
                break
            if spectrum[idx] <= 0.05:
                break
            if any(abs(f - p["freq_hz"]) < 1.5 for p in peaks):
                continue  # skip neighbours of an existing peak
            peaks.append({
                "freq_hz": round(f, 2),
                "amplitude_mm_s": round(float(spectrum[idx]), 3),
                "ratio_to_1x": round(f / equipment["rotational_hz"], 3),
            })
        peaks.sort(key=lambda p: -p["amplitude_mm_s"])

        payload = {
            "equipment_id": equipment_id,
            "operation": "spectrum_peaks",
            "sampling_rate_hz": round(fs, 1),
            "duration_s": round(duration, 3),
            "frequency_resolution_hz": round(1.0 / duration, 3) if duration > 0 else 0.0,
            "rms_mm_s": round(rms, 3),
            "iso10816_status": status,
            "rotational_hz": equipment["rotational_hz"],
            "peaks": peaks,
        }
        return ToolResult(ok=True, output=json.dumps(payload, ensure_ascii=False), meta=payload)


class CreateWorkOrderTool(Tool):
    """Structured-output guardrail: schema-validated, persisted, auditable."""

    name = "create_work_order"
    description = (
        "创建维修工单（结构化）。参数必须完整：equipment_id、title、fault_type、confidence(0-1)、"
        "priority(P1-P4)、actions(处置步骤数组)、parts(备件数组)、estimated_hours。"
        "参数不符合 Schema 会被拒绝并返回错误说明。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "equipment_id": {"type": "string"},
            "title": {"type": "string"},
            "fault_type": {"type": "string"},
            "confidence": {"type": "number"},
            "priority": {"type": "string", "enum": ["P1", "P2", "P3", "P4"]},
            "actions": {"type": "array", "items": {"type": "string"}},
            "parts": {"type": "array", "items": {"type": "string"}},
            "estimated_hours": {"type": "number"},
        },
        "required": ["equipment_id", "title", "fault_type", "confidence", "priority", "actions"],
    }

    def __init__(self, db: Database, *, timeout: float = 10.0) -> None:
        self._db = db
        self.timeout = timeout

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        if error := validate_args(args, self.parameters):
            return ToolResult(ok=False, output="", error=f"invalid arguments: {error}")

        if not 0.0 <= float(args["confidence"]) <= 1.0:
            return ToolResult(ok=False, output="", error="confidence must be within [0, 1]")

        # ---- Human-in-the-loop gate ----
        # P1/P2 orders interrupt real production, so unless running in
        # auto-approve mode (CI/eval/zero-config demo) they wait for an
        # explicit human decision, streaming an approval_required event to
        # the UI while polling.
        approval = None
        needs_approval = (not ctx.auto_approve) and args["priority"] in ("P1", "P2")
        if needs_approval:
            approval = self._db.add_approval(
                Approval(
                    session_id=ctx.session_id,
                    action="create_work_order",
                    payload=dict(args),
                )
            )
            if ctx.emit is not None:
                await _maybe_await(ctx.emit({
                    "type": "approval_required",
                    "approval_id": approval.id,
                    "action": "create_work_order",
                    "payload": dict(args),
                    "message": f"创建 {args['priority']} 级维修工单需要人工批准",
                }))

            deadline = time.monotonic() + max(self.timeout - 5.0, 5.0)
            decision = "pending"
            while time.monotonic() < deadline:
                current = await asyncio.to_thread(self._db.get_approval, approval.id)
                decision = current.status if current else "pending"
                if decision != "pending":
                    break
                await asyncio.sleep(0.4)

            if decision == "pending":
                return ToolResult(
                    ok=False, output="",
                    error="审批等待超时，工单未创建。请重新发起。",
                    meta={"approval_id": approval.id, "decision": "timeout"},
                )
            if decision == "rejected":
                return ToolResult(
                    ok=False, output="",
                    error="用户拒绝了工单创建。请向用户确认后续处理方式。",
                    meta={"approval_id": approval.id, "decision": "rejected"},
                )

        seq = self._db.count_work_orders() + 1
        wo = self._db.add_work_order(
            WorkOrder(
                code=f"WO-{seq:06d}",
                session_id=ctx.session_id,
                equipment_id=str(args["equipment_id"]).upper(),
                title=str(args["title"])[:300],
                fault_type=str(args["fault_type"])[:120],
                confidence=float(args["confidence"]),
                priority=str(args["priority"]),
                actions=[str(a) for a in args.get("actions", [])],
                parts=[str(p) for p in args.get("parts", [])],
                estimated_hours=float(args.get("estimated_hours", 0.0)),
            )
        )

        # Long-term memory: durable diagnosis facts keyed by equipment, so
        # future sessions can recall this machine's history (MemGPT-style).
        self._db.add_memory(
            Memory(
                session_id=ctx.session_id,
                equipment_id=wo.equipment_id,
                kind="diagnosis",
                content=(
                    f"{wo.equipment_id} 诊断为 {wo.fault_type}"
                    f"（置信度 {wo.confidence:.2f}），生成工单 {wo.code}（{wo.priority}）"
                ),
            )
        )

        payload = {
            "work_order_id": wo.code,
            "status": wo.status,
            "equipment_id": wo.equipment_id,
            "priority": wo.priority,
            "fault_type": wo.fault_type,
            "confidence": wo.confidence,
            "actions": wo.actions,
            "parts": wo.parts,
            "estimated_hours": wo.estimated_hours,
            "approved_via": approval.id if approval else "auto",
        }

        if ctx.settings.server.webhook_url:
            _fire_webhook(
                ctx.settings.server.webhook_url,
                {"event": "work_order.created", "work_order": payload},
            )

        return ToolResult(
            ok=True,
            output=f"工单已创建并通过 Schema 校验: {json.dumps(payload, ensure_ascii=False)}",
            meta=payload,
        )
