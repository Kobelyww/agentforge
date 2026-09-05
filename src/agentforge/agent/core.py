"""The agent runtime.

Two orchestration modes:

- ``react`` — classic ReAct loop per user turn (stream, call tools, feed
  results back) — robust default for chat.
- ``plan_execute`` — the user turn is decomposed by a **Planner** into an
  explicit step plan, each step is executed by an **Executor** (its own ReAct
  loop), and a **Synthesizer** merges step results into the final answer.
  Every step is persisted as it completes, so long tasks are auditable and
  partial progress survives disconnects.

Every event is yielded as an :class:`AgentEvent` so the API layer can serialise
it to SSE and the eval harness can consume it without a server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from agentforge.agent.context import build_llm_messages
from agentforge.agent.memory import SessionMemory
from agentforge.agent.prompts import (
    EXECUTOR_SYSTEM,
    PLANNER_SYSTEM,
    SYNTHESIZER_SYSTEM,
    SYSTEM_PROMPT,
)
from agentforge.config import Settings
from agentforge.llm.types import (
    ChatMessage,
    Finish,
    ProviderError,
    Routed,
    TextDelta,
    ToolCallDelta,
    ToolSpec,
    estimate_tokens,
)
from agentforge.observability.metrics import (
    AGENT_ITERATIONS,
    LLM_CALLS,
    LLM_LATENCY,
    LLM_TOKENS,
)
from agentforge.persistence.db import Database
from agentforge.persistence.models import Message
from agentforge.tools.base import ToolContext
from agentforge.tools.registry import ToolRegistry

logger = logging.getLogger("agentforge.agent")


@dataclass
class AgentEvent:
    type: str
    data: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"type": self.type, **self.data}


def parse_plan(text: str) -> dict | None:
    """Extract a planner JSON object from model output (balanced-brace scan)."""
    start = text.find("{")
    if start == -1:
        return None
    decoder = json.JSONDecoder()
    try:
        plan, _ = decoder.raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    if not isinstance(plan, dict):
        return None
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    cleaned = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            return None
        cleaned.append(
            {
                "id": str(step.get("id", f"s{i + 1}")),
                "title": str(step.get("title", f"步骤{i + 1}"))[:80],
                "instruction": str(step.get("instruction", "")).strip(),
            }
        )
        if not cleaned[-1]["instruction"]:
            return None
    plan["steps"] = cleaned
    return plan


class Agent:
    def __init__(
        self,
        db: Database,
        registry: Any,  # ProviderRegistry
        tool_registry: ToolRegistry,
        settings: Settings,
        retriever: Any | None = None,
    ) -> None:
        self._db = db
        self._registry = registry
        self._tools = tool_registry
        self._settings = settings
        self._memory = SessionMemory(
            db,
            lambda: registry.get(registry.resolve()[0]),
            threshold_tokens=settings.agent.summary_threshold_tokens,
        )
        self._retriever = retriever

    # ---------- public API ----------
    async def run(
        self,
        session_id: str,
        content: str,
        *,
        model: str | None = None,
        max_iterations: int | None = None,
        orchestrator: str | None = None,
    ) -> Any:  # AsyncIterator[AgentEvent]
        """Execute one user turn; yields AgentEvent stream."""
        orchestrator = orchestrator or getattr(self._settings.agent, "orchestrator", "react")
        max_iterations = max_iterations or self._settings.agent.max_iterations
        started = time.perf_counter()

        provider_name, model_name = self._registry.resolve(model)

        # 1. Persist the user message.
        seq = await asyncio.to_thread(self._db.next_seq, session_id)
        user_row = Message(
            session_id=session_id, seq=seq, role="user", content=content,
            tokens=estimate_tokens(content),
        )
        await asyncio.to_thread(self._db.add_message, user_row)
        yield AgentEvent("user_message", {"message": _row_to_dict(user_row)})

        if await asyncio.to_thread(self._db.count_messages, session_id) == 1:
            await asyncio.to_thread(
                self._db.update_session, session_id, title=content.strip()[:40] or "新会话"
            )

        # 2. Memory + context.
        history = self._memory.load(session_id)
        summary = await self._memory.maybe_summarize(session_id, history)
        if summary:
            from agentforge.agent.context import ContextWindow

            history = ContextWindow(self._settings.agent.context_budget_tokens).fit(
                history, summary=summary
            )

        tool_ctx = ToolContext(
            session_id=session_id,
            workspace=self._settings.data_dir / "workspace" / session_id,
            settings=self._settings,
            retriever=self._retriever,
        )
        specs: list[ToolSpec] = self._tools.specs()

        final_text = ""
        try:
            if orchestrator == "plan_execute":
                async for event in self._plan_execute(
                    session_id, content, history, summary, specs, tool_ctx,
                    provider_name, model_name, max_iterations,
                ):
                    if event.type == "assistant_message":
                        final_text = event.data["message"]["content"] or ""
                    yield event
            else:
                async for event in self._react(
                    session_id, history, summary, specs, tool_ctx,
                    provider_name, model_name, SYSTEM_PROMPT, max_iterations,
                    meta_kind=None,
                ):
                    if event.type == "assistant_message":
                        final_text = event.data["message"]["content"] or ""
                    yield event
            AGENT_ITERATIONS.labels(outcome="completed").inc()
        except asyncio.CancelledError:
            AGENT_ITERATIONS.labels(outcome="cancelled").inc()
            raise

        total_ms = (time.perf_counter() - started) * 1000
        yield AgentEvent("done", {"session_id": session_id, "elapsed_ms": round(total_ms, 1),
                                  "final_text": final_text[:200]})

    # ---------- ReAct inner loop (shared by both orchestrators) ----------
    async def _react(
        self,
        session_id: str,
        history: list[ChatMessage],
        summary: str,
        specs: list[ToolSpec],
        tool_ctx: ToolContext,
        provider_name: str,
        model_name: str,
        system_prompt: str,
        max_iterations: int,
        *,
        meta_kind: str | None = None,
        step_id: str | None = None,
    ) -> Any:  # AsyncIterator[AgentEvent]
        seq = await asyncio.to_thread(self._db.next_seq, session_id)
        text_parts: list[str] = []

        try:
            for iteration in range(max_iterations):
                yield AgentEvent("iteration", {"index": iteration + 1, "max": max_iterations})

                llm_messages = build_llm_messages(
                    system_prompt, history, summary=summary,
                    budget_tokens=self._settings.agent.context_budget_tokens,
                )

                text_parts = []
                tool_deltas: list[ToolCallDelta] = []
                usage = None
                finish_reason = "stop"
                llm_started = time.perf_counter()
                served_provider, served_model = provider_name, model_name

                try:
                    async for event in self._registry.stream(
                        llm_messages, specs,
                        model=f"{provider_name}/{model_name}" if model_name else provider_name,
                    ):
                        match event:
                            case Routed(provider=p, model=m):
                                served_provider, served_model = p, m
                            case TextDelta(text=t):
                                text_parts.append(t)
                                yield AgentEvent("text_delta", {"text": t})
                            case ToolCallDelta():
                                tool_deltas.append(event)
                            case Finish(finish_reason=reason, usage=u):
                                finish_reason = reason
                                if u:
                                    usage = u
                    llm_status = "ok"
                except ProviderError as exc:
                    LLM_CALLS.labels(provider=served_provider, model=served_model, status="error").inc()
                    yield AgentEvent("error", {"message": f"LLM 调用失败: {exc}"})
                    return

                LLM_CALLS.labels(provider=served_provider, model=served_model, status=llm_status).inc()
                LLM_LATENCY.labels(provider=served_provider).observe(time.perf_counter() - llm_started)
                if usage:
                    LLM_TOKENS.labels(provider=served_provider, direction="prompt").inc(usage.prompt_tokens)
                    LLM_TOKENS.labels(provider=served_provider, direction="completion").inc(usage.completion_tokens)

                from agentforge.llm.types import accumulate_tool_calls

                tool_calls = accumulate_tool_calls(tool_deltas)
                text = "".join(text_parts)

                # Persist the assistant turn.
                meta: dict[str, Any] = {
                    "provider": served_provider, "model": served_model,
                    "finish_reason": finish_reason, "iteration": iteration + 1,
                }
                if meta_kind:
                    meta["kind"] = meta_kind
                if step_id:
                    meta["step_id"] = step_id
                seq += 1
                assistant_row = Message(
                    session_id=session_id, seq=seq, role="assistant", content=text,
                    tool_calls=[{"id": c.id, "name": c.name, "arguments": c.arguments} for c in tool_calls] or None,
                    tokens=usage.completion_tokens if usage else estimate_tokens(text),
                    latency_ms=round((time.perf_counter() - llm_started) * 1000, 1),
                    meta=meta,
                )
                await asyncio.to_thread(self._db.add_message, assistant_row)
                history.append(
                    ChatMessage(role="assistant", content=text or None, tool_calls=tool_calls or None)
                )

                if not tool_calls:
                    yield AgentEvent("assistant_message", {"message": _row_to_dict(assistant_row)})
                    return

                # Execute every requested tool, feed results back.
                for call in tool_calls:
                    yield AgentEvent(
                        "tool_start",
                        {"call_id": call.id, "name": call.name, "arguments": call.arguments},
                    )
                    result = await self._tools.run(call, tool_ctx)
                    seq += 1
                    tool_row = Message(
                        session_id=session_id, seq=seq, role="tool",
                        content=result.to_llm_payload(),
                        tool_call_id=call.id, name=call.name,
                        tokens=estimate_tokens(result.output),
                        latency_ms=result.meta.get("latency_ms", 0.0),
                        meta={"ok": result.ok, "step_id": step_id,
                              **{k: v for k, v in result.meta.items() if k != "latency_ms"}},
                    )
                    await asyncio.to_thread(self._db.add_message, tool_row)
                    history.append(
                        ChatMessage(role="tool", content=tool_row.content, tool_call_id=call.id, name=call.name)
                    )
                    yield AgentEvent(
                        "tool_end",
                        {"call_id": call.id, "name": call.name, "ok": result.ok,
                         "output": result.output[:2000], "error": result.error,
                         "latency_ms": result.meta.get("latency_ms", 0.0)},
                    )

                if iteration == max_iterations - 1:
                    AGENT_ITERATIONS.labels(outcome="max_iterations").inc()
                    yield AgentEvent(
                        "error", {"message": f"已达到最大迭代次数 {max_iterations}，任务未收敛。"}
                    )
                    return
        except asyncio.CancelledError:
            # Client disconnected: persist partial text so the transcript stays coherent.
            if text_parts:
                partial = "".join(text_parts)
                seq += 1
                await asyncio.to_thread(
                    self._db.add_message,
                    Message(
                        session_id=session_id, seq=seq, role="assistant",
                        content=partial + "\n\n[响应被中断]",
                        tokens=estimate_tokens(partial), meta={"cancelled": True},
                    ),
                )
            raise

    # ---------- Plan-and-Execute orchestrator ----------
    async def _plan_execute(
        self,
        session_id: str,
        task: str,
        history: list[ChatMessage],
        summary: str,
        specs: list[ToolSpec],
        tool_ctx: ToolContext,
        provider_name: str,
        model_name: str,
        max_iterations: int,
    ) -> Any:  # AsyncIterator[AgentEvent]
        provider = self._registry.get(provider_name)

        # ---- 1. Plan ----
        yield AgentEvent("phase", {"phase": "planning"})
        tool_digest = ", ".join(t.name for t in specs) or "（无工具）"
        try:
            plan_response = await provider.complete(
                [
                    ChatMessage(role="system", content=PLANNER_SYSTEM.format(tools=tool_digest)),
                    ChatMessage(role="user", content=f"用户任务：{task}"),
                ],
                model=model_name or None,
            )
        except ProviderError as exc:
            yield AgentEvent("error", {"message": f"规划失败: {exc}"})
            return

        plan = parse_plan(plan_response.message.content or "")
        if plan is None:
            logger.warning("planner output not parseable, falling back to single-step plan")
            plan = {
                "thought": "规划输出解析失败，退化为单步执行。",
                "steps": [{"id": "s1", "title": "执行任务", "instruction": task}],
                "success_criteria": "回答用户任务",
            }

        seq = await asyncio.to_thread(self._db.next_seq, session_id)
        seq += 1
        await asyncio.to_thread(
            self._db.add_message,
            Message(
                session_id=session_id, seq=seq, role="assistant",
                content=json.dumps(plan, ensure_ascii=False, indent=2),
                meta={"kind": "plan", "provider": served_provider_name(provider_name, plan_response)},
            ),
        )
        history.append(
            ChatMessage(
                role="assistant",
                content="任务计划：" + " → ".join(s["title"] for s in plan["steps"]),
            )
        )
        yield AgentEvent("plan_created", {"thought": plan.get("thought", ""),
                                          "steps": plan["steps"],
                                          "success_criteria": plan.get("success_criteria", "")})

        # ---- 2. Execute steps ----
        step_results: list[dict] = []
        for index, step in enumerate(plan["steps"]):
            yield AgentEvent("phase", {"phase": "executing"})
            yield AgentEvent("step_started",
                             {"step_id": step["id"], "title": step["title"],
                              "index": index + 1, "total": len(plan["steps"]),
                              "instruction": step["instruction"]})

            # Persist the step instruction (role=user) so traces rebuild from DB.
            step_seq = await asyncio.to_thread(self._db.next_seq, session_id)
            step_seq += 1
            await asyncio.to_thread(
                self._db.add_message,
                Message(
                    session_id=session_id, seq=step_seq, role="user",
                    content=f"【当前步骤 {step['id']}】{step['title']}\n指令：{step['instruction']}",
                    meta={"kind": "step_instruction", "step_id": step["id"]},
                ),
            )
            history.append(
                ChatMessage(
                    role="user",
                    content=f"【当前步骤 {step['id']}】{step['title']}\n指令：{step['instruction']}",
                )
            )
            step_started = time.perf_counter()
            step_summary = ""
            plan_digest = " → ".join(f"{s['id']}:{s['title']}" for s in plan["steps"])
            async for event in self._react(
                session_id, history, summary, specs, tool_ctx,
                provider_name, model_name,
                EXECUTOR_SYSTEM.format(plan_digest=plan_digest, step_instruction=step["instruction"]),
                max_iterations,
                meta_kind="step", step_id=step["id"],
            ):
                if event.type == "assistant_message":
                    step_summary = event.data["message"]["content"] or ""
                yield event

            elapsed = round((time.perf_counter() - step_started) * 1000, 1)
            step_results.append({"id": step["id"], "title": step["title"],
                                 "summary": step_summary[:600]})
            yield AgentEvent("step_completed",
                             {"step_id": step["id"], "title": step["title"],
                              "index": index + 1, "summary": step_summary[:800],
                              "elapsed_ms": elapsed})

        # ---- 3. Synthesize ----
        yield AgentEvent("phase", {"phase": "synthesizing"})
        transcript = "\n".join(
            f"[{r['id']} {r['title']}] {r['summary']}" for r in step_results
        )
        try:
            final_response = await provider.complete(
                [
                    ChatMessage(role="system", content=SYNTHESIZER_SYSTEM),
                    ChatMessage(role="user", content=f"用户任务：{task}\n\n步骤结果：\n{transcript}"),
                ],
                model=model_name or None,
            )
        except ProviderError as exc:
            yield AgentEvent("error", {"message": f"汇总失败: {exc}"})
            return

        final_text = final_response.message.content or step_results[-1]["summary"] if step_results else ""
        seq = await asyncio.to_thread(self._db.next_seq, session_id)
        seq += 1
        final_row = Message(
            session_id=session_id, seq=seq, role="assistant", content=final_text,
            tokens=estimate_tokens(final_text),
            latency_ms=round((final_response.usage.total_tokens) * 1.0, 1) if final_response.usage else 0.0,
            meta={"kind": "final", "provider": final_response.provider, "model": final_response.model},
        )
        await asyncio.to_thread(self._db.add_message, final_row)
        yield AgentEvent("assistant_message", {"message": _row_to_dict(final_row)})


def served_provider_name(fallback: str, response: Any) -> str:
    return getattr(response, "provider", None) or fallback


def _row_to_dict(row: Message) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "seq": row.seq,
        "role": row.role,
        "content": row.content,
        "tool_calls": row.tool_calls,
        "tool_call_id": row.tool_call_id,
        "name": row.name,
        "tokens": row.tokens,
        "latency_ms": row.latency_ms,
        "meta": row.meta,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
