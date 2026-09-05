"""Knowledge-base seeding for the ForgeOps vertical."""

from __future__ import annotations

import logging
from pathlib import Path

from agentforge.rag.retriever import Retriever

logger = logging.getLogger("agentforge.forgeops")

_DATA_DIR = Path(__file__).resolve().parent / "data"


async def seed_knowledge_base(retriever: Retriever, *, force: bool = False) -> int:
    """Ingest the packaged equipment manuals / case book into the RAG store.

    Idempotent: skips when the knowledge base already has documents unless
    ``force`` is set. Returns the number of newly ingested documents.
    """
    existing = retriever._db.list_documents()
    if existing and not force:
        return 0

    docs_dir = _DATA_DIR / "docs"
    ingested = 0
    for path in sorted(docs_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        await retriever.ingest(text, name=path.name, source="forgeops-seed", mime="text/markdown")
        ingested += 1
    logger.info("ForgeOps knowledge base seeded: %d documents", ingested)
    return ingested
