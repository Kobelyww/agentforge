"""MCP server configuration: multi-source merge + CRUD.

Sources (later wins on name conflict):
1. ``config.yaml`` ``mcp_servers:`` block
2. User-level ``~/.agentforge/mcp.json``
3. Project-level ``./.mcp.json`` — Claude Code compatible format::

       {"mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}}

This is the OpenClaw/Claude-Code-style config surface: drop a JSON file next
to your project and the platform picks the servers up without touching YAML.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agentforge.config import MCPServerSpec

logger = logging.getLogger("agentforge.mcp")

def user_mcp_path() -> Path:
    return Path.home() / ".agentforge" / "mcp.json"


def project_mcp_path(project_dir: Path | None = None) -> Path:
    return (project_dir or Path.cwd()) / ".mcp.json"


def read_mcp_file(path: Path) -> list[MCPServerSpec]:
    """Parse one MCP config file; tolerates both mapping and list formats."""
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("skipping unreadable MCP config %s: %s", path, exc)
        return []

    servers: list[MCPServerSpec] = []
    if isinstance(raw, dict) and isinstance(raw.get("mcpServers"), dict):
        # Claude Code / OpenClaw compatible mapping format
        for name, spec in raw["mcpServers"].items():
            if isinstance(spec, dict):
                servers.append(
                    MCPServerSpec(name=name, command=spec.get("command", ""),
                                  args=spec.get("args") or [], env=spec.get("env") or {},
                                  enabled=spec.get("enabled", True))
                )
    elif isinstance(raw, list):
        for spec in raw:
            if isinstance(spec, dict) and spec.get("name"):
                servers.append(MCPServerSpec(**{k: v for k, v in spec.items() if k in MCPServerSpec.model_fields}))
    elif isinstance(raw, dict):
        for name, spec in raw.items():
            if isinstance(spec, dict) and spec.get("command"):
                servers.append(
                    MCPServerSpec(name=name, command=spec["command"],
                                  args=spec.get("args") or [], env=spec.get("env") or {})
                )
    return servers


def resolve_mcp_servers(base: list[MCPServerSpec], *, project_dir: Path | None = None) -> list[MCPServerSpec]:
    """Merge config.yaml + user file + project file into one server list."""
    merged: dict[str, MCPServerSpec] = {s.name: s for s in base}
    for source in (user_mcp_path(), project_mcp_path(project_dir)):
        for spec in read_mcp_file(source):
            merged[spec.name] = spec
    return list(merged.values())


def add_mcp_server(name: str, command: str, args: list[str] | None = None,
                   env: dict[str, str] | None = None, *, scope: str = "user") -> Path:
    """Persist a server entry into the user or project MCP config file."""
    path = user_mcp_path() if scope == "user" else project_mcp_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw: dict = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = {}
    servers = raw.get("mcpServers", {}) if isinstance(raw, dict) else {}
    servers[name] = {"command": command, "args": args or [], "env": env or {}}
    raw["mcpServers"] = servers
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def remove_mcp_server(name: str, *, scope: str = "user") -> bool:
    path = user_mcp_path() if scope == "user" else project_mcp_path()
    if not path.is_file():
        return False
    raw = json.loads(path.read_text(encoding="utf-8"))
    servers = raw.get("mcpServers", {}) if isinstance(raw, dict) else {}
    if name not in servers:
        return False
    del servers[name]
    raw["mcpServers"] = servers
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True
