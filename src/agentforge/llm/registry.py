"""Provider registry: construction, routing, and failover across providers."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence

from agentforge.config import ProviderSpec
from agentforge.llm.anthropic import AnthropicLLM
from agentforge.llm.base import BaseLLM
from agentforge.llm.mock import MockLLM
from agentforge.llm.openai_compat import OpenAICompatLLM
from agentforge.llm.types import ChatMessage, ProviderError, Routed, StreamEvent, ToolSpec

logger = logging.getLogger("agentforge.llm")


def build_provider(spec: ProviderSpec, *, max_retries: int = 2) -> BaseLLM:
    if spec.type == "mock":
        return MockLLM()
    if spec.type == "openai":
        return OpenAICompatLLM(spec, max_retries=max_retries)
    if spec.type == "anthropic":
        return AnthropicLLM(spec)
    raise ValueError(f"unknown provider type: {spec.type}")


class ProviderRegistry:
    """Holds every configured provider and routes with automatic failover.

    Routing model strings use ``provider/model`` syntax (e.g. ``glm/glm-4-plus``).
    On retryable failures (429/5xx/timeout) before any output was streamed, the
    next provider in the fallback chain is tried transparently.
    """

    def __init__(
        self,
        specs: Sequence[ProviderSpec],
        default_model: str = "mock/default",
        fallback_chain: Sequence[str] | None = None,
        *,
        max_retries: int = 2,
    ) -> None:
        self._providers: dict[str, BaseLLM] = {}
        for spec in specs:
            if not spec.enabled:
                continue
            if spec.type != "mock" and not spec.api_key.strip():
                logger.info("provider %s skipped (no api key configured)", spec.name)
                continue
            self._providers[spec.name] = build_provider(spec, max_retries=max_retries)

        if not self._providers:
            self._providers["mock"] = MockLLM()

        self.default_model = default_model if self._resolve_model(default_model) else next(
            iter(self._providers)
        )
        self.fallback_chain = [f for f in (fallback_chain or []) if f in self._providers]

    # ---- lookups ----
    def get(self, name: str) -> BaseLLM:
        provider = self._providers.get(name)
        if provider is None:
            raise ProviderError(f"provider {name!r} not configured or disabled", provider=name)
        return provider

    def has(self, name: str) -> bool:
        return name in self._providers

    def names(self) -> list[str]:
        return list(self._providers)

    def _resolve_model(self, model: str | None) -> tuple[str, str] | None:
        if not model:
            return None
        if "/" in model:
            provider_name, model_name = model.split("/", 1)
            if provider_name in self._providers:
                return provider_name, model_name
            return None
        # Bare model name: search providers in order for a default_model match.
        for name, p in self._providers.items():
            if p.default_model == model:
                return name, model
        return None

    def resolve(self, model: str | None = None) -> tuple[str, str]:
        resolved = self._resolve_model(model) or self._resolve_model(self.default_model)
        if resolved is None:
            name = next(iter(self._providers))
            return name, self._providers[name].default_model
        return resolved

    def public_specs(self) -> list[dict]:
        return [
            {
                "name": name,
                "model": p.default_model,
                "default": name == self.resolve()[0],
            }
            for name, p in self._providers.items()
        ]

    # ---- routing with failover ----
    def _candidates(self, model: str | None) -> list[tuple[str, str]]:
        primary = self.resolve(model)
        chain = [primary]
        for name in self.fallback_chain:
            if name != primary[0] and self.has(name):
                chain.append((name, self.get(name).default_model))
        return chain

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream from the preferred provider; failover before any output is emitted.

        Once a candidate has emitted even one event, a failure is raised
        instead of failing over — silently restarting mid-stream would
        duplicate output for the consumer.
        """
        candidates = self._candidates(model)
        last_error: ProviderError | None = None

        for i, (provider_name, model_name) in enumerate(candidates):
            provider = self.get(provider_name)
            try:
                iterator = provider.stream(
                    messages,
                    tools,
                    model=model_name or None,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                first = await iterator.__anext__()
            except ProviderError as exc:
                last_error = exc
                is_last = i == len(candidates) - 1
                if not exc.retryable or is_last:
                    raise
                logger.warning(
                    "provider %s failed before first token (%s), failing over to next candidate",
                    provider_name,
                    exc,
                )
                continue

            yield Routed(provider=provider_name, model=model_name)
            yield first
            async for event in iterator:
                yield event
            return

        raise last_error or ProviderError("no provider available")  # pragma: no cover

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()
