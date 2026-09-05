"""``agentforge`` CLI: serve, ingest, search, eval."""

from __future__ import annotations

import argparse
import asyncio
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentforge", description="AgentForge platform CLI")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start the HTTP server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--reload", action="store_true", help="dev auto-reload")

    ingest = sub.add_parser("ingest", help="ingest text/markdown files into the knowledge base")
    ingest.add_argument("files", nargs="+")

    search = sub.add_parser("search", help="search the knowledge base")
    search.add_argument("query")
    search.add_argument("-k", type=int, default=5)
    search.add_argument("--mode", choices=["hybrid", "vector", "bm25"], default=None)

    evaluate = sub.add_parser("eval", help="run an eval suite YAML")
    evaluate.add_argument("suite")
    evaluate.add_argument("--model", default=None, help="provider/model override")
    evaluate.add_argument("--report", default=None, help="write JSON report to this path")

    sub.add_parser("version", help="print version")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "version":
        import agentforge

        print(f"agentforge {agentforge.__version__}")
        return 0

    if args.command == "serve":
        import uvicorn

        from agentforge.config import load_settings
        from agentforge.server.app import create_app

        settings = load_settings(args.config)
        host = args.host or settings.server.host
        port = args.port or settings.server.port
        uvicorn.run(
            create_app(settings),
            host=host,
            port=port,
            log_level=settings.server.log_level.lower(),
            reload=args.reload,
        )
        return 0

    if args.command == "ingest":
        from pathlib import Path

        from agentforge.config import load_settings
        from agentforge.llm.registry import ProviderRegistry
        from agentforge.persistence.db import Database
        from agentforge.rag.embeddings import build_embedder
        from agentforge.rag.retriever import Retriever

        settings = load_settings(args.config)
        db = Database(settings.db_url, settings.data_dir)
        registry = ProviderRegistry(settings.providers, settings.default_model)
        embedder = build_embedder(settings.rag.embedder, [registry.get(n) for n in registry.names()])
        retriever = Retriever(db, embedder, settings)

        async def _ingest():
            total = 0
            for file in args.files:
                path = Path(file)
                text = path.read_text(encoding="utf-8")
                doc, chunks = await retriever.ingest(text, name=path.name, source=str(path))
                total += chunks
                print(f"✓ {path.name}: {chunks} chunks (id={doc.id})")
            print(f"ingested {total} chunks into {settings.data_dir}")

        asyncio.run(_ingest())
        return 0

    if args.command == "search":
        from agentforge.config import load_settings
        from agentforge.llm.registry import ProviderRegistry
        from agentforge.persistence.db import Database
        from agentforge.rag.embeddings import build_embedder
        from agentforge.rag.retriever import Retriever

        settings = load_settings(args.config)
        db = Database(settings.db_url, settings.data_dir)
        registry = ProviderRegistry(settings.providers, settings.default_model)
        embedder = build_embedder(settings.rag.embedder, [registry.get(n) for n in registry.names()])
        retriever = Retriever(db, embedder, settings)
        results = asyncio.run(retriever.search(args.query, k=args.k, mode=args.mode))
        if not results:
            print("(no results)")
            return 0
        for i, r in enumerate(results, 1):
            print(f"[{i}] {r.document_name} score={r.score:.3f}\n    {r.text[:160]}...")
        return 0

    if args.command == "eval":
        from agentforge.agent.core import Agent
        from agentforge.config import load_settings
        from agentforge.eval.harness import load_cases, run_suite, save_report
        from agentforge.llm.registry import ProviderRegistry
        from agentforge.persistence.db import Database
        from agentforge.rag.embeddings import build_embedder
        from agentforge.rag.retriever import Retriever
        from agentforge.tools.registry import build_default_registry

        settings = load_settings(args.config)
        db = Database(settings.db_url, settings.data_dir)
        registry = ProviderRegistry(settings.providers, settings.default_model)
        embedder = build_embedder(settings.rag.embedder, [registry.get(n) for n in registry.names()])
        retriever = Retriever(db, embedder, settings)
        tools = build_default_registry(settings, db, retriever, registry=registry)
        agent = Agent(db, registry, tools, settings, retriever=retriever)
        cases = load_cases(args.suite)

        from agentforge.forgeops.seed import seed_knowledge_base

        async def _run_suite():
            await seed_knowledge_base(retriever)
            return await run_suite(agent, lambda: db.create_session("eval").id, cases)

        report = asyncio.run(_run_suite())
        if args.report:
            save_report(report, args.report)
            print(f"report written to {args.report}")

        for result in report["results"]:
            mark = "✓" if result["passed"] else "✗"
            print(f"{mark} {result['name']} ({result['elapsed_ms']}ms) tools={result['called_tools']}")
            for failure in result["failures"]:
                print(f"    - {failure}")
        print(f"\n{report['passed']}/{report['total']} passed (rate={report['pass_rate']})")
        return 0 if report["failed"] == 0 else 1

    return 1  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
