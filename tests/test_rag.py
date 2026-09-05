"""RAG: chunking, embedding determinism, hybrid retrieval."""


import numpy as np

from agentforge.rag.chunking import chunk_text
from agentforge.rag.embeddings import HashingEmbedder


def test_chunking_respects_max_chars():
    text = "段落一。" * 100 + "\n\n" + "段落二。" * 50
    chunks = chunk_text(text, max_chars=200, overlap=30)
    assert all(len(c.text) <= 220 for c in chunks)  # small tolerance for heading prefix
    assert len(chunks) >= 3


def test_chunking_keeps_headings():
    body = "本节讲述振动判据与处置流程。" * 8  # long enough to force its own chunk
    md = (
        f"# 轴承标准\n{body}\n\n# 案例一\n{body}\n\n# 案例二\n{body}"
    )
    chunks = chunk_text(md, max_chars=200, overlap=0)
    assert any(c.text.startswith("# 轴承标准") for c in chunks)
    headings = {c.meta.get("heading") for c in chunks}
    assert {"案例一", "案例二"} <= headings


def test_chunking_empty():
    assert chunk_text("   \n  ") == []


def test_hashing_embedder_deterministic():
    e1 = HashingEmbedder(dim=128).embed(["设备振动诊断"])
    e2 = HashingEmbedder(dim=128).embed(["设备振动诊断"])
    assert np.allclose(e1, e2)  # NOT salted per-process

    a = HashingEmbedder(dim=128).embed(["轴承外圈磨损"])[0]
    b = HashingEmbedder(dim=128).embed(["轴承内圈磨损"])[0]
    c = HashingEmbedder(dim=128).embed(["今天天气不错"])[0]
    assert float(a @ b) > float(a @ c)  # related texts closer than unrelated


async def test_hybrid_retrieval_end_to_end(settings):
    from agentforge.persistence.db import Database
    from agentforge.rag.embeddings import build_embedder
    from agentforge.rag.retriever import Retriever

    db = Database(settings.db_url, settings.data_dir)
    embedder = build_embedder("hashing", [])
    retriever = Retriever(db, embedder, settings)

    doc, chunks = await retriever.ingest(
        "6205 轴承外圈故障特征频率为 176.85 Hz，超过 4.5 mm/s 需要停机检查。",
        name="manual.md",
    )
    assert chunks == 1

    for mode in ("bm25", "vector", "hybrid"):
        hits = await retriever.search("轴承外圈 特征频率", k=3, mode=mode)
        assert hits, f"mode {mode} returned no hits"
        assert hits[0].document_name == "manual.md"
        assert hits[0].score > 0

    assert await retriever.delete_document(doc.id)
    assert await retriever.search("轴承外圈", k=3) == []
