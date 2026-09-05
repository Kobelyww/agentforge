"""Structured JSON logging with request-id propagation via contextvars."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line (logfmt-friendly for Loki/ELK)."""

    def __init__(self, extra_keys: tuple[str, ...] = ("request_id",)) -> None:
        super().__init__()
        self._extra_keys = extra_keys

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid != "-":
            payload["request_id"] = rid
        # Support logger.info("...", extra={"key": value})
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_RESERVED = frozenset(
    logging.LogRecord("x", 0, "", 0, "", (), None).__dict__.keys()
) | {"msg", "args", "taskName"}


class PlainFormatter(logging.Formatter):
    """Human-readable console output for local development."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        rid = request_id_var.get()
        base = f"{ts} {record.levelname:<5} [{record.name}] {record.getMessage()}"
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _RESERVED and not k.startswith("_")
        }
        if extras:
            base += f" {extras}"
        if rid != "-":
            base += f" (req={rid[:8]})"
        if record.exc_info and record.exc_info[0] is not None:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging(
    level: str = "INFO",
    json_output: bool = False,
    log_file: str | None = None,
) -> None:
    """Configure root logging: console (+ optional rotating JSON file).

    ``log_file`` enables a 10 MB × 5 rotating handler under data/logs/ so
    deployments keep structured logs across restarts without a collector.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else PlainFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    if log_file:
        from logging.handlers import RotatingFileHandler
        from pathlib import Path

        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)
    # Third-party noise reduction
    for noisy in ("httpx", "httpcore", "uvicorn.access", "jieba"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
