"""Knowledge-base document routes: upload, list, delete, search."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from agentforge.server.auth import require_api_key

router = APIRouter(prefix="/api/documents", dependencies=[Depends(require_api_key)])


class IngestTextRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=2_000_000)
    source: str = ""


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=5, ge=1, le=20)
    mode: str | None = Field(default=None, description="hybrid | vector | bm25")


@router.post("", status_code=201)
async def upload_document(request: Request):
    """Multipart file upload or JSON {name, text}."""
    state = request.app.state.state
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("multipart/form-data"):
        try:
            form = await request.form()
        except Exception:
            raise HTTPException(400, "invalid multipart body") from None
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(400, "multipart field 'file' is required")
        raw = await upload.read()
        if len(raw) > 5 * 1024 * 1024:
            raise HTTPException(413, "file too large (max 5 MB)")
        text = raw.decode("utf-8", errors="replace")
        name = upload.filename or "upload.txt"
        mime = upload.content_type or "text/plain"
        if not text.strip():
            raise HTTPException(400, "file is empty")
        doc, chunks = await state.retriever.ingest(text, name=name, source="upload", mime=mime)
        return {"document": _doc_dict(doc), "chunks": chunks}

    body = IngestTextRequest(**(await request.json()))
    doc, chunks = await state.retriever.ingest(
        body.text, name=body.name, source=body.source or "api"
    )
    return {"document": _doc_dict(doc), "chunks": chunks}


@router.get("")
async def list_documents(request: Request):
    state = request.app.state.state
    docs = await asyncio.to_thread(state.db.list_documents)
    return [_doc_dict(d) for d in docs]


@router.delete("/{document_id}", status_code=204)
async def delete_document(document_id: str, request: Request):
    state = request.app.state.state
    deleted = await state.retriever.delete_document(document_id)
    if not deleted:
        raise HTTPException(404, "document not found")


@router.post("/search")
async def search(request: Request):
    state = request.app.state.state
    body = SearchRequest(**(await request.json()))
    try:
        results = await state.retriever.search(body.query, k=body.k, mode=body.mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "query": body.query,
        "mode": state.retriever.mode,
        "results": [
            {
                "chunk_id": r.chunk_id,
                "document_id": r.document_id,
                "document_name": r.document_name,
                "seq": r.seq,
                "score": round(r.score, 4),
                "text": r.text,
            }
            for r in results
        ],
    }


def _doc_dict(doc) -> dict:
    return {
        "id": doc.id,
        "name": doc.name,
        "source": doc.source,
        "mime": doc.mime,
        "size": doc.size,
        "chunk_count": doc.chunk_count,
        "created_at": doc.created_at,
    }
