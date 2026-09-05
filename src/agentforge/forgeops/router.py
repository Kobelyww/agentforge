"""ForgeOps routes: equipment registry, work orders, session trace."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from agentforge.forgeops.tools import load_equipment
from agentforge.server.auth import require_api_key

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
