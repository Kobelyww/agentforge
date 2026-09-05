"""Tools: schema validation, sandboxed REPL, SSRF guards, ForgeOps domain tools."""


from agentforge.tools.base import ToolContext, validate_args
from agentforge.tools.web_guard import extract_text, validate_url


def _ctx(settings, tmp_path):
    return ToolContext(session_id=None, workspace=tmp_path / "ws", settings=settings)


def test_validate_args():
    schema = {
        "type": "object",
        "properties": {"code": {"type": "string"}, "n": {"type": "integer"}},
        "required": ["code"],
    }
    assert validate_args({"code": "x"}, schema) is None
    assert validate_args({}, schema) == "missing required argument: code"
    assert validate_args({"code": 1}, schema) == "argument 'code' must be of type string"
    assert validate_args(
        {"code": "x", "n": "no"}, schema
    ) == "argument 'n' must be of type integer"


async def test_python_repl_print_and_expression(settings, tmp_path):
    from agentforge.tools.python_repl import PythonREPLTool

    tool = PythonREPLTool(timeout=20)
    ctx = _ctx(settings, tmp_path)

    result = await tool.execute({"code": "print(128*365+42)"}, ctx)
    assert result.ok and "46762" in result.output

    result = await tool.execute({"code": "max([2, 9, 4])"}, ctx)
    assert result.ok and "9" in result.output


async def test_python_repl_runtime_error(settings, tmp_path):
    from agentforge.tools.python_repl import PythonREPLTool

    tool = PythonREPLTool(timeout=20)
    result = await tool.execute({"code": "1/0"}, _ctx(settings, tmp_path))
    assert not result.ok
    assert "ZeroDivisionError" in (result.error or "")


async def test_python_repl_syntax_error(settings, tmp_path):
    from agentforge.tools.python_repl import PythonREPLTool

    tool = PythonREPLTool(timeout=20)
    result = await tool.execute({"code": "def broken(:"}, _ctx(settings, tmp_path))
    assert not result.ok
    assert "SyntaxError" in (result.error or "")


async def test_python_repl_timeout_kills(settings, tmp_path):
    from agentforge.tools.python_repl import PythonREPLTool

    tool = PythonREPLTool(timeout=2.0)
    result = await tool.execute({"code": "import time; time.sleep(30)"}, _ctx(settings, tmp_path))
    assert not result.ok
    assert "timed out" in (result.error or "")


def test_ssrf_guard_blocks_private_and_schemes(monkeypatch):
    import socket

    import agentforge.tools.web_guard as guard

    assert "scheme" in (validate_url("file:///etc/passwd") or "")
    assert "private address" in (validate_url("http://127.0.0.1:8000/") or "")
    assert "private address" in (validate_url("http://192.168.1.10/admin") or "")
    assert "private address" in (validate_url("http://169.254.169.254/latest/meta-data") or "")
    assert "allowlist" in (validate_url("https://example.com", ["trusted.com"]) or "")

    # DNS-dependent cases use a stubbed resolver for determinism
    def _resolve(host, port):
        ip = "93.184.216.34" if "public" in host else "10.0.0.5"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(guard.socket, "getaddrinfo", _resolve)
    assert "private address" in (validate_url("http://internal.secret/host") or "")
    assert validate_url("https://public.example.com/page") is None


def test_extract_text_strips_html():
    html = "<html><head><style>body{}</style></head><body><h1>Title</h1><p>Hello <b>world</b></p><script>alert(1)</script></body></html>"
    text = extract_text(html, "text/html")
    assert "Title" in text and "Hello" in text and "world" in text
    assert "alert" not in text and "body{}" not in text


async def test_sensor_analysis_spectrum(settings, tmp_path):
    import json

    from agentforge.forgeops.tools import SensorAnalysisTool

    tool = SensorAnalysisTool()
    result = await tool.execute(
        {"equipment_id": "AC-017", "operation": "spectrum_peaks"}, _ctx(settings, tmp_path)
    )
    assert result.ok
    payload = json.loads(result.output)
    assert payload["iso10816_status"] == "alarm"  # RMS 4.68 > 4.5
    assert abs(payload["peaks"][0]["freq_hz"] - 176.85) < 2.0  # BPFO peak detected

    result = await tool.execute(
        {"equipment_id": "AC-017", "operation": "rms"}, _ctx(settings, tmp_path)
    )
    assert result.ok and "rms_mm_s" in result.output

    result = await tool.execute(
        {"equipment_id": "NOPE", "operation": "rms"}, _ctx(settings, tmp_path)
    )
    assert not result.ok and "unknown equipment" in result.error


async def test_create_work_order_guardrail(settings, tmp_path):

    from agentforge.forgeops.tools import CreateWorkOrderTool

    db = __import__("agentforge.persistence.db", fromlist=["Database"]).Database(
        settings.db_url, settings.data_dir
    )
    tool = CreateWorkOrderTool(db)
    ctx = _ctx(settings, tmp_path)

    # invalid priority rejected by the schema guardrail
    bad = await tool.execute(
        {"equipment_id": "AC-017", "title": "t", "fault_type": "f",
         "confidence": 0.9, "priority": "P9", "actions": []},
        ctx,
    )
    assert not bad.ok and "priority" in bad.error

    good = await tool.execute(
        {"equipment_id": "ac-017", "title": "轴承更换", "fault_type": "bearing_outer_race_wear",
         "confidence": 0.87, "priority": "P2",
         "actions": ["停机", "更换轴承"], "parts": ["6205-2RS x2"], "estimated_hours": 2},
        ctx,
    )
    assert good.ok
    assert "WO-" in good.output

    orders = db.list_work_orders()
    assert len(orders) == 1
    assert orders[0].equipment_id == "AC-017"  # normalised upper-case
    assert orders[0].status == "open"
