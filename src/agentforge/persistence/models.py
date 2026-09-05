"""SQLAlchemy ORM models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import BLOB, JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def uid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Session(Base):
    """A conversation session."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(200), default="新会话")
    provider: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    messages: Mapped[list[Message]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Message.seq",
    )


class Message(Base):
    """One message (user / assistant / tool) inside a session."""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_session_seq", "session_id", "seq"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | tool | system
    content: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    session: Mapped[Session] = relationship(back_populates="messages")


class Document(Base):
    """An ingested knowledge-base document."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(500), default="")
    mime: Mapped[str] = mapped_column(String(100), default="text/plain")
    size: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Chunk(Base):
    """A retrievable chunk with optional dense embedding."""

    __tablename__ = "chunks"
    __table_args__ = (Index("ix_chunks_document", "document_id", "seq"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[bytes | None] = mapped_column(BLOB, nullable=True)
    embedder: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class ToolInvocation(Base):
    """Audit record of every tool execution."""

    __tablename__ = "tool_invocations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    session_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    tool: Mapped[str] = mapped_column(String(64))
    args: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok | error | timeout | denied
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WorkOrder(Base):
    """Structured maintenance work order produced (and schema-validated) by the agent."""

    __tablename__ = "work_orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    code: Mapped[str] = mapped_column(String(24), unique=True)  # human-readable WO-xxxxxx
    session_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    equipment_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(300))
    fault_type: Mapped[str] = mapped_column(String(120))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    priority: Mapped[str] = mapped_column(String(8))  # P1..P4
    actions: Mapped[list] = mapped_column(JSON, default=list)
    parts: Mapped[list] = mapped_column(JSON, default=list)
    estimated_hours: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | in_progress | done
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
