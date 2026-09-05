#!/usr/bin/env python3
"""Example MCP (Model Context Protocol) server: maintenance calculator.

A standalone, dependency-free MCP server over the stdio transport
(newline-delimited JSON-RPC 2.0). Implements:

- initialize / notifications/initialized handshake
- tools/list  → two domain tools
- tools/call  → bearing fault frequency calculation, unit conversion

Wire it into AgentForge via config.yaml:

    mcp_servers:
      - name: maintenance_calc
        command: python
        args: [examples/mcp_servers/maintenance_calculator.py]

Any MCP-compatible client (Claude Desktop, Cursor, …) can attach this server.
"""

from __future__ import annotations

import json
import math
import sys

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "bearing_fault_frequencies",
        "description": "计算滚动轴承故障特征频率（BPFO/BPFI/BSF/FTF）。输入转速 rpm 与轴承几何参数。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rpm": {"type": "number", "description": "轴转速 (r/min)"},
                "balls": {"type": "integer", "description": "滚动体数量，6205 为 9"},
                "ball_diameter_mm": {"type": "number", "description": "滚动体直径 mm，6205 为 7.94"},
                "pitch_diameter_mm": {"type": "number", "description": "节圆直径 mm，6205 为 39.04"},
            },
            "required": ["rpm"],
        },
    },
    {
        "name": "unit_convert",
        "description": "工业单位换算：mm/s↔in/s、℃↔℉、bar↔psi、mm↔mil。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "value": {"type": "number"},
                "from_unit": {"type": "string", "enum": ["mm/s", "in/s", "C", "F", "bar", "psi", "mm", "mil"]},
                "to_unit": {"type": "string", "enum": ["mm/s", "in/s", "C", "F", "bar", "psi", "mm", "mil"]},
            },
            "required": ["value", "from_unit", "to_unit"],
        },
    },
]


def bearing_fault_frequencies(rpm: float, balls: int = 9, ball_diameter_mm: float = 7.94,
                              pitch_diameter_mm: float = 39.04) -> dict:
    fr = rpm / 60.0
    ratio = ball_diameter_mm / pitch_diameter_mm * math.cos(0.0)
    return {
        "shaft_frequency_hz": round(fr, 2),
        "bpfo_hz": round(balls / 2 * (1 - ratio) * fr, 2),
        "bpfi_hz": round(balls / 2 * (1 + ratio) * fr, 2),
        "bsf_hz": round(pitch_diameter_mm / ball_diameter_mm / 2 * (1 - ratio**2) * fr, 2),
        "ftf_hz": round(fr / 2 * (1 - ratio), 2),
        "assumed": {"balls": balls, "ball_diameter_mm": ball_diameter_mm,
                    "pitch_diameter_mm": pitch_diameter_mm, "contact_angle_deg": 0},
    }


_UNIT_TABLE = {
    ("mm/s", "in/s"): 1 / 25.4, ("in/s", "mm/s"): 25.4,
    ("C", "F"): None, ("F", "C"): None,
    ("bar", "psi"): 14.5038, ("psi", "bar"): 1 / 14.5038,
    ("mm", "mil"): 1000 / 25.4, ("mil", "mm"): 25.4 / 1000,
}


def unit_convert(value: float, from_unit: str, to_unit: str) -> dict:
    if from_unit == to_unit:
        result = value
    elif (from_unit, to_unit) == ("C", "F"):
        result = value * 9 / 5 + 32
    elif (from_unit, to_unit) == ("F", "C"):
        result = (value - 32) * 5 / 9
    elif (from_unit, to_unit) in _UNIT_TABLE and _UNIT_TABLE[(from_unit, to_unit)] is not None:
        result = value * _UNIT_TABLE[(from_unit, to_unit)]
    else:
        raise ValueError(f"unsupported conversion {from_unit} → {to_unit}")
    return {"value": round(float(result), 4), "unit": to_unit}


def dispatch(name: str, arguments: dict) -> dict:
    if name == "bearing_fault_frequencies":
        return bearing_fault_frequencies(
            float(arguments["rpm"]),
            int(arguments.get("balls", 9)),
            float(arguments.get("ball_diameter_mm", 7.94)),
            float(arguments.get("pitch_diameter_mm", 39.04)),
        )
    if name == "unit_convert":
        return unit_convert(
            float(arguments["value"]), arguments["from_unit"], arguments["to_unit"]
        )
    raise ValueError(f"unknown tool: {name}")


def handle(msg: dict) -> dict | None:
    method = msg.get("method", "")
    request_id = msg.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "maintenance-calculator", "version": "0.1.0"},
            },
        }
    if method.startswith("notifications/"):
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params", {})
        try:
            data = dispatch(params.get("name", ""), params.get("arguments") or {})
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
