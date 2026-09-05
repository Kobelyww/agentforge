"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import agentforge
from agentforge.agent.core import Agent
from agentforge.config import Settings, load_settings
from agentforge.llm.base import BaseLLM
from agentforge.llm.registry import ProviderRegistry
from agentforge.persistence.db import Database
from agentforge.rag.embeddings import build_embedder
from agentforge.rag.retriever import Retriever
from agentforge.server.middleware import RequestContextMiddleware, TokenBucketRateLimitMiddleware
from agentforge.tools.registry import build_default_registry

logger = logging.getLogger("agentforge.app")

_FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


@dataclass
class AppState:
    settings: Settings
    db: Database
    registry: ProviderRegistry
    retriever: Retriever
    tool_registry: object
    agent: Agent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Build state on startup so tests can construct apps per-test cleanly.
        cfg = settings or load_settings()
        db = Database(cfg.db_url, cfg.data_dir)
        registry = ProviderRegistry(
            cfg.providers,
            cfg.default_model,
            cfg.fallback_chain,
            max_retries=2,
        )
        provider_llms: list[BaseLLM] = [registry.get(n) for n in registry.names()]
        embedder = build_embedder(cfg.rag.embedder, provider_llms)
        retriever = Retriever(db, embedder, cfg)
        tool_registry = build_default_registry(cfg, db, retriever)

        # Seed the ForgeOps domain knowledge base on first boot (idempotent).
        from agentforge.forgeops.seed import seed_knowledge_base

        await seed_knowledge_base(retriever)

        # Attach configured MCP servers (external tool ecosystems); failures
        # are logged and skipped so one broken integration can't take us down.
        from agentforge.mcp_client import attach_mcp_servers

        mcp_connections = await attach_mcp_servers(tool_registry.tools, cfg.mcp_servers)

        agent = Agent(db, registry, tool_registry, cfg, retriever=retriever)
        app.state.state = AppState(
            settings=cfg,
            db=db,
            registry=registry,
            retriever=retriever,
            tool_registry=tool_registry,
            agent=agent,
        )
        logger.info(
            "AgentForge ready: providers=%s tools=%s chunks=%d",
            registry.names(), tool_registry.names(), db.count_chunks(),
        )
        try:
            yield
        finally:
            for connection in mcp_connections:
                await connection.stop()
            await registry.aclose()

    app = FastAPI(
        title="AgentForge",
        version=agentforge.__version__,
        description="Production-grade AI agent platform: streaming ReAct runtime, "
        "multi-provider LLM gateway, hybrid RAG, sandboxed tools.",
        lifespan=lifespan,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(TokenBucketRateLimitMiddleware, requests_per_minute=settings.server.rate_limit_rpm)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    from agentforge.forgeops.router import router as forgeops_router
    from agentforge.server.routes import chat, documents, system

    app.include_router(system.router)
    app.include_router(chat.router)
    app.include_router(documents.router)
    app.include_router(forgeops_router)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            {"error": {"code": "internal_error", "message": "internal server error"}},
            status_code=500,
        )

    # Serve the built frontend (SPA with client-side routing fallback).
    if _FRONTEND_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(request: Request, full_path: str):
            candidate = (_FRONTEND_DIST / full_path).resolve()
            if (
                full_path
                and candidate.is_file()
                and candidate.is_relative_to(_FRONTEND_DIST)
            ):
                return FileResponse(candidate)
            return FileResponse(_FRONTEND_DIST / "index.html")

    return app
