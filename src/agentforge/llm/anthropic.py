"""Anthropic Messages API adapter (pure httpx)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from agentforge.config import ProviderSpec
from agentforge.llm.base import BaseLLM
from agentforge.llm.types import (
    ChatMessage,
    Finish,
    ProviderError,
    StreamEvent,
    TextDelta,
    ToolCallDelta,
    ToolSpec,
    Usage,
)

_STOP_REASON = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls", "max_tokens": "length"}


class AnthropicLLM(BaseLLM):
    def __init__(self, spec: ProviderSpec, *, timeout_seconds: float = 120.0) -> None:
        self.name = spec.name
        self.default_model = spec.model
        self._client = httpx.AsyncClient(
            base_url=spec.base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            headers={
                "x-api-key": spec.api_key,
                "anthropic-version": "2023-06-01",
                **spec.headers,
            },
        )

    @staticmethod
    def _to_api_messages(messages: Sequence[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
        """Split system prompt out; map assistant tool_use / user tool_result blocks."""
        system_parts: list[str] = []
        api_messages: list[dict[str, Any]] = []

        def _append(role: str, blocks: list[dict[str, Any]]) -> None:
            # Merge consecutive same-role messages (Anthropic requires alternation).
            if api_messages and api_messages[-1]["role"] == role:
                api_messages[-1]["content"].extend(blocks)
            else:
                api_messages.append({"role": role, "content": blocks})

        for m in messages:
            if m.role == "system":
                if m.content:
                    system_parts.append(m.content)
            elif m.role == "tool":
                _append(
                    "user",
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.tool_call_id,
                            "content": m.content or "",
                        }
                    ],
                )
            elif m.role == "assistant" and m.tool_calls:
                blocks: list[dict[str, Any]] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for call in m.tool_calls:
                    blocks.append(
                        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                    )
                _append("assistant", blocks)
            else:
                _append(m.role, [{"type": "text", "text": m.content or ""}])
        return "\n\n".join(system_parts), api_messages

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        system, api_messages = self._to_api_messages(messages)
        body: dict[str, Any] = {
            "model": model or self.default_model,
            "max_tokens": max_tokens or 4096,
            "messages": api_messages,
            "stream": True,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]
        if temperature is not None:
            body["temperature"] = temperature

        tool_meta: dict[int, dict[str, str]] = {}
        usage = Usage()

        async with self._client.stream("POST", "/v1/messages", json=body) as response:
            if response.status_code != 200:
                text = (await response.aread()).decode("utf-8", errors="replace")[:500]
                raise ProviderError(
                    f"{self.name} HTTP {response.status_code}: {text}",
                    provider=self.name,
                    status=response.status_code,
                    retryable=response.status_code in (429, 500, 502, 503, 504, 529),
                )
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue

                etype = event.get("type")
                if etype == "message_start":
                    usage.prompt_tokens = (
                        event.get("message", {}).get("usage", {}).get("input_tokens", 0)
                    )
                elif etype == "content_block_start":
                    block = event.get("content_block", {})
                    index = event.get("index", 0)
                    if block.get("type") == "tool_use":
                        tool_meta[index] = {"id": block.get("id", ""), "name": block.get("name", "")}
                        yield ToolCallDelta(index=index, id=block.get("id"), name=block.get("name"))
                elif etype == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield TextDelta(delta.get("text", ""))
                    elif delta.get("type") == "input_json_delta":
                        yield ToolCallDelta(
                            index=event.get("index", 0),
                            arguments_delta=delta.get("partial_json", ""),
                        )
                elif etype == "message_delta":
                    stop = event.get("delta", {}).get("stop_reason")
                    usage.completion_tokens = event.get("usage", {}).get("output_tokens", 0)
                    if stop:
                        yield Finish(finish_reason=_STOP_REASON.get(stop, "stop"), usage=usage)
                elif etype == "error":
                    err = event.get("error", {})
                    raise ProviderError(
                        f"{self.name} stream error: {err.get('message', event)}",
                        provider=self.name,
                        retryable=err.get("type") in ("overloaded_error", "api_error"),
                    )

    async def aclose(self) -> None:
        await self._client.aclose()
