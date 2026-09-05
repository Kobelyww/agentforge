"""Document chunking: structure-aware splitting with overlap."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)
_PARA_SPLIT_RE = re.compile(r"\n\s*\n")


@dataclass
class TextChunk:
    text: str
    seq: int
    meta: dict = field(default_factory=dict)


def _split_long(text: str, max_chars: int, overlap: int) -> list[str]:
    """Hard-split a long paragraph on sentence boundaries, falling back to chars."""
    sentences = re.split(r"(?<=[。！？.!?\n])", text)
    pieces: list[str] = []
    buf = ""
    for sentence in sentences:
        if len(buf) + len(sentence) > max_chars and buf:
            pieces.append(buf.strip())
            tail = buf[-overlap:] if overlap > 0 else ""
            buf = tail + sentence
        else:
            buf += sentence
    if buf.strip():
        pieces.append(buf.strip())
    return pieces


def chunk_text(
    text: str,
    *,
    max_chars: int = 800,
    overlap: int = 100,
    source: str = "",
) -> list[TextChunk]:
    """Split *text* into overlapping chunks, respecting markdown structure.

    Strategy: split on headings first (each section keeps its heading line),
    then on blank lines, then hard-split any paragraph still over budget.
    """
    text = text.strip()
    if not text:
        return []
    max_chars = max(max_chars, 100)
    overlap = max(0, min(overlap, max_chars // 2))

    # Build (paragraph, heading_context) units.
    sections: list[str] = []
    if _HEADING_RE.search(text):
        # Split on heading lines, keeping the heading attached to its section.
        lines = text.split("\n")
        current: list[str] = []
        for line in lines:
            if _HEADING_RE.match(line) and current:
                sections.append("\n".join(current))
                current = [line]
            else:
                current.append(line)
        if current:
            sections.append("\n".join(current))
    else:
        sections = [text]

    units: list[tuple[str, str, str]] = []
    for section in sections:
        heading = ""
        heading_raw = ""
        body = section
        first_line = section.split("\n", 1)[0]
        if _HEADING_RE.match(first_line):
            heading = first_line.lstrip("# ").strip()
            heading_raw = first_line
            body = section[len(first_line):].strip()
        for para in _PARA_SPLIT_RE.split(body):
            para = para.strip()
            if para:
                units.append((para, heading, heading_raw))

    chunks: list[TextChunk] = []
    buf = ""
    buf_heading = ""
    for para, heading, heading_raw in units:
        prefix = heading_raw or heading
        pieces = _split_long(para, max_chars - len(prefix) - 2, overlap)
        for piece in pieces:
            candidate = f"{prefix}\n{piece}" if prefix else piece
            if buf and len(buf) + len(candidate) + 2 > max_chars:
                chunks.append(TextChunk(text=buf, seq=len(chunks), meta={"heading": buf_heading}))
                buf = ""
            if not buf:
                buf_heading = heading
            if len(candidate) > max_chars:
                # Still over budget even alone: emit as its own chunk.
                if buf:
                    chunks.append(TextChunk(text=buf, seq=len(chunks), meta={"heading": buf_heading}))
                    buf = ""
                chunks.append(
                    TextChunk(text=candidate[:max_chars], seq=len(chunks),
                              meta={"heading": heading, "truncated": True})
                )
            else:
                buf = f"{buf}\n\n{candidate}" if buf else candidate
    if buf.strip():
        chunks.append(TextChunk(text=buf, seq=len(chunks), meta={"heading": buf_heading}))

    for c in chunks:
        if source:
            c.meta["source"] = source
    return chunks
