"""Tool contract: JSON-schema parameters, validation, execution context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentforge.config import Settings

_JSON_TYPES: dict[str, Any] = {
    "string": str, "number": (int, float), "integer": int,
    "boolean": bool, "array": list, "object": dict,
}


@dataclass
class ToolContext:
    """Per-invocation execution context handed to tools."""

    session_id: str | None
    workspace: Path
    settings: Settings
    retriever: Any | None = None  # agentforge.rag.retriever.Retriever


@dataclass
class ToolResult:
    ok: bool
    output: str
    error: str | None = None
    meta: dict = field(default_factory=dict)

    def to_llm_payload(self) -> str:
        """String handed back to the model as the tool message content."""
        if self.ok:
            return self.output
        return f"工具执行失败: {self.error or 'unknown error'}"


class Tool(ABC):
    """A callable capability advertised to the model via JSON Schema."""

    name: str = "tool"
    description: str = ""
    parameters: dict = {"type": "object", "properties": {}, "required": []}
    timeout: float = 20.0

    @abstractmethod
    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult: ...


def validate_args(args: dict, schema: dict) -> str | None:
    """Minimal JSON-Schema validation (required / type / enum, one level deep).

    The full JSON-Schema spec is intentionally not pulled in: every built-in
    tool schema is flat, and the validator returns a human-readable error that
    is fed back to the model so it can self-correct.
    """
    if schema.get("type") != "object":
        return None
    if not isinstance(args, dict):
        return "arguments must be a JSON object"
    for name in schema.get("required", []):
        if name not in args:
            return f"missing required argument: {name}"
    props = schema.get("properties", {})
    for key, value in args.items():
        prop = props.get(key)
        if not prop:
            continue  # additional properties tolerated
        expected = _JSON_TYPES.get(prop.get("type"))
        if expected and not isinstance(value, expected):
            return f"argument {key!r} must be of type {prop.get('type')}"
        if "enum" in prop and value not in prop["enum"]:
            return f"argument {key!r} must be one of {prop['enum']}"
        if prop.get("type") == "object" and isinstance(value, dict):
            for sub in prop.get("required", []):
                if sub not in value:
                    return f"argument {key!r} is missing required field {sub!r}"
    return None
