"""LLM gateway: token estimation, tool-call accumulation, mock routing, failover."""

import json

import pytest

from agentforge.llm.mock import MockLLM, _extract_arithmetic
from agentforge.llm.types import (
    ChatMessage,
    ToolCallDelta,
    ToolSpec,
    accumulate_tool_calls,
    estimate_tokens,
)


def test_estimate_tokens_cjk_vs_ascii():
    assert estimate_tokens("Hello world, this is fine") > 5
    assert estimate_tokens("工业级智能体平台") >= 7  # ~1 token per CJK char
    cjk = estimate_tokens("设备振动诊断")
    latin = estimate_tokens("device vibration diagnosis")
    assert cjk < latin  # CJK packs tighter


def test_accumulate_tool_calls_merges_fragments():
    deltas = [
        ToolCallDelta(index=0, id="c1", name="python_repl", arguments_delta='{"code": "pr'),
        ToolCallDelta(index=0, arguments_delta='int(1+1)"}'),
        ToolCallDelta(index=1, id="c2", name="rag_search", arguments_delta='{"query": "x"}'),
    ]
    calls = accumulate_tool_calls(deltas)
    assert [c.name for c in calls] == ["python_repl", "rag_search"]
    assert calls[0].arguments == {"code": "print(1+1)"}
    assert calls[1].arguments == {"query": "x"}


def test_accumulate_tool_calls_invalid_json_survives():
    calls = accumulate_tool_calls([ToolCallDelta(index=0, id="c1", name="t", arguments_delta="{oops")])
    assert calls[0].arguments.get("_parse_error") is True


def test_extract_arithmetic_safe():
    assert _extract_arithmetic("帮我计算 128*365+42 谢谢") == "128*365+42"
    assert _extract_arithmetic("(1+2)*3") == "(1+2)*3"
    assert _extract_arithmetic("没有算式") is None
    # injection attempts are rejected by the AST whitelist
    assert _extract_arithmetic("__import__('os')") is None


async def test_mock_routes_arithmetic_to_python_repl():
    mock = MockLLM(latency=0)
    tools = [ToolSpec(name="python_repl", description="", parameters={})]
    events = []
    async for ev in mock.stream(
        [ChatMessage(role="user", content="计算 12*34")], tools
    ):
        events.append(ev)
    from agentforge.llm.types import Finish, ToolCallDelta

    deltas = [e for e in events if isinstance(e, ToolCallDelta)]
    finishes = [e for e in events if isinstance(e, Finish)]
    assert len(deltas) == 1 and deltas[0].name == "python_repl"
    assert json.loads(deltas[0].arguments_delta)["code"] == "print(12*34)"
    assert finishes[-1].finish_reason == "tool_calls"


async def test_mock_planner_and_synthesizer():
    mock = MockLLM(latency=0)
    plan_events = []
    async for ev in mock.stream([ChatMessage(role="system", content="你是任务规划器（Planner）…"),
                                 ChatMessage(role="user", content="诊断设备")]):
        plan_events.append(ev)
    plan_text = "".join(e.text for e in plan_events if hasattr(e, "text"))
    plan = json.loads(plan_text)
    assert len(plan["steps"]) == 3
    assert plan["steps"][0]["id"] == "s1"

    synth_events = []
    async for ev in mock.stream([ChatMessage(role="system", content="你是汇总者（Synthesizer）"),
                                 ChatMessage(role="user", content="步骤结果: s1 检索… 214 Hz 置信度 0.87")]):
        synth_events.append(ev)
    final = "".join(e.text for e in synth_events if hasattr(e, "text"))
    assert "轴承外圈磨损" in final and "214" in final


async def test_registry_failover_on_connect_error():
    """Primary provider unreachable → stream falls over to the mock provider."""
    from agentforge.config import ProviderSpec
    from agentforge.llm.registry import ProviderRegistry

    dead = ProviderSpec(
        name="dead", type="openai", base_url="http://127.0.0.1:1/v1",
        api_key="x", model="m",
    )
    registry = ProviderRegistry([dead, ProviderSpec(name="mock", type="mock", model="default")],
                                "dead/m", fallback_chain=["mock"], max_retries=0)
    events = []
    provider_seen = None
    async for ev in registry.stream([ChatMessage(role="user", content="你好")]):
        from agentforge.llm.types import Routed

        if isinstance(ev, Routed):
            provider_seen = ev.provider
        events.append(ev)
    assert provider_seen == "mock"
    assert any(hasattr(e, "text") for e in events)


async def test_registry_no_failover_after_partial_output():
    """Failover must not duplicate output once streaming has begun."""
    from agentforge.config import ProviderSpec
    from agentforge.llm.base import BaseLLM
    from agentforge.llm.registry import ProviderRegistry
    from agentforge.llm.types import ProviderError, TextDelta

    class HalfBrokenLLM(BaseLLM):
        name = "half"
        default_model = "m"

        async def stream(self, messages, tools=(), *, model=None, temperature=None, max_tokens=None):
            yield TextDelta("partial ")
            raise ProviderError("mid-stream failure", provider=self.name, retryable=True)

    registry = ProviderRegistry(
        [ProviderSpec(name="mock", type="mock", model="default")], "mock/default",
        fallback_chain=["mock"], max_retries=0,
    )
    registry._providers["half"] = HalfBrokenLLM()
    registry.default_model = "half/m"

    events = []
    with pytest.raises(ProviderError):
        async for ev in registry.stream([ChatMessage(role="user", content="hi")]):
            events.append(ev)
    texts = [e.text for e in events if isinstance(e, TextDelta)]
    assert texts == ["partial "]  # exactly one partial yield, no duplicated fallback
