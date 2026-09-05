"""Lightweight schema migration framework.

``create_all`` only creates missing tables — it never evolves existing ones.
This module applies ordered, versioned migrations on startup and records the
applied version in ``_schema_version``, so an AgentForge database from an
older release upgrades in place. Idempotent: running twice is a no-op.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger("agentforge.migrations")


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    apply: Callable[[Engine], None]


def _m1_hot_path_indexes(engine: Engine) -> None:
    """v1→v2: backfill hot-path indexes for deployments created before they existed."""
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_messages_session_seq ON messages (session_id, seq)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_chunks_document ON chunks (document_id, seq)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_memories_equipment ON memories (equipment_id)"
        ))


MIGRATIONS: list[Migration] = [
    Migration(version=2, description="hot-path indexes for messages/chunks/memories", apply=_m1_hot_path_indexes),
]


def run_migrations(engine: Engine) -> int:
    """Apply pending migrations in order. Returns the final schema version."""
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS _schema_version ("
            " version INTEGER PRIMARY KEY, description TEXT, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        ))
        row = conn.execute(text("SELECT COALESCE(MAX(version), 1) FROM _schema_version")).scalar()
        current = int(row or 1)

    applied = 0
    for migration in sorted(MIGRATIONS, key=lambda m: m.version):
        if migration.version <= current:
            continue
        logger.info("applying migration v%d: %s", migration.version, migration.description)
        migration.apply(engine)
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO _schema_version (version, description) VALUES (:v, :d)"),
                {"v": migration.version, "d": migration.description},
            )
        current = migration.version
        applied += 1
    return current
