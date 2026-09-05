"""MCP client against the bundled example server (real subprocess IPC)."""

import sys

import pytest

from agentforge.mcp_client import MCPConnection, MCPError, MCPToolProxy


@pytest.fixture()
async def connection():
    conn = MCPConnection(
        "calc", sys.executable, ["examples/mcp_servers/maintenance_calculator.py"]
    )
    await conn.start()
    yield conn
    await conn.stop()


async def test_handshake_and_list_tools(connection):
    assert connection._server_info.get("name") == "maintenance-calculator"
    tools = await connection.list_tools()
    names = {t["name"] for t in tools}
    assert {"bearing_fault_frequencies", "unit_convert"} <= names


async def test_call_tool_and_error_path(connection):
    output = await connection.call_tool("bearing_fault_frequencies", {"rpm": 2960})
    assert '"bpfo_hz"' in output
    assert "176.85" in output  # matches the manual: BPFO 3.59 × 49.33 Hz (6205 @ 2960 rpm)

    with pytest.raises(MCPError):
        await connection.call_tool("bearing_fault_frequencies", {"rpm": "not-a-number"})


async def test_tool_proxy_registration(connection):
    spec = {"name": "unit_convert", "description": "unit conversion",
            "inputSchema": {"type": "object", "properties": {}, "required": []}}
    proxy = MCPToolProxy(connection, spec)
    assert proxy.name == "mcp__calc__unit_convert"
    result = await proxy.execute({"value": 5, "from_unit": "C", "to_unit": "F"}, None)
    assert result.ok and "41" in result.output


async def test_attach_skips_broken_server(settings, tmp_path):
    from agentforge.config import MCPServerSpec
    from agentforge.mcp_client import attach_mcp_servers

    tools: dict = {}
    connections = await attach_mcp_servers(
        tools,
        [
            MCPServerSpec(name="broken", command="/nonexistent-binary-xyz"),
            MCPServerSpec(name="good", command=sys.executable,
                          args=["examples/mcp_servers/maintenance_calculator.py"]),
        ],
    )
    assert len(connections) == 1  # broken server skipped, platform survives
    assert any(t.startswith("mcp__good__") for t in tools)
    assert not any(t.startswith("mcp__broken__") for t in tools)
    for c in connections:
        await c.stop()
