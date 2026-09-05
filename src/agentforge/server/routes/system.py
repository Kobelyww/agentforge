"""System routes: health, readiness, metrics."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request):
    state = request.app.state.state
    db_ok = await __import__("asyncio").to_thread(state.db.health_check)
    return {
        "status": "ready" if db_ok else "degraded",
        "database": db_ok,
        "providers": state.registry.names(),
        "tools": state.tool_registry.names(),
        "chunks": state.db.count_chunks(),
    }


@router.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
