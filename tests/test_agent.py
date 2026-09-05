"""Agent runtime: context window, ReAct loop, plan-and-execute, plan parsing."""

from agentforge.agent.context import build_llm_messages
from agentforge.agent.core import parse_plan
from agentforge.llm.types import ChatMessage


def test_parse_plan_valid_and_garbage():
    plan = parse_plan('废话 {"thought":"t","steps":[{"id":"s1","title":"a","instruction":"do x"}],"success_criteria":"y"} 后缀')
    assert plan and plan["steps"][0]["instruction"] == "do x"

    assert parse_plan("no json here") is None
    assert parse_plan('{"steps": []}') is None
    assert parse_plan('{"steps": [{"id": "s1"}]}') is None  # missing instruction


def test_context_window_trims_oldest_and_keeps_pairing():
    messages = [
        ChatMessage(role="system", content="S"),
        ChatMessage(role="user", content="问题一 " + "x" * 500),
        ChatMessage(role="assistant", content="回答一 " + "y" * 500),
        ChatMessage(role="assistant", content="调用工具", tool_calls=[]),
        ChatMessage(role="tool", content="工具结果"),
        ChatMessage(role="user", content="最新问题"),
    ]
    # tiny budget: everything except system + latest user must be trimmed
    fitted = build_llm_messages("S", messages, budget_tokens=80)
    roles = [m.role for m in fitted]
    assert roles[0] == "system"
    assert fitted[-1].content == "最新问题"
    # orphan tool results must never lead the trimmed history
    assert "tool" not in roles[1:-1] or roles[1] != "tool"


async def test_react_loop_with_tool_call(settings):
    """Full ReAct: arithmetic → python_repl → final answer with the result."""

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
    tools = build_default_registry(settings, db, retriever)
    agent = Agent(db, registry, tools, settings, retriever=retriever)

    session_id = db.create_session("t").id
    events = []
    async for event in agent.run(session_id, "帮我计算 12*34"):
        events.append(event)

    types = [e.type for e in events]
    assert "tool_start" in types and "tool_end" in types
    tool_end = next(e for e in events if e.type == "tool_end")
    assert tool_end.data["name"] == "python_repl"
    assert "408" in tool_end.data["output"]

    final = next(e for e in events if e.type == "assistant_message")
    assert "408" in final.data["message"]["content"]

    # persistence audit: user + assistant + tool rows exist
    rows = db.list_messages(session_id)
    assert [r.role for r in rows] == ["user", "assistant", "tool", "assistant"]


async def test_plan_execute_flow(settings):
    """Planner → 3 steps (rag / sensor / work order) → synthesizer → trace-ready log."""

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
    tools = build_default_registry(settings, db, retriever)
    agent = Agent(db, registry, tools, settings, retriever=retriever)

    session_id = db.create_session("t2").id
    events = []
    async for event in agent.run(
        session_id, "诊断 AC-017：振动报警并异响，给出结论并生成工单", orchestrator="plan_execute"
    ):
        events.append(event)

    types = [e.type for e in events]
    assert "plan_created" in types
    assert types.count("step_started") == 3
    assert types.count("step_completed") == 3
    tool_names = [e.data["name"] for e in events if e.type == "tool_end"]
    assert tool_names == ["rag_search", "sensor_analysis", "create_work_order"]

    # work order persisted exactly once (no orchestration-loop duplication)
    assert db.count_work_orders() == 1

    # the persisted log supports trace reconstruction
    rows = db.list_messages(session_id)
    kinds = [(r.meta or {}).get("kind") for r in rows]
    assert "plan" in kinds and "step_instruction" in kinds and "final" in kinds
