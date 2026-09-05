"""Retriever facade: ingest + hybrid search (RRF over vector and BM25)."""

from __future__ import annotations

import asyncio
import logging
import threading

import numpy as np

from agentforge.config import Settings
from agentforge.llm.types import estimate_tokens
from agentforge.persistence.db import Database
from agentforge.persistence.models import Chunk, Document
from agentforge.rag.chunking import chunk_text
from agentforge.rag.embeddings import Embedder
from agentforge.rag.store import BM25Index, ScoredChunk, VectorIndex

logger = logging.getLogger("agentforge.rag")


class Retriever:
    """Owns the knowledge base: ingest documents and answer search queries.

    Hybrid retrieval fuses the two rankings with Reciprocal Rank Fusion
    (score = Σ 1/(60+rank)) — BM25 catches exact terms, vectors catch
    paraphrases, and RRF needs no score calibration between the two.
    Both indexes are rebuilt from the same ordered `list_chunks()` snapshot,
    so positional indices stay aligned.
    """

    def __init__(self, db: Database, embedder: Embedder, settings: Settings) -> None:
        self._db = db
        self._embedder = embedder
        self._settings = settings
        self._bm25 = BM25Index()
        self._vector = VectorIndex()
        self._lock = threading.Lock()
        self.mode = settings.rag.search_mode
        self.rebuild_indexes()

    # ---- indexing ----
    def rebuild_indexes(self) -> None:
        with self._lock:
            n = self._bm25.rebuild(self._db)
            names = {d.id: d.name for d in self._db.list_documents()}
            rows = self._db.list_chunks()
            self._vector.rebuild([(chunk, names.get(chunk.document_id, "?")) for chunk in rows])
        logger.info(
            "RAG indexes rebuilt: %d chunks (embedder=%s, mode=%s)",
            n, self._embedder.name, self.mode,
        )

    # ---- ingest ----
    async def ingest(
        self,
        text: str,
        *,
        name: str,
        source: str = "",
        mime: str = "text/plain",
    ) -> tuple[Document, int]:
        pieces = chunk_text(
            text,
            max_chars=self._settings.rag.chunk_size,
            overlap=self._settings.rag.chunk_overlap,
            source=name,
        )
        if not pieces:
            raise ValueError("document is empty after chunking")

        vectors = await self._embedder.embed_async([p.text for p in pieces])

        def _persist() -> Document:
            doc = self._db.add_document(
                Document(
                    name=name,
                    source=source,
                    mime=mime,
                    size=len(text.encode("utf-8")),
                    chunk_count=len(pieces),
                )
            )
            rows = [
                Chunk(
                    document_id=doc.id,
                    seq=p.seq,
                    text=p.text,
                    tokens=estimate_tokens(p.text),
                    meta=p.meta,
                    embedding=vectors[p.seq].tobytes(),
                    embedder=self._embedder.name,
                )
                for p in pieces
            ]
            self._db.add_chunks(rows)
            return doc

        doc = await asyncio.to_thread(_persist)
        self.rebuild_indexes()
        return doc, len(pieces)

    async def delete_document(self, document_id: str) -> bool:
        deleted = await asyncio.to_thread(self._db.delete_document, document_id)
        if deleted:
            self.rebuild_indexes()
        return deleted

    # ---- search ----
    async def search(
        self, query: str, *, k: int | None = None, mode: str | None = None
    ) -> list[ScoredChunk]:
        k = k or self._settings.rag.top_k
        mode = mode or self.mode

        def _bm25() -> list[tuple[int, float]]:
            return self._bm25.search(query, k=k)

        def _vector() -> list[tuple[int, float]]:
            qvec = self._embedder.embed([query])[0]
            return self._vector.search(np.asarray(qvec, dtype=np.float32), k=k)

        loop = asyncio.get_running_loop()
        bm25_hits: list[tuple[int, float]] = []
        vec_hits: list[tuple[int, float]] = []
        if mode in ("bm25", "hybrid"):
            bm25_hits = await loop.run_in_executor(None, _bm25)
        if mode in ("vector", "hybrid"):
            vec_hits = await loop.run_in_executor(None, _vector)

        if mode == "bm25":
            return [self._chunk_at(i, s) for i, s in bm25_hits]
        if mode == "vector":
            return [self._chunk_at(i, s) for i, s in vec_hits]

        # hybrid: Reciprocal Rank Fusion
        rrf: dict[int, float] = {}
        for ranking in (bm25_hits, vec_hits):
            for rank, (index, _score) in enumerate(ranking):
                rrf[index] = rrf.get(index, 0.0) + 1.0 / (60.0 + rank + 1)
        fused = sorted(rrf.items(), key=lambda pair: pair[1], reverse=True)[:k]
        return [self._chunk_at(i, s) for i, s in fused]

    # Both indexes are built from the same ordered DB snapshot, so a single
    # lookup path (BM25 doc store) resolves metadata for either ranking.
    def _chunk_at(self, index: int, score: float) -> ScoredChunk:
        chunk_id, doc_id, doc_name, seq = self._bm25._docs[index]
        return ScoredChunk(
            chunk_id=chunk_id,
            document_id=doc_id,
            document_name=doc_name,
            text=self._bm25._texts[index],
            score=score,
            seq=seq,
        )


__all__ = ["Retriever"]
