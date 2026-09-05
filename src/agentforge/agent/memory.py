"""Conversation memory: DB-backed history with LLM rolling summarisation."""

from __future__ import annotations

import logging
from collections.abc import Callable

from agentforge.llm.base import BaseLLM
from agentforge.llm.types import ChatMessage, estimate_tokens
from agentforge.persistence.db import Database
from agentforge.persistence.models import Message

logger = logging.getLogger("agentforge.memory")


def history_to_messages(rows: list[Message]) -> list[ChatMessage]:
    """Map persisted rows to provider-agnostic ChatMessages.

    Tool rows are keyed to their assistant tool_calls via tool_call_id.
    """
    out: list[ChatMessage] = []
    for row in rows:
        if row.role == "assistant":
            from agentforge.llm.types import ToolCall

            calls = [
                ToolCall(id=c["id"], name=c["name"], arguments=c.get("arguments") or {})
                for c in (row.tool_calls or [])
            ]
            out.append(
                ChatMessage(
                    role="assistant",
                    content=row.content or None,
                    tool_calls=calls or None,
                )
            )
        elif row.role == "tool":
            out.append(
                ChatMessage(
                    role="tool",
                    content=row.content,
                    tool_call_id=row.tool_call_id,
                    name=row.name,
                )
            )
        elif row.role in ("user", "system"):
            out.append(ChatMessage(role=row.role, content=row.content))
    return out


class SessionMemory:
    """Loads recent history and compresses it with an LLM summary when oversized."""

    def __init__(
        self,
        db: Database,
        llm_factory: Callable[[], BaseLLM],
        *,
        threshold_tokens: int = 8000,
    ) -> None:
        self._db = db
        self._llm_factory = llm_factory
        self._threshold = threshold_tokens

    def load(self, session_id: str) -> list[ChatMessage]:
        rows = self._db.list_messages(session_id)
        return history_to_messages(rows)

    async def maybe_summarize(self, session_id: str, history: list[ChatMessage]) -> str:
        """If history exceeds the threshold, summarize older turns and persist it.

        Returns the (possibly existing) summary string. The summarized turns are
        kept in the DB (full audit) but excluded from the live prompt via the
        summary marker stored on the session row.
        """
        total = sum(estimate_tokens(m.content or "") for m in history)
        if total <= self._threshold:
            return ""

        half = max(1, len(history) // 2)
        older = history[:half]
        transcript = "\n".join(
            f"{m.role}: {(m.content or '')[:400]}" for m in older if m.role in ("user", "assistant")
        )
        if not transcript.strip():
            return ""

        from agentforge.agent.prompts import SUMMARY_PROMPT

        try:
            response = await self._llm_factory().complete(
                [ChatMessage(role="user", content=SUMMARY_PROMPT.format(history=transcript))],
                model=None,
            )
        except Exception:
            logger.exception("memory summarization failed; proceeding without summary")
            return ""

        summary = response.message.content or ""
        if summary:
            self._db.update_session(session_id, summary=summary)
            logger.info("session %s history compressed (%d chars summary)", session_id, len(summary))
        return summary
