"""Chat routes: sessions CRUD + SSE streaming chat + tool/provider discovery."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agentforge.observability.metrics import ACTIVE_STREAMS
from agentforge.persistence.models import Message
from agentforge.server.auth import require_api_key
from agentforge.server.sse import sse_comment, sse_event

logger = logging.getLogger("agentforge.api.chat")

router = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])


class CreateSessionRequest(BaseModel):
    title: str = Field(default="新会话", max_length=200)


class RenameSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=32000)
    model: str | None = Field(default=None, description="provider/model, e.g. glm/glm-4-plus")
    orchestrator: str | None = Field(default=None, description="react | plan_execute")
    auto_approve: bool | None = Field(
        default=None,
        description="False 启用 human-in-the-loop：P1/P2 工单创建前等待 /approvals 决定",
    )


# ---------- sessions ----------
@router.post("/sessions", status_code=201)
async def create_session(body: CreateSessionRequest, request: Request):
    state = request.app.state.state
    session = await asyncio.to_thread(state.db.create_session)
    if body.title:
        session = await asyncio.to_thread(state.db.update_session, session.id, title=body.title)
    return _session_dict(session)


@router.get("/sessions")
async def list_sessions(request: Request):
    state = request.app.state.state
    sessions = await asyncio.to_thread(state.db.list_sessions)
    return [_session_dict(s) for s in sessions]


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    state = request.app.state.state
    session = await asyncio.to_thread(state.db.get_session, session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    messages = await asyncio.to_thread(state.db.list_messages, session_id)
    return {**_session_dict(session), "messages": [_message_dict(m) for m in messages]}


@router.patch("/sessions/{session_id}")
async def rename_session(session_id: str, body: RenameSessionRequest, request: Request):
    state = request.app.state.state
    session = await asyncio.to_thread(state.db.update_session, session_id, title=body.title)
    if session is None:
        raise HTTPException(404, "session not found")
    return _session_dict(session)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, request: Request):
    state = request.app.state.state
    deleted = await asyncio.to_thread(state.db.delete_session, session_id)
    if not deleted:
        raise HTTPException(404, "session not found")


# ---------- chat (SSE) ----------
@router.post("/sessions/{session_id}/chat")
async def chat(session_id: str, body: ChatRequest, request: Request):
    state = request.app.state.state
    session = await asyncio.to_thread(state.db.get_session, session_id)
    if session is None:
        raise HTTPException(404, "session not found")

    async def event_stream():
        ACTIVE_STREAMS.inc()
        queue: asyncio.Queue = asyncio.Queue()
        _SENTINEL = object()

        async def produce():
            try:
                async for event in state.agent.run(
                    session_id, body.content, model=body.model,
                    orchestrator=body.orchestrator, auto_approve=body.auto_approve,
                ):
                    await queue.put(sse_event(event.type, event.data))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("chat stream failed")
                await queue.put(sse_event("error", {"message": "internal error, see server logs"}))
            finally:
                await queue.put(_SENTINEL)

        producer = asyncio.create_task(produce())
        try:
            yield sse_event("open", {"session_id": session_id})
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield sse_comment()
                    continue
                if item is _SENTINEL:
                    break
                yield item
        finally:
            producer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await producer
            ACTIVE_STREAMS.dec()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------- discovery ----------
@router.get("/tools")
async def list_tools(request: Request):
    state = request.app.state.state
    return [
        {"name": t.name, "description": t.description, "parameters": t.parameters, "timeout": t.timeout}
        for t in state.tool_registry.tools.values()
    ]


@router.get("/providers")
async def list_providers(request: Request):
    state = request.app.state.state
    return state.registry.public_specs()


# ---------- trace ----------
@router.get("/sessions/{session_id}/trace")
async def session_trace(session_id: str, request: Request):
    """Full decision trace for one session: plan → steps → tools → final.

    Rebuilt purely from the persisted message log (the audit trail is the
    source of truth, not in-memory state), so traces survive restarts.
    """
    import json as _json

    state = request.app.state.state
    session = await asyncio.to_thread(state.db.get_session, session_id)
    if session is None:
        raise HTTPException(404, "session not found")

    messages = await asyncio.to_thread(state.db.list_messages, session_id)
    plan: dict | None = None
    final: str | None = None
    user_task = ""
    steps_by_id: dict[str, dict] = {}
    step_order: list[str] = []
    tool_calls_total = 0
    tokens_total = 0

    for m in messages:
        meta = m.meta or {}
        kind = meta.get("kind")
        tokens_total += m.tokens or 0
        if m.role == "user":
            if not user_task:
                user_task = m.content
            elif kind == "step_instruction" or m.content.startswith("【当前步骤"):
                step_id = meta.get("step_id") or m.content.split("】")[0].replace("【当前步骤", "").strip()
                head = m.content.split("\n", 1)[0]
                title = head.split("】")[-1].strip()
                instruction = (m.content.partition("\n指令：")[2] if "指令：" in m.content else "").strip()
                if step_id and step_id not in steps_by_id:
                    steps_by_id[step_id] = {
                        "step_id": step_id, "title": title,
                        "instruction": instruction,
                        "summary": "", "tools": [],
                    }
                    step_order.append(step_id)
            continue
        if m.role == "tool":
            tool_calls_total += 1
            step_id = meta.get("step_id")
            target = steps_by_id.get(step_id) if step_id else None
            if target is None and step_order:
                target = steps_by_id[step_order[-1]]
            if target is not None:
                target["tools"].append({
                    "name": m.name,
                    "ok": meta.get("ok", True),
                    "content": (m.content or "")[:400],
                    "latency_ms": m.latency_ms,
                })
            continue
        if m.role == "assistant":
            if kind == "plan":
                try:
                    plan = _json.loads(m.content)
                except _json.JSONDecodeError:
                    plan = None
            elif kind == "final":
                final = m.content
            elif kind == "step" and meta.get("step_id"):
                step = steps_by_id.setdefault(
                    meta["step_id"],
                    {"step_id": meta["step_id"], "title": "", "instruction": "", "summary": "", "tools": []},
                )
                step["summary"] = (m.content or "")[:1200]
                if meta.get("iteration"):
                    step["iterations"] = meta["iteration"]

    steps = [steps_by_id[sid] for sid in step_order if sid in steps_by_id]
    if plan:
        # Ensure every planned step appears (even unexecuted ones).
        for planned in plan.get("steps", []):
            if planned["id"] not in steps_by_id:
                steps.append({"step_id": planned["id"], "title": planned["title"],
                              "instruction": planned["instruction"], "summary": "", "tools": []})
        plan_titles = {s["id"]: s["title"] for s in plan.get("steps", [])}
        for step in steps:
            step["title"] = step["title"] or plan_titles.get(step["step_id"], "")

    return {
        "session_id": session_id,
        "title": session.title,
        "orchestrator": "plan_execute" if plan else "react",
        "user_task": user_task,
        "plan": plan,
        "steps": steps,
        "final": final,
        "totals": {"tool_calls": tool_calls_total, "tokens_est": tokens_total,
                   "messages": len(messages)},
    }


# ---------- serializers ----------
def _session_dict(session) -> dict:
    return {
        "id": session.id,
        "title": session.title,
        "provider": session.provider,
        "model": session.model,
        "summary": session.summary,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def _message_dict(row: Message) -> dict:
    return {
        "id": row.id,
        "seq": row.seq,
        "role": row.role,
        "content": row.content,
        "tool_calls": row.tool_calls,
        "tool_call_id": row.tool_call_id,
        "name": row.name,
        "tokens": row.tokens,
        "latency_ms": row.latency_ms,
        "meta": row.meta,
        "created_at": row.created_at,
    }
