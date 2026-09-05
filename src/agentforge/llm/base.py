"""Abstract provider contract shared by every adapter."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from agentforge.llm.types import (
    ChatMessage,
    Finish,
    ProviderError,
    StreamEvent,
    TextDelta,
    ToolCallDelta,
    ToolSpec,
    Usage,
    accumulate_tool_calls,
)


@dataclass
class LLMResponse:
    """Result of a non-streaming completion (aggregated from the stream)."""

    message: ChatMessage
    usage: Usage
    finish_reason: str
    provider: str
    model: str


class BaseLLM(ABC):
    """A chat LLM endpoint.

    Subclasses implement :meth:`stream`; :meth:`complete` aggregates it, so
    every adapter only maintains a single streaming code path.
    """

    name: str = "base"
    default_model: str = ""

    @abstractmethod
    def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Yield TextDelta / ToolCallDelta / Finish events."""

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        text_parts: list[str] = []
        tool_deltas: list[ToolCallDelta] = []
        usage = Usage()
        finish_reason = "stop"

        async for event in self.stream(
            messages, tools, model=model, temperature=temperature, max_tokens=max_tokens
        ):
            match event:
                case TextDelta(text=text):
                    text_parts.append(text)
                case ToolCallDelta():
                    tool_deltas.append(event)
                case Finish(finish_reason=reason, usage=u):
                    finish_reason = reason
                    if u:
                        usage = u
                case _:
                    pass

        tool_calls = accumulate_tool_calls(tool_deltas) or None
        content = "".join(text_parts)
        if usage.completion_tokens == 0:
            from agentforge.llm.types import estimate_tokens

            usage.completion_tokens = estimate_tokens(content)
        return LLMResponse(
            message=ChatMessage(
                role="assistant",
                content=content or None,
                tool_calls=tool_calls,
            ),
            usage=usage,
            finish_reason=finish_reason,
            provider=self.name,
            model=model or self.default_model,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise ProviderError(f"provider {self.name!r} does not support embeddings")

    async def aclose(self) -> None:  # noqa: B027 - optional lifecycle hook
        pass
