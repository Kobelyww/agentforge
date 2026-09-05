#!/usr/bin/env python3
"""``agentforge mcp serve`` — expose ForgeOps capabilities as an MCP server.

Agent-as-MCP-Server: any MCP client (Claude Desktop, Cursor, our own
agentforge chat) can mount this process and call the platform's vertical
tools directly. Runs the same stdio JSON-RPC transport as the bundled
example server; tools are served through the audited ToolRegistry.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

PROTOCOL_VERSION = "2024-11-05"

# Tools exposed to external MCP clients. python_repl is intentionally NOT
# exposed: a remote client executing code on this host widens the attack
# surface beyond what the process sandbox is meant to guard.
EXPOSED_TOOLS = ["sensor_analysis", "rag_search", "create_work_order"]

_stack: dict[str, Any] = {}


def get_stack() -> dict[str, Any]:
    """Build the engine stack lazily so `tools/list` costs nothing at boot."""
    if not _stack:
        from agentforge.config import load_settings
        from agentforge.llm.registry import ProviderRegistry
        from agentforge.persistence.db import Database
        from agentforge.rag.embeddings import build_embedder
        from agentforge.rag.retriever import Retriever
        from agentforge.tools.registry import build_default_registry

        settings = load_settings()
        db = Database(settings.db_url, settings.data_dir)
        registry = ProviderRegistry(settings.providers, settings.default_model)
        embedder = build_embedder(settings.rag.embedder, [registry.get(n) for n in registry.names()])
        retriever = Retriever(db, embedder, settings)
        tools = build_default_registry(settings, db, retriever, registry=registry)
        _stack.update(settings=settings, db=db, retriever=retriever, tools=tools)
    return _stack


def mcp_tool_definitions(stack: dict[str, Any]) -> list[dict[str, Any]]:
    defs = []
    for spec in stack["tools"].specs():
        if spec.name not in EXPOSED_TOOLS:
            continue
        defs.append({
            "name": spec.name,
            "description": spec.description,
            "inputSchema": spec.parameters,
        })
    return defs


async def dispatch_call(name: str, arguments: dict) -> dict:
    from agentforge.llm.types import ToolCall
    from agentforge.tools.base import ToolContext

    stack = get_stack()
    tool = stack["tools"].get(name)
    if tool is None:
        raise ValueError(f"unknown tool: {name}")
    ctx = ToolContext(
        session_id=None,
        workspace=stack["settings"].data_dir / "workspace" / "mcp-server",
        settings=stack["settings"],
        retriever=stack["retriever"],
        auto_approve=True,  # headless integration: no human attached
    )
    result = await stack["tools"].run(ToolCall(id=f"mcp-{name}", name=name, arguments=arguments), ctx)
    if not result.ok:
        raise ValueError(result.error or "tool execution failed")
    return {"output": result.output, "meta": result.meta}


def handle(msg: dict) -> dict | None:
    method = msg.get("method", "")
    request_id = msg.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "forgeops", "version": "0.1.0"},
            },
        }
    if method.startswith("notifications/"):
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id,
                "result": {"tools": mcp_tool_definitions(get_stack())}}
    if method == "tools/call":
        params = msg.get("params", {})
        try:
            data = asyncio.run(dispatch_call(params.get("name", ""), params.get("arguments") or {}))
            return {
                "jsonrpc": "2.0", "id": request_id,
                "result": {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                           "isError": False},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0", "id": request_id,
                "result": {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            }
    if request_id is not None:
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


def main() -> int:
    # stdout is the protocol channel — keep library noise away from it
    import logging

    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        response = handle(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
