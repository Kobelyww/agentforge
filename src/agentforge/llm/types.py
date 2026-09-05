"""Provider-agnostic LLM types: messages, tool specs, stream events, errors.

Every provider adapter normalises its wire protocol into these types, so the
agent runtime and the API layer never see vendor-specific payloads.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field


# ---------- messages ----------
@dataclass
class ToolCall:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class ChatMessage:
    role: str  # system | user | assistant | tool
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class ToolSpec:
    """Tool schema advertised to the model (JSON Schema parameters)."""

    name: str
    description: str
    parameters: dict


# ---------- usage ----------
@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


# ---------- stream events ----------
@dataclass
class Routed:
    """Emitted once at stream start: which provider/model is actually serving."""

    provider: str
    model: str


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCallDelta:
    """Incremental tool-call payload; merged by index then JSON-parsed."""

    index: int
    id: str | None = None
    name: str | None = None
    arguments_delta: str = ""


@dataclass
class Finish:
    finish_reason: str = "stop"  # stop | tool_calls | length
    usage: Usage | None = None


StreamEvent = Routed | TextDelta | ToolCallDelta | Finish


# ---------- errors ----------
class ProviderError(Exception):
    """A failure from an LLM provider, optionally retryable / failoverable."""

    def __init__(self, message: str, *, provider: str = "", status: int = 0, retryable: bool = False):
        super().__init__(message)
        self.provider = provider
        self.status = status
        self.retryable = retryable


# ---------- helpers ----------
_CJK_RANGES = (
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0x3400, 0x4DBF),
    (0x3000, 0x303F),  # CJK punctuation
    (0xFF00, 0xFFEF),  # fullwidth forms
)


def estimate_tokens(text: str) -> int:
    """CJK-aware token estimator (~1 token per CJK char, ~4 chars per Latin token).

    Deliberately dependency-free: good enough for context budgeting, and the
    estimator is swapped for provider usage numbers whenever available.
    """
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        code = ord(ch)
        if any(lo <= code <= hi for lo, hi in _CJK_RANGES):
            cjk += 1
        else:
            other += 1
    return cjk + (other + 3) // 4


def accumulate_tool_calls(deltas: list[ToolCallDelta]) -> list[ToolCall]:
    """Merge streamed ToolCallDelta fragments (by index) into ToolCall objects."""
    by_index: dict[int, dict] = {}
    for d in deltas:
        slot = by_index.setdefault(d.index, {"id": "", "name": "", "arguments": ""})
        if d.id:
            slot["id"] = d.id
        if d.name:
            slot["name"] = d.name
        slot["arguments"] += d.arguments_delta

    calls: list[ToolCall] = []
    for index in sorted(by_index):
        slot = by_index[index]
        raw = slot["arguments"].strip() or "{}"
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            args = {"_raw": raw, "_parse_error": True}
        calls.append(
            ToolCall(
                id=slot["id"] or f"call_{index}",
                name=slot["name"] or "unknown_tool",
                arguments=args if isinstance(args, dict) else {"_raw": args},
            )
        )
    return calls


_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Unicode NFKC normalisation + whitespace collapse (for deterministic tests)."""
    return _WS_RE.sub(" ", unicodedata.normalize("NFKC", text)).strip()
