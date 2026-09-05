"""``agentforge`` CLI: serve, ingest, search, eval."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


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

    backup = sub.add_parser("backup", help="online SQLite backup into data/backups/")
    backup.add_argument("--out", default=None, help="output directory")

    export = sub.add_parser("export", help="export a session transcript")
    export.add_argument("session_id")
    export.add_argument("--format", choices=["md", "json"], default="md")
    export.add_argument("-o", "--out", default=None, help="write to file instead of stdout")

    mcp = sub.add_parser("mcp", help="manage MCP servers (list/add/remove/test/serve)")
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_sub.add_parser("list", help="list configured MCP servers (merged from all sources)")
    mcp_add = mcp_sub.add_parser("add", help="add an MCP server entry")
    mcp_add.add_argument("name")
    mcp_add.add_argument("server_command")
    mcp_add.add_argument("server_args", nargs="*", default=[])
    mcp_add.add_argument("--scope", choices=["user", "project"], default="user")
    mcp_remove = mcp_sub.add_parser("remove", help="remove an MCP server entry")
    mcp_remove.add_argument("name")
    mcp_remove.add_argument("--scope", choices=["user", "project"], default="user")
    mcp_test = mcp_sub.add_parser("test", help="spawn a server and list its tools")
    mcp_test.add_argument("name")
    mcp_sub.add_parser("serve", help="expose ForgeOps tools as an MCP server (stdio)")

    chat = sub.add_parser("chat", help="interactive terminal chat (pi-style REPL)")
    chat.add_argument("--session", default=None, help="resume a session id")
    chat.add_argument("--orchestrator", choices=["react", "plan_execute"], default=None)

    sub.add_parser("doctor", help="environment self-check")
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

    if args.command == "backup":
        import datetime
        import sqlite3

        from agentforge.config import load_settings

        settings = load_settings(args.config)
        src_path = settings.db_url.replace("sqlite:///", "") or str(
            settings.data_dir / "agentforge.db"
        )
        out_dir = Path(args.out) if args.out else settings.data_dir / "backups"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = out_dir / f"agentforge-{stamp}.db"

        src_conn = sqlite3.connect(src_path)
        dst_conn = sqlite3.connect(dest)
        with dst_conn:
            src_conn.backup(dst_conn)
        dst_conn.close()
        src_conn.close()

        check = sqlite3.connect(dest).execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            print(f"✗ backup failed integrity check: {dest}")
            return 1
        print(f"✓ backup written: {dest} ({dest.stat().st_size / 1024:.1f} KB, integrity ok)")
        return 0

    if args.command == "export":
        import json as jsonlib

        from agentforge.config import load_settings
        from agentforge.persistence.db import Database

        settings = load_settings(args.config)
        db = Database(settings.db_url, settings.data_dir)
        session = db.get_session(args.session_id)
        if session is None:
            print(f"✗ session not found: {args.session_id}")
            return 1
        messages = db.list_messages(args.session_id)

        if args.format == "json":
            content = jsonlib.dumps(
                {
                    "session": {"id": session.id, "title": session.title,
                                "created_at": session.created_at.isoformat()},
                    "messages": [
                        {"role": m.role, "content": m.content, "name": m.name,
                         "tool_calls": m.tool_calls, "meta": m.meta,
                         "created_at": m.created_at.isoformat()}
                        for m in messages
                    ],
                },
                ensure_ascii=False, indent=2,
            )
        else:
            lines = [f"# {session.title}", "", f"> session `{session.id}` · exported from AgentForge", ""]
            for m in messages:
                if m.role == "user" and (m.meta or {}).get("kind") == "step_instruction":
                    continue
                label = {"user": "👤 用户", "assistant": "⚒ 助手", "tool": "🛠 工具"}.get(m.role, m.role)
                lines.append(f"## {label}" + (f" · {m.name}" if m.name else ""))
                lines.append("")
                lines.append(m.content or "(empty)")
                lines.append("")
            content = "\n".join(lines)

        if args.out:
            Path(args.out).write_text(content, encoding="utf-8")
            print(f"✓ exported to {args.out}")
        else:
            print(content)
        return 0

    if args.command == "mcp":
        import json as jsonlib

        from agentforge.config import MCPServerSpec, load_settings
        from agentforge.mcp_config import (
            add_mcp_server,
            read_mcp_file,
            remove_mcp_server,
            resolve_mcp_servers,
        )

        settings = load_settings(args.config)

        if args.mcp_command == "list":
            from agentforge.mcp_config import project_mcp_path, read_mcp_file, user_mcp_path

            merged = resolve_mcp_servers(settings.mcp_servers)
            file_names = ({s.name for s in read_mcp_file(user_mcp_path())}
                          | {s.name for s in read_mcp_file(project_mcp_path())})
            if not merged:
                print("(no MCP servers configured — use 'agentforge mcp add' or drop a .mcp.json)")
            for spec in merged:
                origin = "file" if spec.name in file_names else "yaml"
                print(f"✓ {spec.name:<18} {spec.command} {' '.join(spec.args)}  [{origin}]{' (disabled)' if not spec.enabled else ''}")
            return 0
        if args.mcp_command == "add":
            path = add_mcp_server(args.name, args.server_command, list(args.server_args), scope=args.scope)
            print(f"✓ added {args.name} → {path}")
            merged = resolve_mcp_servers(settings.mcp_servers)
            spec = next(sp for sp in merged if sp.name == args.name)
            print(f"  {spec.command} {' '.join(spec.args)}")
            return 0
        if args.mcp_command == "remove":
            removed = remove_mcp_server(args.name, scope=args.scope)
            print(f"✓ removed {args.name}" if removed else f"✗ {args.name} not found in scope {args.scope}")
            return 0 if removed else 1
        if args.mcp_command == "test":
            by_name = {spec.name: spec for spec in resolve_mcp_servers(settings.mcp_servers)}
            target_spec: MCPServerSpec | None = by_name.get(args.name)
            if target_spec is None:
                print(f"✗ server {args.name!r} not configured")
                return 1

            async def _test():
                from agentforge.mcp_client import MCPConnection

                conn = MCPConnection(target_spec.name, target_spec.command, target_spec.args, target_spec.env)
                try:
                    await conn.start()
                    tools = await conn.list_tools()
                    print(f"✓ {target_spec.name}: {len(tools)} tools")
                    for t in tools:
                        print(f"  · {t['name']} — {t.get('description', '')[:60]}")
                finally:
                    await conn.stop()

            asyncio.run(_test())
            return 0
        if args.mcp_command == "serve":
            from agentforge.mcp_server import main as serve_main

            return serve_main()
        return 1

    if args.command == "chat":
        from agentforge.chat_repl import chat_repl
        from agentforge.config import load_settings

        settings = load_settings(args.config)
        return asyncio.run(
            chat_repl(settings, args.session, args.orchestrator or settings.agent.orchestrator)
        )

    if args.command == "doctor":
        from agentforge.config import load_settings
        from agentforge.llm.registry import ProviderRegistry
        from agentforge.persistence.db import Database
        from agentforge.tools.registry import build_default_registry

        settings = load_settings(args.config)
        failures = 0

        def emit_check(ok: bool, label: str, detail: str = "", warn: bool = False) -> None:
            nonlocal failures
            mark = "✓" if ok else ("⚠" if warn else "✗")
            if not ok and not warn:
                failures += 1
            print(f"{mark} {label}" + (f" — {detail}" if detail else ""))

        emit_check(True, f"config: {settings.config_path or 'env-only defaults'}")
        try:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            probe = settings.data_dir / ".doctor-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            emit_check(True, f"data dir writable: {settings.data_dir}")
        except OSError as exc:
            emit_check(False, "data dir writable", str(exc))

        try:
            db = Database(settings.db_url, settings.data_dir)
            emit_check(db.health_check(), "database reachable", settings.db_url or "sqlite")
            print(f"  · chunks={db.count_chunks()} work_orders={db.count_work_orders()} "
                  f"sessions={len(db.list_sessions(1000))}")
        except Exception as exc:  # noqa: BLE001
            emit_check(False, "database reachable", str(exc))

        registry = ProviderRegistry(settings.providers, settings.default_model)
        only_mock = registry.names() == ["mock"]
        emit_check(True, f"providers: {', '.join(registry.names())}",
               "only the offline mock is configured" if only_mock else "", warn=only_mock)

        tools = build_default_registry(settings, db, registry=registry)
        emit_check(len(tools.names()) > 0, f"tools: {', '.join(tools.names())}")

        auth_mode = ("jwt" if settings.server.admin_password else "") +                     ("/api-key" if settings.server.api_key else "")
        emit_check(True, "auth: " + (auth_mode.strip("/") or "open dev mode"),
               "" if auth_mode else "set AGENTFORGE_ADMIN_PASSWORD or AGENTFORGE_API_KEY for exposure",
               warn=not auth_mode)
        emit_check(True, "webhook: " + (settings.server.webhook_url or "not configured"),
               warn=not settings.server.webhook_url)

        print(f"\n{'✅ environment ready' if failures == 0 else '❌ ' + str(failures) + ' critical failure(s)'}")
        return 1 if failures else 0

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
