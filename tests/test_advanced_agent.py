"""Advanced agent techniques: subagents, HITL approval, critic, memory, parallel tools."""

import asyncio

from agentforge.agent.core import _parse_critic
from agentforge.persistence.models import Memory as MemoryRow


def _build_stack(settings):
    from agentforge.agent.core import Agent
    from agentforge.llm.registry import ProviderRegistry
    from agentforge.persistence.db import Database
    from agentforge.rag.embeddings import build_embedder
    from agentforge.rag.retriever import Retriever
    from agentforge.tools.registry import build_default_registry

    db = Database(settings.db_url, settings.data_dir)
    registry = ProviderRegistry(settings.providers, settings.default_model)
    embedder = build_embedder("hashing", [])
    retriever = Retriever(db, embedder, settings)
    tools = build_default_registry(settings, db, retriever, registry=registry)
    agent = Agent(db, registry, tools, settings, retriever=retriever)
    return db, registry, retriever, tools, agent


async def test_dispatch_subagent_parallel_runs(settings):
    """Two specialists dispatched in one call run concurrently and both report."""
    db, registry, retriever, tools, _agent = _build_stack(settings)
    tool = tools.get("dispatch_subagent")
    assert tool is not None

    from agentforge.tools.base import ToolContext

    ctx = ToolContext(
        session_id=None, workspace=settings.data_dir / "ws",
        settings=settings, retriever=retriever,
    )
    result = await tool.execute(
        {"specialists": [
            {"role": "knowledge_researcher", "task": "检索设备手册中振动异常的判据与历史案例"},
            {"role": "data_analyst", "task": "分析设备 AC-017 的振动数据，operation=spectrum_peaks"},
        ]},
        ctx,
    )
    assert result.ok
    assert "知识研究员" in result.output and "数据分析师" in result.output
    assert result.meta["parallel"] is True
    # sub-agent tool calls went through the audited registry
    invocations = db.list_tool_invocations(limit=10)
    used = {inv.tool for inv in invocations}
    assert {"rag_search", "sensor_analysis"} <= used


async def test_dispatch_subagent_rejects_unknown_role(settings):
    db, registry, retriever, tools, _agent = _build_stack(settings)
    tool = tools.get("dispatch_subagent")

    from agentforge.tools.base import ToolContext

    ctx = ToolContext(session_id=None, workspace=settings.data_dir / "ws",
                      settings=settings, retriever=retriever)
    result = await tool.execute(
        {"specialists": [{"role": "hacker", "task": "do things"}]}, ctx
    )
    assert not result.ok and "unknown role" in result.error


async def test_hitl_approval_flow(settings):
    """auto_approve=False → tool waits; API decision resumes it."""
    db, registry, retriever, tools, agent = _build_stack(settings)
    tool = tools.get("create_work_order")

    from agentforge.tools.base import ToolContext

    ctx = ToolContext(session_id=None, workspace=settings.data_dir / "ws",
                      settings=settings, retriever=retriever, auto_approve=False)
    emitted: list[dict] = []

    async def emit(payload: dict) -> None:
        emitted.append(payload)

    ctx.emit = emit

    order_args = {
        "equipment_id": "AC-017", "title": "轴承更换", "fault_type": "bearing_outer_race_wear",
        "confidence": 0.87, "priority": "P2", "actions": ["停机", "换轴承"], "estimated_hours": 2,
    }

    async def decide_later():
        await asyncio.sleep(0.6)
        approval = db.list_approvals()[0]
        assert approval.status == "pending"
        db.decide_approval(approval.id, "approved", "chief_engineer")

    result, _ = await asyncio.gather(tool.execute(dict(order_args), ctx), decide_later())
    assert result.ok, result.error
    assert result.meta["approved_via"] != "auto"
    assert any(e["type"] == "approval_required" for e in emitted)
    # diagnosis memory was persisted together with the work order
    memories = db.list_memories(equipment_id="AC-017")
    assert len(memories) == 1 and "bearing_outer_race_wear" in memories[0].content


async def test_hitl_rejection_blocks_work_order(settings):
    db, registry, retriever, tools, _agent = _build_stack(settings)
    tool = tools.get("create_work_order")

    from agentforge.tools.base import ToolContext

    ctx = ToolContext(session_id=None, workspace=settings.data_dir / "ws",
                      settings=settings, retriever=retriever, auto_approve=False)

    order_args = {
        "equipment_id": "AC-017", "title": "轴承更换", "fault_type": "bearing_outer_race_wear",
        "confidence": 0.9, "priority": "P1", "actions": ["立即停机"],
    }

    async def reject_soon():
        await asyncio.sleep(0.5)
        approval = db.list_approvals()[0]
        db.decide_approval(approval.id, "rejected")

    result, _ = await asyncio.gather(tool.execute(dict(order_args), ctx), reject_soon())
    assert not result.ok and "拒绝" in result.error
    assert db.count_work_orders() == 0  # nothing created without approval


async def test_auto_approve_skips_gate(settings):
    db, registry, retriever, tools, _agent = _build_stack(settings)
    tool = tools.get("create_work_order")

    from agentforge.tools.base import ToolContext

    ctx = ToolContext(session_id=None, workspace=settings.data_dir / "ws",
                      settings=settings, retriever=retriever, auto_approve=True)
    result = await tool.execute(
        {"equipment_id": "WP-203", "title": "对中复查", "fault_type": "misalignment",
         "confidence": 0.8, "priority": "P2", "actions": ["激光对中"]},
        ctx,
    )
    assert result.ok and result.meta["approved_via"] == "auto"
    assert db.list_approvals() == []  # gate never engaged


def test_parse_critic_verdict():
    assert _parse_critic('{"pass": false, "issues": ["数值 4.66 与证据 4.68 不一致"]}') == {
        "pass": False, "issues": ["数值 4.66 与证据 4.68 不一致"],
    }
    assert _parse_critic('{"pass": true, "issues": []}')["pass"] is True
    assert _parse_critic("格式坏了")["pass"] is True  # malformed critic → fail-open
    assert _parse_critic('{"pass": true, "issues": ["有遗留问题"]}')["pass"] is False


async def test_memory_recalled_into_plan_execute(settings):
    db, registry, retriever, tools, agent = _build_stack(settings)
    db.add_memory(MemoryRow(
        equipment_id="AC-017", kind="diagnosis",
        content="AC-017 曾诊断为 bearing_outer_race_wear（历史工单 WO-000001）",
    ))
    session_id = db.create_session("mem").id
    events = []
    async for event in agent.run(
        session_id, "诊断 AC-017 振动报警", orchestrator="plan_execute"
    ):
        events.append(event)
    types = [e.type for e in events]
    assert "memory_recalled" in types
    recalled = next(e for e in events if e.type == "memory_recalled")
    assert any("WO-000001" in m for m in recalled.data["memories"])


async def test_waveform_endpoint(client):
    r = await client.get("/api/forgeops/equipment/AC-017/waveform?points=800")
    assert r.status_code == 200
    body = r.json()
    assert body["equipment_id"] == "AC-017"
    assert 0 < len(body["time_s"]) <= 800
    assert len(body["time_s"]) == len(body["vibration_mm_s"])
    assert body["rms_mm_s"] > 4.0  # AC-017 is in alarm
    assert body["iso10816_status"] == "alarm"

    r = await client.get("/api/forgeops/equipment/NOPE/waveform")
    assert r.status_code == 404
