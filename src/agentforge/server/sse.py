"""SSE encoding helpers."""

from __future__ import annotations

import json
from typing import Any


def sse_event(event_type: str, data: Any) -> str:
    """Encode one SSE frame: ``event: <type>\\ndata: <json>\\n\\n``."""
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event_type}\ndata: {payload}\n\n"


def sse_comment(text: str = "ping") -> str:
    """Keep-alive comment frame (ignored by EventSource clients)."""
    return f": {text}\n\n"
