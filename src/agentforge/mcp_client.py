"""MCP (Model Context Protocol) client — stdio transport, JSON-RPC 2.0.

Attaches external tool servers to the platform: each configured MCP server is
launched as a subprocess, its ``tools/list`` is registered into the tool
registry (namespaced ``mcp__<server>__<tool>``), and tool calls are forwarded
over ``tools/call``. Implements the core MCP handshake (initialize →
notifications/initialized → tools/list → tools/call) over newline-delimited
JSON-RPC, which is the standard stdio transport framing.

A failing MCP server never takes the platform down: attach errors are logged
and skipped — degraded capability instead of outage.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from agentforge.config import MCPServerSpec
from agentforge.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("agentforge.mcp")

PROTOCOL_VERSION = "2024-11-05"


class MCPError(Exception):
    pass


class MCPConnection:
    """One MCP server subprocess with request/response correlation."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        *,
        timeout: float = 15.0,
    ) -> None:
        self.name = name
        self._command = [command, *(args or [])]
        self._env_extra = env or {}
        self._timeout = timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._server_info: dict = {}

    async def start(self) -> None:
        import os

        env = {**os.environ, **self._env_extra}
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except (OSError, NotImplementedError) as exc:
            raise MCPError(f"failed to launch MCP server {self.name!r}: {exc}") from exc

        result = await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "agentforge", "version": "0.1.0"},
            },
        )
        self._server_info = result.get("serverInfo", {}) if isinstance(result, dict) else {}
        await self._notify("notifications/initialized")
        logger.info("MCP server %s connected: %s", self.name, self._server_info.get("name", "?"))

    async def _send(self, payload: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPError(f"MCP server {self.name!r} is not running")
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        self._proc.stdin.write(line.encode())
        await self._proc.stdin.drain()

    async def _read_message(self) -> dict:
        if self._proc is None or self._proc.stdout is None:
            raise MCPError(f"MCP server {self.name!r} is not running")
        while True:
            line = await asyncio.wait_for(self._proc.stdout.readline(), self._timeout)
            if not line:
                raise MCPError(f"MCP server {self.name!r} closed the stream unexpectedly")
            text = line.decode().strip()
            if not text:
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue

    async def _request(self, method: str, params: dict | None = None) -> Any:
        self._next_id += 1
        request_id = self._next_id
        await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        while True:
            message = await self._read_message()
            if message.get("id") != request_id:
                continue  # ignore notifications / foreign ids
            if "error" in message:
                err = message["error"]
                raise MCPError(f"MCP {method} error: {err.get('message', err)}")
            return message.get("result")

    async def _notify(self, method: str, params: dict | None = None) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._request("tools/list")
        return result.get("tools", []) if isinstance(result, dict) else []

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        result = await self._request("tools/call", {"name": tool_name, "arguments": arguments})
        if not isinstance(result, dict):
            raise MCPError("MCP tools/call returned non-object result")
        if result.get("isError"):
            texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
            raise MCPError("; ".join(t for t in texts if t) or "tool reported an error")
        parts = []
        for content in result.get("content", []):
            if content.get("type") == "text":
                parts.append(content.get("text", ""))
        return "\n".join(parts) or json.dumps(result, ensure_ascii=False)

    async def stop(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            await asyncio.wait_for(self._proc.wait(), 5)
        except (TimeoutError, ProcessLookupError):
            self._proc.kill()
            await self._proc.wait()
        self._proc = None


class MCPToolProxy(Tool):
    """Adapts a remote MCP tool to the local Tool contract."""

    def __init__(self, connection: MCPConnection, spec: dict[str, Any], *, timeout: float = 30.0) -> None:
        remote = spec["name"]
        self.connection = connection
        self._remote_name = remote
        self.name = f"mcp__{connection.name}__{remote}"
        self.description = (
            f"[MCP:{connection.name}] {spec.get('description') or remote}"
        )
        self.parameters = spec.get("inputSchema") or {"type": "object", "properties": {}, "required": []}
        self.timeout = timeout

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            output = await self.connection.call_tool(self._remote_name, args)
        except MCPError as exc:
            return ToolResult(ok=False, output="", error=str(exc))
        return ToolResult(ok=True, output=output, meta={"via": "mcp", "server": self.connection.name})


async def attach_mcp_servers(
    tools: dict[str, Tool], specs: list[MCPServerSpec]
) -> list[MCPConnection]:
    """Launch configured MCP servers and register their tools into *tools*.

    Returns the live connections (for shutdown). Failures are logged and
    skipped: one broken integration must not degrade the whole platform.
    """
    connections: list[MCPConnection] = []
    for spec in specs:
        if not spec.enabled:
            continue
        connection = MCPConnection(spec.name, spec.command, spec.args, spec.env)
        try:
            await connection.start()
            remote_tools = await connection.list_tools()
        except (TimeoutError, MCPError) as exc:
            logger.warning("MCP server %s unavailable, skipping: %s", spec.name, exc)
            await connection.stop()
            continue
        for tool_spec in remote_tools:
            try:
                proxy = MCPToolProxy(connection, tool_spec)
                tools[proxy.name] = proxy
            except Exception:  # noqa: BLE001 - bad tool spec on a remote server
                logger.exception("skipping malformed MCP tool from %s", spec.name)
        connections.append(connection)
        logger.info("MCP server %s attached %d tools", spec.name, len(remote_tools))
    return connections


__all__ = ["MCPConnection", "MCPError", "MCPToolProxy", "attach_mcp_servers"]
