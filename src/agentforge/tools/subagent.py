"""Multi-agent subagent dispatch (Claude Code style).

The orchestrator can fan out specialist sub-agents in a single turn. Each
sub-agent runs an ephemeral in-memory ReAct loop with a *restricted* toolset
and its own fresh context — no shared scratchpad, no chance of one specialist
polluting another's reasoning. Only the parent orchestrator persists
transcript rows; sub-agent tool calls still flow through the audited
ToolRegistry (session-scoped audit trail).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agentforge.agent.prompts import SUBAGENT_SYSTEM
from agentforge.config import Settings
from agentforge.llm.types import (
    ChatMessage,
    Finish,
    Routed,
    TextDelta,
    ToolCallDelta,
    ToolSpec,
    accumulate_tool_calls,
)
from agentforge.persistence.db import Database
from agentforge.tools.base import Tool, ToolContext, ToolResult
from agentforge.tools.registry import ToolRegistry

logger = logging.getLogger("agentforge.subagent")

SPECIALIST_ROLES: dict[str, dict[str, Any]] = {
    "knowledge_researcher": {
        "label": "知识研究员",
        "tools": ["rag_search"],
        "description": "检索设备手册、案例库与 SOP，输出事实性知识摘要",
    },
    "data_analyst": {
        "label": "数据分析师",
        "tools": ["python_repl", "sensor_analysis"],
        "description": "对传感器/数值数据做计算与频谱分析，输出量化结论",
    },
    "quality_auditor": {
        "label": "质量审核员",
        "tools": ["rag_search"],
        "description": "对照手册判据复核结论与数据的一致性",
    },
}

_MAX_SPECIALISTS = 3
_SUB_MAX_ITERATIONS = 4


class DispatchSubagentTool(Tool):
    name = "dispatch_subagent"
    description = (
        "并行派出专家子代理（多智能体协作）。每个子代理拥有独立上下文与受限工具集，"
        "并行执行后汇总各自报告。可一次派出多个角色：knowledge_researcher（知识研究员）、"
        "data_analyst（数据分析师）、quality_auditor（质量审核员）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "specialists": {
                "type": "array",
                "description": "要派出的子代理列表（1-3 个，将并行执行）",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {
                            "type": "string",
                            "enum": list(SPECIALIST_ROLES),
                            "description": "子代理角色",
                        },
                        "task": {"type": "string", "description": "给该子代理的完整自包含任务"},
                    },
                    "required": ["role", "task"],
                },
            },
        },
        "required": ["specialists"],
    }

    def __init__(
        self,
        registry: Any,  # ProviderRegistry
        tool_registry: ToolRegistry,
        settings: Settings,
        db: Database,
        *,
        timeout: float = 90.0,
    ) -> None:
        self._registry = registry
        self._tool_registry = tool_registry
        self._settings = settings
        self._db = db
        self.timeout = timeout

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        if error := _validate_specialists(args):
            return ToolResult(ok=False, output="", error=error)

        specialists = args["specialists"][:_MAX_SPECIALISTS]
        try:
            reports = await asyncio.wait_for(
                asyncio.gather(*(self._run_specialist(spec, ctx) for spec in specialists)),
                timeout=self.timeout,
            )
        except TimeoutError:
            return ToolResult(
                ok=False, output="",
                error=f"subagent dispatch timed out after {self.timeout}s",
                meta={"timeout": True},
            )

        provider_name, model_name = self._registry.resolve()
        output = "\n\n---\n\n".join(reports)
        return ToolResult(
            ok=True,
            output=output,
            meta={
                "specialists": [s["role"] for s in specialists],
                "parallel": len(specialists) > 1,
                "provider": provider_name,
                "model": model_name,
            },
        )

    async def _run_specialist(self, spec: dict, parent_ctx: ToolContext) -> str:
        """One ephemeral sub-agent: fresh context, restricted tools, small loop."""
        role = spec["role"]
        task = spec["task"]
        meta = SPECIALIST_ROLES[role]

        # Restricted toolset: whitelist per role, and never allow recursive
        # dispatch — a sub-agent cannot spawn further sub-agents.
        allowed = [t for t in meta["tools"] if t in self._tool_registry.tools]
        sub_tools = {name: self._tool_registry.tools[name] for name in allowed}
        sub_registry = ToolRegistry(sub_tools, self._settings, self._db)
        sub_ctx = ToolContext(
            session_id=parent_ctx.session_id,
            workspace=parent_ctx.workspace,
            settings=self._settings,
            retriever=parent_ctx.retriever,
            auto_approve=parent_ctx.auto_approve,
        )
        specs: list[ToolSpec] = sub_registry.specs()

        system_prompt = SUBAGENT_SYSTEM.format(role_name=meta["label"], task=task)
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=task),
        ]

        report = ""
        for _iteration in range(_SUB_MAX_ITERATIONS):
            text_parts: list[str] = []
            tool_deltas: list[ToolCallDelta] = []

            async for event in self._registry.stream(
                messages, specs, model=None, temperature=0.2
            ):
                match event:
                    case TextDelta(text=t):
                        text_parts.append(t)
                    case ToolCallDelta():
                        tool_deltas.append(event)
                    case Finish():
                        pass
                    case Routed():
                        pass

            tool_calls = accumulate_tool_calls(tool_deltas)
            report = "".join(text_parts)

            if not tool_calls:
                break

            messages.append(
                ChatMessage(role="assistant", content=report or None, tool_calls=tool_calls)
            )
            for call in tool_calls:
                result = await sub_registry.run(call, sub_ctx)
                messages.append(
                    ChatMessage(
                        role="tool", content=result.to_llm_payload(),
                        tool_call_id=call.id, name=call.name,
                    )
                )
        else:
            logger.warning("subagent %s hit iteration cap", role)

        header = f"### 【{meta['label']} · {role}】"
        return f"{header}\n{report or '（子代理未产出报告）'}"


def _validate_specialists(args: dict) -> str | None:
    if not isinstance(args, dict) or not isinstance(args.get("specialists"), list):
        return "arguments must include a 'specialists' array"
    if not args["specialists"]:
        return "specialists must not be empty"
    for spec in args["specialists"]:
        if not isinstance(spec, dict):
            return "each specialist must be an object"
        if spec.get("role") not in SPECIALIST_ROLES:
            return f"unknown role: {spec.get('role')!r} (known: {list(SPECIALIST_ROLES)})"
        if not str(spec.get("task", "")).strip():
            return "each specialist needs a non-empty task"
    return None
