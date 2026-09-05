"""Context-window management: token budgeting, history trimming with pairing integrity."""

from __future__ import annotations

from agentforge.llm.types import ChatMessage, estimate_tokens


class ContextWindow:
    """Fits a message list into a token budget.

    Trimming rules (in order):
    1. Never drop the system prompt or the most recent user turn.
    2. Drop the oldest turns first.
    3. Never leave an orphan ``tool`` result: it is dropped together with the
       assistant tool-call message that requested it (most APIs reject orphans).
    """

    def __init__(self, budget_tokens: int) -> None:
        self.budget = budget_tokens

    def message_tokens(self, message: ChatMessage) -> int:
        total = estimate_tokens(message.content or "")
        for call in message.tool_calls or []:
            total += estimate_tokens(call.name) + estimate_tokens(str(call.arguments))
        return total

    def total_tokens(self, messages: list[ChatMessage]) -> int:
        return sum(self.message_tokens(m) for m in messages)

    def fit(
        self,
        messages: list[ChatMessage],
        *,
        summary: str = "",
    ) -> list[ChatMessage]:
        """Return a budget-fitting copy, prepending the rolled-up summary."""
        system = [m for m in messages if m.role == "system"]
        rest = [m for m in messages if m.role != "system"]

        prefix: list[ChatMessage] = []
        if summary:
            prefix.append(
                ChatMessage(
                    role="system",
                    content=f"此前对话的摘要（较早内容已被压缩）：{summary}",
                )
            )

        budget = self.budget - self.total_tokens(system) - self.total_tokens(prefix)
        # Keep at least the last user turn even if over budget (degrade gracefully).
        if budget <= 0:
            return system + prefix + rest[-1:]

        keep_from = 0
        running = self.total_tokens(rest)
        for i in range(len(rest)):
            if running <= budget:
                break
            running -= self.message_tokens(rest[i])
            keep_from = i + 1
        kept = rest[keep_from:]

        # Enforce tool pairing: a leading `tool` message whose assistant
        # tool-call partner was trimmed must be dropped as well.
        while kept and kept[0].role == "tool":
            kept = kept[1:]
        return system + prefix + kept


def build_llm_messages(
    system_prompt: str,
    history: list[ChatMessage],
    *,
    summary: str = "",
    budget_tokens: int = 12000,
) -> list[ChatMessage]:
    """Assemble the final prompt: system + (summary) + fitted history."""
    window = ContextWindow(budget_tokens)
    messages = [ChatMessage(role="system", content=system_prompt), *history]
    return window.fit(messages, summary=summary)
