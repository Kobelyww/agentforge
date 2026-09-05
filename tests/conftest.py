"""Shared fixtures."""

import json

import httpx
import pytest


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTFORGE_CONFIG", raising=False)
    monkeypatch.delenv("AGENTFORGE_API_KEY", raising=False)
    from agentforge.config import load_settings

    s = load_settings()
    s.data_dir = tmp_path / "data"
    s.db_url = f"sqlite:///{tmp_path / 'test.db'}"
    s.rag.chunk_size = 300
    s.rag.chunk_overlap = 40
    s.server.rate_limit_rpm = 100000  # effectively unlimited unless a test overrides
    return s


@pytest.fixture()
async def client(settings):
    from agentforge.server.app import create_app

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c,
        app.router.lifespan_context(app),
    ):
        yield c


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into (event, data) tuples."""
    events: list[tuple[str, dict]] = []
    for frame in text.split("\n\n"):
        if not frame.startswith("event:"):
            continue
        lines = frame.split("\n")
        etype = lines[0][len("event:") :].strip()
        data: dict = {}
        for line in lines[1:]:
            if line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
                break
        events.append((etype, data))
    return events
