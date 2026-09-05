"""Database engine/session management + repositories."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, desc, func, select
from sqlalchemy.orm import Session as ORMSession
from sqlalchemy.orm import sessionmaker

from agentforge.persistence.models import (
    Base,
    Chunk,
    Document,
    Message,
    ToolInvocation,
    WorkOrder,
)
from agentforge.persistence.models import (
    Session as ChatSession,
)


class Database:
    """Thin wrapper around a sync SQLAlchemy engine.

    SQLite with WAL keeps writes fast; call sites that matter use
    ``asyncio.to_thread`` so the event loop is never blocked for long.
    """

    def __init__(self, url: str, data_dir: Path) -> None:
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        if not url:
            url = f"sqlite:///{(data_dir / 'agentforge.db').as_posix()}"
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
        Base.metadata.create_all(self.engine)
        if url.startswith("sqlite"):
            with self.engine.connect() as conn:
                conn.exec_driver_sql("PRAGMA journal_mode=WAL")
                conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        self._factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._lock = threading.Lock()

    @contextmanager
    def session(self) -> Iterator[ORMSession]:
        with self._lock, self._factory() as db:
            yield db

    # ---- sessions ----
    def create_session(self, title: str = "新会话", provider: str = "", model: str = "") -> ChatSession:
        with self.session() as db:
            s = ChatSession(title=title, provider=provider, model=model)
            db.add(s)
            db.commit()
            return s

    def get_session(self, session_id: str) -> ChatSession | None:
        with self.session() as db:
            return db.get(ChatSession, session_id)

    def list_sessions(self, limit: int = 100) -> list[ChatSession]:
        with self.session() as db:
            return list(
                db.scalars(
                    select(ChatSession).order_by(desc(ChatSession.updated_at)).limit(limit)
                )
            )

    def update_session(self, session_id: str, **fields: object) -> ChatSession | None:
        with self.session() as db:
            s = db.get(ChatSession, session_id)
            if s is None:
                return None
            for k, v in fields.items():
                setattr(s, k, v)
            db.commit()
            return s

    def delete_session(self, session_id: str) -> bool:
        with self.session() as db:
            s = db.get(ChatSession, session_id)
            if s is None:
                return False
            db.delete(s)
            db.commit()
            return True

    # ---- messages ----
    def next_seq(self, session_id: str) -> int:
        with self.session() as db:
            return (db.scalar(
                select(func.coalesce(func.max(Message.seq), -1)).where(
                    Message.session_id == session_id
                )
            ) or -1) + 1

    def add_message(self, msg: Message) -> Message:
        with self.session() as db:
            db.add(msg)
            db.commit()
            return msg

    def list_messages(self, session_id: str) -> list[Message]:
        with self.session() as db:
            return list(
                db.scalars(
                    select(Message)
                    .where(Message.session_id == session_id)
                    .order_by(Message.seq)
                )
            )

    def count_messages(self, session_id: str) -> int:
        with self.session() as db:
            return db.scalar(
                select(func.count()).select_from(Message).where(Message.session_id == session_id)
            ) or 0

    # ---- documents / chunks ----
    def add_document(self, doc: Document) -> Document:
        with self.session() as db:
            db.add(doc)
            db.commit()
            return doc

    def get_document(self, doc_id: str) -> Document | None:
        with self.session() as db:
            return db.get(Document, doc_id)

    def list_documents(self) -> list[Document]:
        with self.session() as db:
            return list(db.scalars(select(Document).order_by(desc(Document.created_at))))

    def delete_document(self, doc_id: str) -> bool:
        with self.session() as db:
            doc = db.get(Document, doc_id)
            if doc is None:
                return False
            db.delete(doc)
            db.commit()
            return True

    def add_chunks(self, chunks: list[Chunk]) -> None:
        with self.session() as db:
            db.add_all(chunks)
            db.commit()

    def list_chunks(self, document_id: str | None = None) -> list[Chunk]:
        with self.session() as db:
            stmt = select(Chunk).order_by(Chunk.document_id, Chunk.seq)
            if document_id:
                stmt = stmt.where(Chunk.document_id == document_id)
            return list(db.scalars(stmt))

    def count_chunks(self) -> int:
        with self.session() as db:
            return db.scalar(select(func.count()).select_from(Chunk)) or 0

    # ---- tool audit ----
    def add_tool_invocation(self, inv: ToolInvocation) -> None:
        with self.session() as db:
            db.add(inv)
            db.commit()

    def list_tool_invocations(self, session_id: str | None = None, limit: int = 500) -> list[ToolInvocation]:
        with self.session() as db:
            stmt = select(ToolInvocation).order_by(desc(ToolInvocation.created_at)).limit(limit)
            if session_id:
                stmt = stmt.where(ToolInvocation.session_id == session_id)
            return list(db.scalars(stmt))

    # ---- work orders ----
    def add_work_order(self, wo: WorkOrder) -> WorkOrder:
        with self.session() as db:
            db.add(wo)
            db.commit()
            return wo

    def list_work_orders(self, limit: int = 50) -> list[WorkOrder]:
        with self.session() as db:
            return list(
                db.scalars(select(WorkOrder).order_by(desc(WorkOrder.created_at)).limit(limit))
            )

    def count_work_orders(self) -> int:
        with self.session() as db:
            return db.scalar(select(func.count()).select_from(WorkOrder)) or 0

    def update_work_order_status(self, code: str, status: str) -> WorkOrder | None:
        with self.session() as db:
            wo = db.scalar(select(WorkOrder).where(WorkOrder.code == code))
            if wo is None:
                return None
            wo.status = status
            db.commit()
            return wo

    def health_check(self) -> bool:
        try:
            with self.session() as db:
                db.scalar(select(func.count()).select_from(ChatSession))
            return True
        except Exception:
            return False
