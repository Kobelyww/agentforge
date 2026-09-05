"""ForgeOps routes: equipment registry, work orders, session trace."""

from __future__ import annotations

import asyncio
import pathlib

from fastapi import APIRouter, Depends, HTTPException, Request

from agentforge.forgeops.tools import load_equipment
from agentforge.server.auth import require_api_key

_DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"

router = APIRouter(prefix="/api/forgeops", dependencies=[Depends(require_api_key)])


@router.get("/equipment")
async def equipment(request: Request):
    state = request.app.state.state
    items = load_equipment()
    for item in items:
        recent = [
            wo
            for wo in await asyncio.to_thread(state.db.list_work_orders, 100)
            if wo.equipment_id == item["id"]
        ]
        item["open_work_orders"] = sum(1 for wo in recent if wo.status == "open")
    return items


@router.get("/workorders")
async def workorders(request: Request):
    state = request.app.state.state
    rows = await asyncio.to_thread(state.db.list_work_orders, 50)
    return [_wo_dict(wo) for wo in rows]


@router.post("/workorders/{code}/status")
async def update_workorder_status(code: str, request: Request):
    state = request.app.state.state
    body = await request.json()
    status = body.get("status", "")
    if status not in ("open", "in_progress", "done"):
        raise HTTPException(400, "status must be open | in_progress | done")
    updated = await asyncio.to_thread(state.db.update_work_order_status, code, status)
    if updated is None:
        raise HTTPException(404, "work order not found")
    return _wo_dict(updated)


# ---------- raw waveform for the UI oscilloscope ----------
@router.get("/equipment/{equipment_id}/waveform")
async def equipment_waveform(equipment_id: str, request: Request, points: int = 1600):
    """Time-domain vibration waveform for the frontend oscilloscope.

    Downsampled server-side with numpy (stride sampling preserves the shape
    of the signal for display; the frontend does its own FFT for spectrum).
    """
    import numpy as np

    equipment = next((e for e in load_equipment() if e["id"] == equipment_id.upper()), None)
    if equipment is None:
        raise HTTPException(404, "unknown equipment")
    csv_path = _DATA_DIR / "sensors" / equipment["sensor_file"]
    if not csv_path.is_file():
        raise HTTPException(404, "no sensor data for this equipment")

    def _load():
        raw = np.genfromtxt(csv_path, delimiter=",", names=True)
        t = np.asarray(raw["time_s"], dtype=np.float64)
        x = np.asarray(raw["vibration_mm_s"], dtype=np.float64)
        n = max(2, min(points, len(t)))
        stride = max(1, len(t) // n)
        t, x = t[::stride][:n], x[::stride][:n]
        rms = float(np.sqrt(np.mean(np.asarray(raw["vibration_mm_s"]) ** 2)))
        return t.tolist(), x.tolist(), rms, equipment["rotational_hz"]

    t, x, rms, rot_hz = await asyncio.to_thread(_load)
    return {
        "equipment_id": equipment["id"],
        "time_s": [round(v, 6) for v in t],
        "vibration_mm_s": [round(v, 6) for v in x],
        "rms_mm_s": round(rms, 3),
        "iso10816_status": (
            "good" if rms <= 2.8 else "allow" if rms <= 4.5
            else "alarm" if rms <= 7.1 else "danger"
        ),
        "rotational_hz": rot_hz,
    }


# ---------- human-in-the-loop approvals ----------
@router.get("/approvals")
async def list_approvals(request: Request, session_id: str | None = None):
    state = request.app.state.state
    rows = await asyncio.to_thread(state.db.list_approvals, session_id)
    return [_approval_dict(a) for a in rows]


@router.post("/approvals/{approval_id}/decide")
async def decide_approval(approval_id: str, request: Request):
    """Human decision on a pending high-priority action (P1/P2 work order)."""
    state = request.app.state.state
    body = await request.json()
    decision = body.get("decision", "")
    if decision not in ("approved", "rejected"):
        raise HTTPException(400, "decision must be approved | rejected")
    decided_by = str(body.get("decided_by", "user"))[:64]
    updated = await asyncio.to_thread(state.db.decide_approval, approval_id, decision, decided_by)
    if updated is None:
        raise HTTPException(404, "approval not found")
    return _approval_dict(updated)


# ---------- long-term memory ----------
@router.get("/memory")
async def list_memory(request: Request, equipment_id: str | None = None):
    state = request.app.state.state
    rows = await asyncio.to_thread(state.db.list_memories, equipment_id)
    return [
        {
            "id": m.id,
            "equipment_id": m.equipment_id,
            "kind": m.kind,
            "content": m.content,
            "session_id": m.session_id,
            "created_at": m.created_at,
        }
        for m in rows
    ]


def _approval_dict(a) -> dict:
    return {
        "id": a.id,
        "session_id": a.session_id,
        "action": a.action,
        "payload": a.payload,
        "status": a.status,
        "decided_by": a.decided_by,
        "created_at": a.created_at,
        "decided_at": a.decided_at,
    }


def _wo_dict(wo) -> dict:
    return {
        "id": wo.id,
        "code": wo.code,
        "session_id": wo.session_id,
        "equipment_id": wo.equipment_id,
        "title": wo.title,
        "fault_type": wo.fault_type,
        "confidence": wo.confidence,
        "priority": wo.priority,
        "actions": wo.actions,
        "parts": wo.parts,
        "estimated_hours": wo.estimated_hours,
        "status": wo.status,
        "created_at": wo.created_at,
    }
