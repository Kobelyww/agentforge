"""Tool registry: discovery, validation, execution, audit, metrics."""

from __future__ import annotations

import asyncio
import logging
import time

from agentforge.config import Settings
from agentforge.llm.types import ToolCall, ToolSpec
from agentforge.observability.metrics import TOOL_INVOCATIONS, TOOL_LATENCY
from agentforge.persistence.db import Database
from agentforge.persistence.models import ToolInvocation
from agentforge.tools.base import Tool, ToolContext, ToolResult, validate_args

logger = logging.getLogger("agentforge.tools")


class ToolRegistry:
    def __init__(self, tools: dict[str, Tool], settings: Settings, db: Database) -> None:
        self._tools = tools
        self._settings = settings
        self._db = db

    @property
    def tools(self) -> dict[str, Tool]:
        """Mutable view — used by the MCP integration to attach remote tools."""
        return self._tools

    # ---- discovery ----
    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(name=t.name, description=t.description, parameters=t.parameters)
            for t in self._tools.values()
        ]

    # ---- execution ----
    async def run(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        """Execute a tool call defensively: never raises, always audited."""
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(ok=False, output="", error=f"unknown tool: {call.name}")

        if error := validate_args(call.arguments, tool.parameters):
            return ToolResult(ok=False, output="", error=f"invalid arguments: {error}")

        started = time.perf_counter()
        status = "ok"
        try:
            result = await asyncio.wait_for(tool.execute(call.arguments, ctx), tool.timeout)
        except TimeoutError:
            status = "timeout"
            result = ToolResult(ok=False, output="", error=f"tool {call.name} timed out after {tool.timeout}s")
        except Exception as exc:  # noqa: BLE001 - boundary: report, don't crash the loop
            status = "error"
            logger.exception("tool %s crashed", call.name)
            result = ToolResult(ok=False, output="", error=f"{type(exc).__name__}: {exc}")

        latency_ms = (time.perf_counter() - started) * 1000
        result.meta["latency_ms"] = round(latency_ms, 1)
        TOOL_INVOCATIONS.labels(tool=call.name, status=status).inc()
        TOOL_LATENCY.labels(tool=call.name).observe(latency_ms / 1000)

        await asyncio.to_thread(
            self._db.add_tool_invocation,
            ToolInvocation(
                session_id=ctx.session_id,
                tool=call.name,
                args=call.arguments,
                result={"output": result.output[:2000], "meta": result.meta},
                status=status,
                latency_ms=latency_ms,
                error=result.error,
            ),
        )
        return result


def build_default_registry(settings: Settings, db: Database, retriever=None, registry=None) -> ToolRegistry:
    """Instantiate the tools enabled in settings.

    ``registry`` (the provider registry) is required for the multi-agent
    ``dispatch_subagent`` tool; without it that tool is skipped.
    """
    from agentforge.tools.python_repl import PythonREPLTool
    from agentforge.tools.rag_search import RagSearchTool
    from agentforge.tools.web_fetch import WebFetchTool

    candidates: dict[str, Tool] = {}

    limits = settings.tool_limits("python_repl")
    candidates["python_repl"] = PythonREPLTool(
        timeout=limits.timeout_seconds,
        memory_limit_mb=limits.memory_limit_mb,
        cpu_limit_seconds=limits.cpu_limit_seconds,
    )

    limits = settings.tool_limits("web_fetch")
    candidates["web_fetch"] = WebFetchTool(
        timeout=limits.timeout_seconds,
        max_bytes=limits.max_bytes,
        allowed_domains=limits.allowed_domains,
    )

    limits = settings.tool_limits("rag_search")
    candidates["rag_search"] = RagSearchTool(
        top_k=settings.rag.top_k, timeout=limits.timeout_seconds
    )

    # ForgeOps vertical tools (sensor analysis + structured work orders).
    try:
        from agentforge.forgeops.tools import CreateWorkOrderTool, SensorAnalysisTool

        limits = settings.tool_limits("sensor_analysis")
        candidates["sensor_analysis"] = SensorAnalysisTool(timeout=limits.timeout_seconds)
        limits = settings.tool_limits("create_work_order")
        candidates["create_work_order"] = CreateWorkOrderTool(db, timeout=limits.timeout_seconds)
    except ImportError:  # pragma: no cover
        pass

    # Multi-agent dispatch (needs the provider registry to run sub-agents).
    # Attached after the full registry exists so it can whitelist from it;
    # the sub-agent loop itself excludes dispatch_subagent (no recursion).
    enabled = settings.agent.enabled_tools or list(candidates)
    tools = {name: tool for name, tool in candidates.items() if name in enabled}
    tool_registry = ToolRegistry(tools, settings, db)
    # dispatch_subagent lives outside `candidates` (it needs the finished
    # registry to whitelist from), so its enablement is checked separately.
    if registry is not None and "dispatch_subagent" in (settings.agent.enabled_tools or ["dispatch_subagent"]):
        from agentforge.tools.subagent import DispatchSubagentTool

        limits = settings.tool_limits("dispatch_subagent")
        tool_registry.tools["dispatch_subagent"] = DispatchSubagentTool(
            registry, tool_registry, settings, db,
            timeout=limits.timeout_seconds,
        )
    return tool_registry
