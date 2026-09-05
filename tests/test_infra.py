"""Backend infrastructure: auth, session lock, security headers, webhook, migrations, ops CLI."""

import asyncio
import pathlib

import httpx
import respx

from agentforge.persistence.migrations import MIGRATIONS, run_migrations
from agentforge.server.security import hash_password, jwt_decode, jwt_encode, verify_password


# ---------- security primitives ----------
def test_password_hash_roundtrip():
    stored = hash_password("s3cret!")
    assert stored != "s3cret!" and "$" in stored
    assert verify_password("s3cret!", stored)
    assert not verify_password("wrong", stored)


def test_jwt_lifecycle():
    claims = jwt_encode({"sub": "admin"}, "k3y", expires_s=60)
    decoded = jwt_decode(claims, "k3y")
    assert decoded and decoded["sub"] == "admin"

    assert jwt_decode(claims, "wrong-key") is None  # tampered signature
    expired = jwt_encode({"sub": "admin"}, "k3y", expires_s=-1)
    assert jwt_decode(expired, "k3y") is None  # expired


def test_admin_password_normalized(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTFORGE_ADMIN_PASSWORD", "plain-pw")
    monkeypatch.delenv("AGENTFORGE_AUTH_SECRET", raising=False)
    from agentforge.config import load_settings

    settings = load_settings()
    assert settings.server.admin_password != "plain-pw"  # hashed in memory
    assert verify_password("plain-pw", settings.server.admin_password)
    assert settings.server.auth_secret  # fallback JWT secret derived


# ---------- JWT login flow ----------
async def test_login_and_bearer_access(settings):
    from agentforge.server.app import create_app

    settings.server.admin_password = hash_password("pw-admin")
    settings.server.auth_secret = "test-signing-secret"
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as client,
        app.router.lifespan_context(app),
    ):
            r = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "pw-admin"},
            )
            assert r.status_code == 200
            token = r.json()["access_token"]

            r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
            assert r.json() == {"authenticated": True, "mode": "jwt", "username": "admin"}

            r = await client.get(
                "/api/sessions", headers={"Authorization": f"Bearer {token}"}
            )
            assert r.status_code == 200

            r = await client.post(
                "/api/auth/login", json={"username": "admin", "password": "nope"}
            )
            assert r.status_code == 401

            r = await client.get("/api/sessions", headers={"Authorization": "Bearer bogus"})
            assert r.status_code == 401


# ---------- per-session concurrency lock ----------
async def test_session_busy_lock(settings):
    """A session with a live stream rejects a second chat with 409.

    The lock is the routing-level guard (`_busy_sessions`); the in-process
    ASGI transport buffers whole responses, so liveness is simulated by
    marking the session busy directly, exactly as the running generator does.
    """
    from agentforge.server.app import create_app
    from agentforge.server.routes import chat as chat_routes

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as client,
        app.router.lifespan_context(app),
    ):
            sid = (await client.post("/api/sessions", json={})).json()["id"]

            chat_routes._busy_sessions.add(sid)  # as a live stream would
            second = await client.post(
                f"/api/sessions/{sid}/chat", json={"content": "并发写"}
            )
            assert second.status_code == 409
            assert "active chat stream" in second.json()["detail"]

            # other sessions are unaffected
            sid2 = (await client.post("/api/sessions", json={})).json()["id"]
            ok = await client.post(f"/api/sessions/{sid2}/chat", json={"content": "计算 2+2"})
            assert ok.status_code == 200
            await ok.aread()

            chat_routes._busy_sessions.discard(sid)  # stream finished
            third = await client.post(f"/api/sessions/{sid}/chat", json={"content": "重试"})
            assert third.status_code == 200
            await third.aread()


# ---------- security headers ----------
async def test_security_headers(client):
    r = await client.get("/healthz")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


# ---------- webhook ----------
@respx.mock
async def test_webhook_fired_on_work_order(settings):
    settings.server.webhook_url = "https://hooks.example/notify"
    route = respx.post("https://hooks.example/notify").mock(return_value=httpx.Response(200))

    from agentforge.forgeops.tools import CreateWorkOrderTool
    from agentforge.persistence.db import Database
    from agentforge.tools.base import ToolContext

    db = Database(settings.db_url, settings.data_dir)
    tool = CreateWorkOrderTool(db)
    ctx = ToolContext(
        session_id=None, workspace=settings.data_dir / "ws",
        settings=settings, retriever=None, auto_approve=True,
    )
    result = await tool.execute(
        {"equipment_id": "AC-017", "title": "t", "fault_type": "f",
         "confidence": 0.9, "priority": "P2", "actions": ["x"]},
        ctx,
    )
    assert result.ok
    for _ in range(20):  # fire-and-forget task needs a beat
        if route.called:
            break
        await asyncio.sleep(0.05)
    assert route.called
    body = route.calls[0].request.read().decode()
    assert "work_order.created" in body and "WO-" in body


# ---------- migrations ----------
async def test_migrations_apply_and_idempotent(tmp_path):
    from sqlalchemy import text

    url = f"sqlite:///{tmp_path / 'mig.db'}"
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    import sqlalchemy as sa

    engine = sa.create_engine(url, connect_args=connect_args)
    # production ordering: create_all first, migrations second
    from agentforge.persistence.models import Base

    Base.metadata.create_all(engine)
    final = run_migrations(engine)
    assert final == max(m.version for m in MIGRATIONS)
    assert run_migrations(engine) == final  # idempotent

    with engine.begin() as conn:
        indexes = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_messages_session_seq'"
        )).scalar()
    assert indexes == "ix_messages_session_seq"


# ---------- rotating file log ----------
async def test_file_logging_created(settings):
    from agentforge.server.app import create_app

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as client,
        app.router.lifespan_context(app),
    ):
            await client.get("/healthz")
    log_file = settings.data_dir / "logs" / "agentforge.log"
    assert log_file.is_file() and log_file.stat().st_size > 0


# ---------- ops CLI ----------
def _write_config(tmp_path: pathlib.Path) -> pathlib.Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"data_dir: {tmp_path / 'data'}\n"
        f"db_url: sqlite:///{tmp_path / 'data' / 'ops.db'}\n"
        "providers:\n  - name: mock\n    type: mock\n    model: default\n",
        encoding="utf-8",
    )
    return cfg


def test_cli_doctor_and_backup(tmp_path, capsys):
    from agentforge.cli import main

    cfg = _write_config(tmp_path)
    assert main(["--config", str(cfg), "doctor"]) == 0
    assert main(["--config", str(cfg), "backup"]) == 0
    backups = list((tmp_path / "data" / "backups").glob("agentforge-*.db"))
    assert len(backups) == 1 and backups[0].stat().st_size > 0
    out = capsys.readouterr().out
    assert "✓ backup written" in out and "integrity ok" in out


def test_cli_export_markdown(tmp_path, capsys):
    from agentforge.cli import main
    from agentforge.persistence.db import Database
    from agentforge.persistence.models import Message

    cfg = _write_config(tmp_path)
    settings_data = tmp_path / "data"
    db = Database(f"sqlite:///{settings_data / 'ops.db'}", settings_data)
    session = db.create_session("导出测试")
    db.add_message(Message(session_id=session.id, seq=0, role="user", content="诊断空调"))
    db.add_message(Message(session_id=session.id, seq=1, role="assistant", content="结论：正常"))
    out_file = tmp_path / "export.md"
    assert main(["--config", str(cfg), "export", session.id, "--format", "md", "-o", str(out_file)]) == 0
    exported = out_file.read_text(encoding="utf-8")
    assert "# 导出测试" in exported and "诊断空调" in exported and "结论：正常" in exported
