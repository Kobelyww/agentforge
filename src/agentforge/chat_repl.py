"""``agentforge chat`` — a pi-style terminal chat loop over the engine.

Runs the full agent runtime in-process (no HTTP): streaming text, tool-call
tickers, plan/step progress, slash commands, and HITL approval decisions
directly in the terminal.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

ACCENT = "\033[38;5;75m"
DIM = "\033[2m"
GREEN = "\033[38;5;71m"
RED = "\033[38;5;167m"
AMBER = "\033[38;5;179m"
RESET = "\033[0m"
BOLD = "\033[1m"


class ChatSessionState:
    """Mutable REPL state shared with slash-command handlers."""

    def __init__(self, stack: dict[str, Any], session_id: str | None, orchestrator: str) -> None:
        self.stack = stack
        self.session_id = session_id
        self.orchestrator = orchestrator
        self.model: str | None = None
        self.auto_approve = True

    @property
    def agent(self):
        return self.stack["agent"]

    @property
    def db(self):
        return self.stack["db"]


HELP = """\
commands:
  /help                  show this help
  /new [title]           start a new session
  /sessions              list recent sessions
  /session <id>          switch to a session
  /trace [id]            print the decision trace of a session
  /tools                 list registered tools
  /model [provider/model]  show or switch the model
  /orchestrator <mode>   react | plan_execute
  /auto <on|off>         auto-approve P1/P2 work orders (off = HITL prompt)
  /export <id>           export a session transcript as markdown
  /quit                  leave
anything else is sent to the agent. Enter to send; multi-line not supported.\
"""


def handle_command(line: str, state: ChatSessionState) -> bool:
    """Execute a slash command. Returns False when the REPL should exit."""
    parts = line.split()
    cmd, args = parts[0].lower(), parts[1:]
    agent = state.agent

    if cmd in ("/quit", "/exit", "/q"):
        return False
    if cmd == "/help":
        print(HELP)
    elif cmd == "/new":
        state.session_id = agent._db.create_session(" ".join(args) or "REPL 会话").id
        print(f"{DIM}session {state.session_id}{RESET}")
    elif cmd == "/sessions":
        for s in agent._db.list_sessions(10):
            marker = "*" if s.id == state.session_id else " "
            print(f"{marker} {s.id[:8]}  {s.title}")
    elif cmd == "/session":
        if not args:
            print(f"{RED}usage: /session <id>{RESET}")
        else:
            target = next((s for s in agent._db.list_sessions(100) if s.id.startswith(args[0])), None)
            if target:
                state.session_id = target.id
                print(f"{DIM}switched to {target.id[:8]} · {target.title}{RESET}")
            else:
                print(f"{RED}session not found: {args[0]}{RESET}")
    elif cmd == "/trace":
        import json as _json

        sid = args[0] if args else state.session_id
        session = agent._db.get_session(sid) if sid else None
        if session is None:
            print(f"{RED}session not found{RESET}")
        else:
            messages = agent._db.list_messages(session.id)
            plan = next((m for m in messages if (m.meta or {}).get("kind") == "plan"), None)
            if plan:
                parsed = _json.loads(plan.content)
                print(f"{DIM}plan: {' → '.join(s['title'] for s in parsed['steps'])}{RESET}")
            print(f"{DIM}messages={len(messages)} tool_invocations={len(agent._db.list_tool_invocations(session.id))}{RESET}")
    elif cmd == "/tools":
        for spec in agent._tools.specs():
            print(f"{ACCENT}{spec.name}{RESET} — {spec.description[:70]}")
    elif cmd == "/model":
        if args:
            state.model = args[0]
        print(f"model: {state.model or agent._registry.resolve()!r}")
    elif cmd == "/orchestrator":
        if args and args[0] in ("react", "plan_execute"):
            state.orchestrator = args[0]
        elif args:
            print(f"{RED}invalid mode {args[0]!r} — react | plan_execute{RESET}")
        print(f"orchestrator: {state.orchestrator}")
    elif cmd == "/auto":
        if args and args[0] in ("on", "off"):
            state.auto_approve = args[0] == "on"
        print(f"auto_approve: {state.auto_approve}")
    elif cmd == "/export":

        from agentforge.cli import main as cli_main

        sid = args[0] if args else state.session_id
        if not sid:
            print(f"{RED}usage: /export <session-id>{RESET}")
        else:
            cli_main(["--config", str(state.stack["settings"].config_path or ""),
                      "export", sid, "--format", "md"] if state.stack["settings"].config_path
                     else ["export", sid, "--format", "md"])
    else:
        print(f"{RED}unknown command {cmd} — /help for help{RESET}")
    return True


async def _stream_turn(state: ChatSessionState, content: str) -> None:
    if state.session_id is None:
        state.session_id = state.agent._db.create_session("REPL 会话").id
        print(f"{DIM}session {state.session_id}{RESET}")

    async for event in state.agent.run(
        state.session_id, content, model=state.model,
        orchestrator=state.orchestrator, auto_approve=state.auto_approve,
    ):
        match event.type:
            case "text_delta":
                print(event.data["text"], end="", flush=True)
            case "tool_start":
                print(f"\n{ACCENT}  🛠 {event.data['name']}{RESET} {DIM}{json.dumps(event.data['arguments'], ensure_ascii=False)[:100]}{RESET}")
            case "tool_end":
                mark = f"{GREEN}ok{RESET}" if event.data["ok"] else f"{RED}fail{RESET}"
                print(f"  {DIM}↳ {mark} ({event.data['latency_ms']:.0f}ms) {str(event.data['output'])[:140]}{RESET}")
            case "plan_created":
                chain = " → ".join(s["title"] for s in event.data["steps"])
                print(f"\n{BOLD}📋 计划{RESET} {DIM}{chain}{RESET}")
            case "step_started":
                print(f"\n{ACCENT}▶ {event.data['step_id']} {event.data['title']}{RESET}")
            case "step_completed":
                print(f"{DIM}  ✓ {event.data['elapsed_ms']:.0f}ms{RESET}")
            case "phase":
                pass
            case "approval_required":
                print(f"\n{AMBER}⚠ HITL {event.data['message']}{RESET}")
                answer = await asyncio.to_thread(input, f"{BOLD}批准执行? [y/N] {RESET}")
                decision = "approved" if answer.strip().lower() in ("y", "yes") else "rejected"
                state.db.decide_approval(event.data["approval_id"], decision, "repl-user")
                print(f"{DIM}决定: {decision}{RESET}")
            case "critic_verdict":
                if not event.data["pass"]:
                    print(f"{AMBER}🧐 审核未通过: {event.data['issues']}{RESET}")
                elif event.data.get("revised"):
                    print(f"{DIM}🧐 审核通过（已修订）{RESET}")
            case "error":
                print(f"\n{RED}✗ {event.data['message']}{RESET}")
    print()


async def chat_repl(settings: Any, session_id: str | None, orchestrator: str) -> int:
    from agentforge.agent.core import Agent
    from agentforge.llm.registry import ProviderRegistry
    from agentforge.persistence.db import Database
    from agentforge.rag.embeddings import build_embedder
    from agentforge.rag.retriever import Retriever
    from agentforge.tools.registry import build_default_registry

    db = Database(settings.db_url, settings.data_dir)
    registry = ProviderRegistry(settings.providers, settings.default_model)
    embedder = build_embedder(settings.rag.embedder, [registry.get(n) for n in registry.names()])
    retriever = Retriever(db, embedder, settings)
    tools = build_default_registry(settings, db, retriever, registry=registry)
    agent = Agent(db, registry, tools, settings, retriever=retriever)

    state = ChatSessionState(
        stack={"agent": agent, "db": db, "settings": settings},
        session_id=session_id, orchestrator=orchestrator,
    )

    print(f"{BOLD}⚒ ForgeOps REPL{RESET} {DIM}· {state.orchestrator} · /help for commands · /quit to leave{RESET}")
    while True:
        try:
            line = await asyncio.to_thread(input, f"{ACCENT}you>{RESET} ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        line = line.strip()
        if not line:
            continue
        if line.startswith("/"):
            if not handle_command(line, state):
                return 0
            continue
        try:
            await _stream_turn(state, line)
        except asyncio.CancelledError:
            return 130
        except Exception as exc:  # noqa: BLE001 - REPL must survive any turn
            print(f"{RED}✗ turn failed: {exc}{RESET}")


def new_session_id() -> str:
    return uuid.uuid4().hex
