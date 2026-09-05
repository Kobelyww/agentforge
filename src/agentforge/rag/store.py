"""In-memory BM25 index + vector index over SQLite-stored chunks."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from agentforge.persistence.db import Database
from agentforge.persistence.models import Chunk

logger = logging.getLogger("agentforge.rag")

def _tokenize(text: str) -> list[str]:
    import jieba

    jieba.setLogLevel(60)
    return [t for t in jieba.lcut(text.lower()) if t.strip()]


@dataclass
class ScoredChunk:
    chunk_id: str
    document_id: str
    document_name: str
    text: str
    score: float
    seq: int


class BM25Index:
    """Okapi BM25 (k1=1.5, b=0.75) over jieba tokens, rebuilt from the DB."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._docs: list[tuple[str, str, str, int]] = []  # (chunk_id, doc_id, doc_name, seq)
        self._texts: list[str] = []
        self._tf: list[dict[str, int]] = []
        self._lengths: list[int] = []
        self._df: dict[str, int] = {}
        self._avgdl = 0.0

    def rebuild(self, db: Database) -> int:
        self._docs.clear()
        self._texts.clear()
        self._tf.clear()
        self._lengths.clear()
        self._df.clear()

        chunks = db.list_chunks()
        doc_names: dict[str, str] = {d.id: d.name for d in db.list_documents()}
        total_len = 0
        for chunk in chunks:
            tokens = _tokenize(chunk.text)
            tf: dict[str, int] = {}
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
            self._docs.append((chunk.id, chunk.document_id, doc_names.get(chunk.document_id, "?"), chunk.seq))
            self._texts.append(chunk.text)
            self._tf.append(tf)
            self._lengths.append(len(tokens))
            for token in tf:
                self._df[token] = self._df.get(token, 0) + 1
            total_len += len(tokens)
        self._avgdl = (total_len / len(chunks)) if chunks else 0.0
        return len(chunks)

    def search(self, query: str, k: int = 5) -> list[tuple[int, float]]:
        tokens = _tokenize(query)
        if not tokens or not self._docs:
            return []
        n = len(self._docs)
        scores = [0.0] * n
        for token in tokens:
            df = self._df.get(token, 0)
            if df == 0:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for i, tf in enumerate(self._tf):
                freq = tf.get(token, 0)
                if freq == 0:
                    continue
                # doc length via tf dict size would be wrong; store lengths instead
                scores[i] += idf * self._weight(i, freq)
        ranked = sorted(enumerate(scores), key=lambda pair: pair[1], reverse=True)[:k]
        return [(i, s) for i, s in ranked if s > 0]

    def _weight(self, i: int, freq: int) -> float:
        # length stored per doc
        return (
            freq * (self.k1 + 1)
            / (freq + self.k1 * (1 - self.b + self.b * self._lengths[i] / max(self._avgdl, 1e-9)))
        )


class VectorIndex:
    """Dense vector search with an in-memory matrix cache over chunk BLOBs."""

    def __init__(self) -> None:
        self._ids: list[str] = []
        self._matrix: np.ndarray | None = None
        self._meta: list[tuple[str, str, str, int]] = []  # chunk_id, doc_id, doc_name, seq

    def rebuild(self, chunks: list[tuple[Chunk, str]]) -> int:
        """chunks: list of (chunk_row, document_name)."""
        self._ids.clear()
        self._meta.clear()
        vectors: list[np.ndarray] = []
        for chunk, doc_name in chunks:
            if chunk.embedding is None:
                continue
            vec = np.frombuffer(chunk.embedding, dtype=np.float32)
            vectors.append(vec)
            self._ids.append(chunk.id)
            self._meta.append((chunk.id, chunk.document_id, doc_name, chunk.seq))
        self._matrix = np.vstack(vectors) if vectors else None
        return len(self._ids)

    def search(self, query_vec: np.ndarray, k: int = 5) -> list[tuple[int, float]]:
        if self._matrix is None or not len(self._ids):
            return []
        q = query_vec / (np.linalg.norm(query_vec) or 1.0)
        sims = self._matrix @ q
        top = np.argsort(-sims)[:k]
        return [(int(i), float(sims[int(i)])) for i in top if sims[int(i)] > 0]
