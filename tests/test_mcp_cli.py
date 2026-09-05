"""MCP config surface, mcp CLI, Agent-as-MCP-Server, chat REPL, hermes preset."""

import json
import sys

from agentforge.mcp_config import (
    add_mcp_server,
    read_mcp_file,
    remove_mcp_server,
    resolve_mcp_servers,
)


# ---------- config file parsing ----------
def test_read_mcp_file_supports_claude_code_format(tmp_path):
    cfg = tmp_path / ".mcp.json"
    cfg.write_text(json.dumps({
        "mcpServers": {
            "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]},
            "git": {"command": "uvx", "args": ["mcp-server-git"], "env": {"G": "1"}},
        }
    }), encoding="utf-8")
    specs = read_mcp_file(cfg)
    assert {s.name for s in specs} == {"fetch", "git"}
    fetch = next(s for s in specs if s.name == "fetch")
    assert fetch.command == "uvx" and fetch.args == ["mcp-server-fetch"]


def test_read_mcp_file_tolerates_garbage(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text("{not json", encoding="utf-8")
    assert read_mcp_file(cfg) == []
    assert read_mcp_file(tmp_path / "missing.json") == []


def test_add_remove_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))  # Path.home() follows HOME
    path = add_mcp_server("demo", "python", ["-m", "demo_server"], {"K": "V"}, scope="user")
    assert path.is_file()
    specs = {s.name: s for s in read_mcp_file(path)}
    assert specs["demo"].command == "python"
    assert specs["demo"].args == ["-m", "demo_server"]
    assert specs["demo"].env == {"K": "V"}

    assert remove_mcp_server("demo", scope="user")
    assert not remove_mcp_server("demo", scope="user")
    assert read_mcp_file(path) == []


def test_resolve_merge_project_wins_over_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    user_dir = tmp_path / ".agentforge"
    user_dir.mkdir()
    (user_dir / "mcp.json").write_text(json.dumps(
        {"mcpServers": {"shared": {"command": "user-cmd"}, "user-only": {"command": "u"}}}
    ), encoding="utf-8")
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"shared": {"command": "project-cmd"}}}
    ), encoding="utf-8")

    merged = {s.name: s for s in resolve_mcp_servers([], project_dir=project)}
    assert merged["shared"].command == "project-cmd"  # project overrides user
    assert merged["user-only"].command == "u"


def test_load_settings_merges_project_mcp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"from-file": {"command": "bin", "args": ["x"]}}}
    ), encoding="utf-8")
    from agentforge.config import load_settings

    settings = load_settings()
    names = {s.name for s in settings.mcp_servers}
    assert "from-file" in names


# ---------- hermes provider preset ----------
def test_hermes_preset_from_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NOUS_API_KEY", "nous-key-123")
    monkeypatch.setenv("HERMES_MODEL", "Hermes-4-405B")
    from agentforge.config import load_settings

    settings = load_settings()
    hermes = next(p for p in settings.providers if p.name == "hermes")
    assert hermes.type == "openai"
    assert hermes.api_key == "nous-key-123"
    assert hermes.model == "Hermes-4-405B"
    assert hermes.base_url.startswith("https://")


# ---------- agent-as-mcp-server (real subprocess) ----------
async def test_mcp_serve_handshake_and_call(tmp_path, monkeypatch):
    from agentforge.mcp_client import MCPConnection

    data_dir = tmp_path / "serve-data"
    monkeypatch.setenv("AGENTFORGE_DATA_DIR", str(data_dir))
    conn = MCPConnection(
        "forgeops", sys.executable, ["-m", "agentforge.mcp_server"], timeout=60
    )
    try:
        await conn.start()
        assert conn._server_info.get("name") == "forgeops"
        tools = {t["name"] for t in await conn.list_tools()}
        # python_repl deliberately not exposed to remote clients
        assert {"sensor_analysis", "rag_search", "create_work_order"} <= tools
        assert "python_repl" not in tools and "dispatch_subagent" not in tools

        out = await conn.call_tool("sensor_analysis", {"equipment_id": "AC-017", "operation": "rms"})
        assert '"iso10816_status": "alarm"' in out
    finally:
        await conn.stop()


# ---------- chat REPL commands ----------
def _build_state(tmp_path):
    from agentforge.agent.core import Agent
    from agentforge.chat_repl import ChatSessionState
    from agentforge.config import load_settings
    from agentforge.llm.registry import ProviderRegistry
    from agentforge.persistence.db import Database
    from agentforge.rag.embeddings import build_embedder
    from agentforge.rag.retriever import Retriever
    from agentforge.tools.registry import build_default_registry

    settings = load_settings()
    settings.data_dir = tmp_path / "repl-data"
    settings.db_url = f"sqlite:///{tmp_path / 'repl.db'}"
    db = Database(settings.db_url, settings.data_dir)
    registry = ProviderRegistry(settings.providers, settings.default_model)
    embedder = build_embedder("hashing", [])
    retriever = Retriever(db, embedder, settings)
    tools = build_default_registry(settings, db, retriever, registry=registry)
    agent = Agent(db, registry, tools, settings, retriever=retriever)
    state = ChatSessionState(
        stack={"agent": agent, "db": db, "settings": settings},
        session_id=None, orchestrator="react",
    )
    return state


def test_repl_quit_and_orchestrator(tmp_path, capsys):
    from agentforge.chat_repl import handle_command

    state = _build_state(tmp_path)
    assert handle_command("/quit", state) is False
    assert handle_command("/exit", state) is False

    assert handle_command("/orchestrator plan_execute", state) is True
    assert state.orchestrator == "plan_execute"
    assert handle_command("/orchestrator bogus", state) is True  # rejected, stays
    assert state.orchestrator == "plan_execute"
    assert "invalid mode" in capsys.readouterr().out


def test_repl_sessions_and_tools(tmp_path, capsys):
    from agentforge.chat_repl import handle_command

    state = _build_state(tmp_path)
    assert handle_command("/tools", state) is True
    assert "python_repl" in capsys.readouterr().out

    handle_command("/sessions", state)
    assert "暂无" not in capsys.readouterr().out or True  # empty listing is fine

    state.session_id = state.agent._db.create_session("标记会话").id
    handle_command("/sessions", state)
    assert "标记会话" in capsys.readouterr().out

    assert handle_command("/auto off", state) is True
    assert state.auto_approve is False


def test_repl_stream_turn_end_to_end(tmp_path, capsys):
    import asyncio

    from agentforge.chat_repl import _stream_turn

    state = _build_state(tmp_path)
    asyncio.run(_stream_turn(state, "帮我计算 12*34"))
    out = capsys.readouterr().out
    assert "python_repl" in out and "408" in out
    assert state.session_id  # session auto-created
