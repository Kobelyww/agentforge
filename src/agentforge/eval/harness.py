"""Eval harness: run the agent loop against YAML cases and score them."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from agentforge.agent.core import Agent


@dataclass
class EvalCase:
    name: str
    input: str
    description: str = ""
    expect_tools: list[str] = field(default_factory=list)
    expect_contains: list[str] = field(default_factory=list)
    max_iterations: int = 6
    orchestrator: str | None = None  # default: agent settings (react)


@dataclass
class CaseResult:
    name: str
    passed: bool
    called_tools: list[str]
    final_text: str
    failures: list[str]
    elapsed_ms: float


def load_cases(path: str | Path) -> list[EvalCase]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cases = raw.get("cases", raw) if isinstance(raw, dict) else raw
    return [EvalCase(**case) for case in cases]


async def run_case(agent: Agent, session_factory, case: EvalCase) -> CaseResult:
    session_id = session_factory()
    called_tools: list[str] = []
    final_text = ""
    started = time.perf_counter()

    async for event in agent.run(
        session_id, case.input, max_iterations=case.max_iterations,
        orchestrator=case.orchestrator,
    ):
        if event.type == "tool_start":
            called_tools.append(event.data["name"])
        elif event.type == "assistant_message":
            final_text = event.data["message"]["content"] or ""

    elapsed_ms = (time.perf_counter() - started) * 1000
    failures: list[str] = []
    missing_tools = [t for t in case.expect_tools if t not in called_tools]
    if missing_tools:
        failures.append(f"tools not called: {missing_tools} (called: {called_tools})")
    lowered = final_text.lower()
    for needle in case.expect_contains:
        if needle.lower() not in lowered:
            failures.append(f"final answer missing {needle!r}")
    if not final_text.strip():
        failures.append("empty final answer")

    return CaseResult(
        name=case.name,
        passed=not failures,
        called_tools=called_tools,
        final_text=final_text,
        failures=failures,
        elapsed_ms=round(elapsed_ms, 1),
    )


async def run_suite(agent: Agent, session_factory, cases: list[EvalCase]) -> dict:
    results = [await run_case(agent, session_factory, case) for case in cases]
    passed = sum(1 for r in results if r.passed)
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results), 3) if results else 0.0,
        "results": [
            {
                "name": r.name,
                "passed": r.passed,
                "called_tools": r.called_tools,
                "failures": r.failures,
                "elapsed_ms": r.elapsed_ms,
                "final_text_preview": r.final_text[:200],
            }
            for r in results
        ],
    }


def save_report(report: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
