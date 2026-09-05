"""OpenAI-compatible chat-completions adapter (pure httpx, no vendor SDK).

Works with OpenAI, GLM (Zhipu), DeepSeek, Kimi, Qwen, vLLM, Ollama and
Huawei Cloud ModelArts MaaS — anything speaking ``/chat/completions``.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Iterator, Sequence
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


class OpenAICompatLLM(BaseLLM):
    def __init__(
        self,
        spec: ProviderSpec,
        *,
        max_retries: int = 2,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.name = spec.name
        self.spec = spec
        self.default_model = spec.model
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=spec.base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            headers={"Authorization": f"Bearer {spec.api_key}", **spec.headers},
        )

    # ---- request mapping ----
    @staticmethod
    def _message_to_dict(message: ChatMessage) -> dict[str, Any]:
        if message.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content or "",
            }
        if message.role == "assistant" and message.tool_calls:
            return {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in message.tool_calls
                ],
            }
        out: dict[str, Any] = {"role": message.role, "content": message.content or ""}
        if message.name:
            out["name"] = message.name
        return out

    @staticmethod
    def _build_body(
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        model: str,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [OpenAICompatLLM._message_to_dict(m) for m in messages],
            "stream": True,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        return body

    # ---- streaming ----
    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        model = model or self.default_model
        body = self._build_body(messages, tools, model, temperature, max_tokens)

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            emitted = False
            try:
                async for event in self._stream_once(body):
                    emitted = True
                    yield event
                return
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = ProviderError(
                    f"network error talking to {self.name}: {exc}",
                    provider=self.name,
                    retryable=not emitted,
                )
            except ProviderError as exc:
                exc.retryable = exc.retryable and not emitted
                last_error = exc
            if not last_error.retryable or attempt >= self.max_retries:
                raise last_error  # type: ignore[misc]
            # Exponential backoff with jitter; never retry after partial output.
            await asyncio.sleep(min(0.5 * 2**attempt, 8.0) + random.uniform(0, 0.3))

    async def _stream_once(self, body: dict[str, Any]) -> AsyncIterator[StreamEvent]:
        async with self._client.stream("POST", "/chat/completions", json=body) as response:
            if response.status_code != 200:
                text = (await response.aread()).decode("utf-8", errors="replace")[:500]
                raise ProviderError(
                    f"{self.name} HTTP {response.status_code}: {text}",
                    provider=self.name,
                    status=response.status_code,
                    retryable=response.status_code in (429, 500, 502, 503, 504),
                )

            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                for event in self._parse_chunk(chunk):
                    yield event

    @staticmethod
    def _parse_chunk(chunk: dict[str, Any]) -> Iterator[StreamEvent]:
        if chunk.get("usage"):
            # Some providers report usage in a final chunk without choices.
            usage = chunk["usage"]
            yield Finish(
                finish_reason="stop",
                usage=Usage(
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                ),
            )
            return
        choices = chunk.get("choices") or []
        if not choices:
            return
        choice = choices[0]
        delta = choice.get("delta") or {}
        if content := delta.get("content"):
            yield TextDelta(content)
        for tc in delta.get("tool_calls") or []:
            fn = tc.get("function") or {}
            yield ToolCallDelta(
                index=tc.get("index", 0),
                id=tc.get("id") or None,
                name=fn.get("name") or None,
                arguments_delta=fn.get("arguments") or "",
            )
        if choice.get("finish_reason"):
            yield Finish(finish_reason=_map_finish(choice["finish_reason"]))

    # ---- embeddings ----
    async def embed(self, texts: list[str]) -> list[list[float]]:
        model = self.spec.model
        response = await self._client.post(
            "/embeddings", json={"model": model, "input": texts}
        )
        if response.status_code != 200:
            raise ProviderError(
                f"{self.name} embeddings HTTP {response.status_code}: {response.text[:300]}",
                provider=self.name,
                status=response.status_code,
                retryable=response.status_code in (429, 503),
            )
        data = response.json()["data"]
        return [item["embedding"] for item in sorted(data, key=lambda d: d["index"])]

    async def aclose(self) -> None:
        await self._client.aclose()


def _map_finish(reason: str) -> str:
    return {"tool_calls": "tool_calls", "function_call": "tool_calls", "length": "length"}.get(
        reason, "stop"
    )
