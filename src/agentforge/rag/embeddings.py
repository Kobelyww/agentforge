"""Embedding backends: provider API or deterministic local hashing fallback."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod

import numpy as np

from agentforge.llm.base import BaseLLM
from agentforge.llm.types import ProviderError


class Embedder(ABC):
    name: str = "base"

    @abstractmethod
    def embed(self, texts: list[str]) -> np.ndarray:
        """Return an (n, dim) float32 matrix, L2-normalised per row."""

    async def embed_async(self, texts: list[str]) -> np.ndarray:
        import asyncio

        return await asyncio.to_thread(self.embed, texts)


class HashingEmbedder(Embedder):
    """Deterministic feature-hashing embeddings — zero dependencies, zero API keys.

    Character n-grams (2/3) plus jieba word tokens are hashed into fixed
    buckets (hashlib, NOT builtin hash — that is salted per process and would
    silently break vectors across restarts), TF-weighted, then L2-normalised.
    Quality is below a trained model but it makes semantic search work
    offline and gives reproducible CI behaviour.
    """

    def __init__(self, dim: int = 256) -> None:
        import jieba

        self.dim = dim
        self.name = f"hashing-{dim}"
        jieba.setLogLevel(60)  # silence the build-dict log line

    def _tokens(self, text: str) -> list[str]:
        import jieba

        text = text.lower().strip()
        tokens = list(jieba.lcut(text))
        # character 2/3-grams add robustness for names/typos
        compact = text.replace(" ", "")
        tokens += [compact[i : i + 2] for i in range(len(compact) - 1)]
        tokens += [compact[i : i + 3] for i in range(len(compact) - 2)]
        return [t for t in tokens if t.strip()]

    def _bucket(self, token: str) -> int:
        digest = hashlib.md5(token.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "little") % self.dim

    def embed(self, texts: list[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            counts: dict[int, int] = {}
            tokens = self._tokens(text)
            if not tokens:
                continue
            for token in tokens:
                counts[self._bucket(token)] = counts.get(self._bucket(token), 0) + 1
            row = matrix[i]
            for bucket, count in counts.items():
                row[bucket] = 1.0 + np.log(count)
            norm = float(np.linalg.norm(row))
            if norm > 0:
                row /= norm
        return matrix


class ProviderEmbedder(Embedder):
    """Embeddings via an OpenAI-compatible ``/embeddings`` endpoint."""

    def __init__(self, llm: BaseLLM, model: str | None = None) -> None:
        self._llm = llm
        self._model = model or llm.default_model
        self.name = f"provider:{llm.name}"

    def embed(self, texts: list[str]) -> np.ndarray:
        raise RuntimeError("use embed_async for ProviderEmbedder")

    async def embed_async(self, texts: list[str]) -> np.ndarray:
        try:
            vectors = await self._llm.embed(texts)
        except ProviderError:
            raise
        matrix = np.array(vectors, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return matrix / norms


def build_embedder(preferred: str, providers: list[BaseLLM]) -> Embedder:
    """``auto`` → first embedding-capable provider, else local hashing."""
    if preferred == "hashing":
        return HashingEmbedder()
    if preferred.startswith("provider:"):
        name = preferred.split(":", 1)[1]
        for llm in providers:
            if llm.name == name:
                return ProviderEmbedder(llm)
    if preferred == "auto":
        for llm in providers:
            if type(llm).__name__ == "OpenAICompatLLM":  # embedding-capable adapter
                return ProviderEmbedder(llm)
    return HashingEmbedder()
