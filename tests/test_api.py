"""HTTP API: system endpoints, sessions, SSE chat, documents, auth, rate limit, trace, ForgeOps."""

import httpx


async def test_health_and_readiness(client):
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"

    r = await client.get("/readyz")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ready"
    assert body["database"] is True
    assert "mock" in body["providers"]
    assert body["chunks"] > 0  # ForgeOps knowledge base auto-seeded


async def test_metrics_exposed(client):
    r = await client.get("/metrics")
    assert r.status_code == 200
    assert "agentforge_http_requests_total" in r.text
    assert "agentforge_llm_calls_total" in r.text


async def test_session_crud(client):
    r = await client.post("/api/sessions", json={"title": "测试会话"})
    assert r.status_code == 201
    sid = r.json()["id"]

    r = await client.get("/api/sessions")
    assert any(s["id"] == sid for s in r.json())

    r = await client.patch(f"/api/sessions/{sid}", json={"title": "改名"})
    assert r.json()["title"] == "改名"

    r = await client.delete(f"/api/sessions/{sid}")
    assert r.status_code == 204
    r = await client.get(f"/api/sessions/{sid}")
    assert r.status_code == 404


async def test_chat_react_stream_with_tool(client, parse_sse):
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    r = await client.post(
        f"/api/sessions/{sid}/chat", json={"content": "帮我计算 55*66"}
    )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]

    events = parse_sse(r.text)
    types = [t for t, _ in events]
    assert types[0] == "open"
    assert "tool_start" in types and "tool_end" in types
    assert types[-1] == "done"

    tool_end = next(d for t, d in events if t == "tool_end")
    assert tool_end["name"] == "python_repl"
    assert "3630" in tool_end["output"]

    # auto-titled from the first user message
    session = (await client.get(f"/api/sessions/{sid}")).json()
    assert "帮我计算" in session["title"]

    # reload: transcript persisted with tool output attached
    messages = session["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant"]


async def test_chat_404(client):
    r = await client.post("/api/sessions/doesnotexist/chat", json={"content": "hi"})
    assert r.status_code == 404


async def test_document_ingest_and_search(client):
    r = await client.post(
        "/api/documents",
        json={"name": "notes.md", "text": "# 笔记\nReAct 是推理与行动结合的 Agent 范式，工具调用扩展了模型能力边界。"},
    )
    assert r.status_code == 201
    doc_id = r.json()["document"]["id"]

    r = await client.post("/api/documents/search", json={"query": "ReAct 什么范式"})
    hits = r.json()["results"]
    assert hits and hits[0]["document_id"] == doc_id

    r = await client.delete(f"/api/documents/{doc_id}")
    assert r.status_code == 204
    r = await client.post("/api/documents/search", json={"query": "ReAct 什么范式"})
    results = r.json()["results"]
    # the deleted document's chunks are gone (other seeded docs may remain)
    assert all(hit["document_name"] != "notes.md" for hit in results)


async def test_forgeops_equipment_and_workorders(client):
    r = await client.get("/api/forgeops/equipment")
    eq = r.json()
    assert {e["id"] for e in eq} >= {"AC-017", "WP-203"}

    # drive a plan-execute diagnosis through the API
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    await client.post(
        f"/api/sessions/{sid}/chat",
        json={"content": "诊断 AC-017 振动报警异响，给出结论并生成工单",
              "orchestrator": "plan_execute"},
    )
    r = await client.get("/api/forgeops/workorders")
    orders = r.json()
    assert len(orders) == 1
    assert orders[0]["equipment_id"] == "AC-017"
    assert orders[0]["priority"] == "P2"

    # status transition
    code = orders[0]["code"]
    r = await client.post(f"/api/forgeops/workorders/{code}/status", json={"status": "in_progress"})
    assert r.json()["status"] == "in_progress"
    r = await client.post(f"/api/forgeops/workorders/{code}/status", json={"status": "bogus"})
    assert r.status_code == 400


async def test_trace_endpoint(client):
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    await client.post(
        f"/api/sessions/{sid}/chat",
        json={"content": "诊断 AC-017 振动报警异响，给出结论并生成工单",
              "orchestrator": "plan_execute"},
    )
    r = await client.get(f"/api/sessions/{sid}/trace")
    assert r.status_code == 200
    trace = r.json()
    assert trace["orchestrator"] == "plan_execute"
    assert trace["plan"] and len(trace["plan"]["steps"]) == 3
    assert len(trace["steps"]) == 3
    assert all(step["tools"] for step in trace["steps"])
    assert trace["totals"]["tool_calls"] == 3
    assert "轴承外圈磨损" in (trace["final"] or "")


async def test_api_key_auth(settings):
    from agentforge.server.app import create_app

    settings.server.api_key = "s3cret"
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
            r = await client.get("/healthz")  # unauthenticated paths stay open
            assert r.status_code == 200
            r = await client.get("/api/sessions")
            assert r.status_code == 401
            r = await client.get("/api/sessions", headers={"X-API-Key": "wrong"})
            assert r.status_code == 401
            r = await client.get("/api/sessions", headers={"X-API-Key": "s3cret"})
            assert r.status_code == 200


async def test_rate_limit(settings):
    from agentforge.server.app import create_app

    settings.server.rate_limit_rpm = 3  # capacity 3
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
            statuses = []
            for _ in range(5):
                r = await client.get("/api/sessions")
                statuses.append(r.status_code)
            assert 429 in statuses
            r = await client.get("/healthz")  # health endpoints exempt
            assert r.status_code == 200
