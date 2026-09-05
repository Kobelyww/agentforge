"""Configuration loading: YAML file + environment variable interpolation."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

_ENV_PATTERN = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::(?P<default>[^}]*))?\}")

DEFAULT_CONFIG_PATHS = ("config.yaml", "agentforge.yaml")


def _interpolate(value: Any) -> Any:
    """Recursively interpolate ``${VAR}`` / ``${VAR:default}`` in strings."""
    if isinstance(value, str):
        def _sub(match: re.Match[str]) -> str:
            name = match.group("name")
            default = match.group("default")
            env = os.environ.get(name)
            if env is not None:
                return env
            if default is not None:
                return default
            return ""

        return _ENV_PATTERN.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


class ProviderSpec(BaseModel):
    """One LLM provider endpoint."""

    name: str
    type: Literal["openai", "anthropic", "mock"] = "openai"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class AgentConfig(BaseModel):
    max_iterations: int = 8
    context_budget_tokens: int = 12000
    summary_threshold_tokens: int = 8000
    enabled_tools: list[str] = Field(default_factory=list)
    # react | plan_execute
    orchestrator: str = "react"
    # Human-in-the-loop: when False, P1/P2 work-order creation waits for an
    # explicit approval decision (per-request override possible). Default True
    # keeps the zero-config demo & CI autonomous.
    auto_approve: bool = True


class MCPServerSpec(BaseModel):
    """One external MCP (Model Context Protocol) server to attach."""

    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class ToolLimits(BaseModel):
    timeout_seconds: float = 20.0
    memory_limit_mb: int = 512
    cpu_limit_seconds: int = 10
    max_bytes: int = 2 * 1024 * 1024
    allowed_domains: list[str] = Field(default_factory=list)


class RagConfig(BaseModel):
    chunk_size: int = 800
    chunk_overlap: int = 100
    top_k: int = 5
    search_mode: Literal["hybrid", "vector", "bm25"] = "hybrid"
    embedder: str = "auto"


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    api_key: str | None = None
    rate_limit_rpm: int = 60
    # ---- auth (all optional; nothing set = open dev mode) ----
    auth_secret: str | None = None       # JWT signing key
    admin_username: str = "admin"
    admin_password: str | None = None    # enables POST /api/auth/login
    token_ttl_s: int = 12 * 3600
    # ---- ops ----
    webhook_url: str | None = None       # POSTed on work-order creation
    log_file: bool = True                # rotating JSON log under data/logs/


class Settings(BaseModel):
    """Top-level application settings."""

    server: ServerConfig = Field(default_factory=ServerConfig)
    providers: list[ProviderSpec] = Field(default_factory=list)
    default_model: str = "mock/default"
    fallback_chain: list[str] = Field(default_factory=list)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    tools: dict[str, ToolLimits] = Field(default_factory=dict)
    rag: RagConfig = Field(default_factory=RagConfig)
    mcp_servers: list[MCPServerSpec] = Field(default_factory=list)
    data_dir: Path = Path("data")
    db_url: str = ""
    config_path: Path | None = None

    def tool_limits(self, name: str) -> ToolLimits:
        return self.tools.get(name, ToolLimits())


def _default_providers() -> list[ProviderSpec]:
    """Providers built purely from environment variables (zero-config friendly)."""
    specs: list[ProviderSpec] = [ProviderSpec(name="mock", type="mock", model="default")]

    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        specs.append(
            ProviderSpec(
                name="openai",
                type="openai",
                base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                api_key=openai_key,
                model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            )
        )

    glm_key = os.environ.get("GLM_API_KEY", "")
    if glm_key:
        specs.append(
            ProviderSpec(
                name="glm",
                type="openai",
                base_url="https://open.bigmodel.cn/api/paas/v4",
                api_key=glm_key,
                model=os.environ.get("GLM_MODEL", "glm-4-plus"),
            )
        )

    ma_key = os.environ.get("MODELARTS_API_KEY", "")
    if ma_key:
        specs.append(
            ProviderSpec(
                name="modelarts",
                type="openai",
                base_url=os.environ.get(
                    "MODELARTS_BASE_URL", "https://api.modelarts-maas.com/v1"
                ),
                api_key=ma_key,
                model=os.environ.get("MODELARTS_MODEL", "DeepSeek-V3"),
            )
        )

    hermes_key = os.environ.get("NOUS_API_KEY") or os.environ.get("HERMES_API_KEY", "")
    if hermes_key:
        specs.append(
            ProviderSpec(
                name="hermes",
                type="openai",
                base_url=os.environ.get(
                    "HERMES_BASE_URL", "https://inference-api.nousresearch.com/v1"
                ),
                api_key=hermes_key,
                model=os.environ.get("HERMES_MODEL", "Hermes-4-70B"),
            )
        )

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        specs.append(
            ProviderSpec(
                name="anthropic",
                type="anthropic",
                base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
                api_key=anthropic_key,
                model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
            )
        )
    return specs


def _normalize_admin_auth(server: ServerConfig) -> None:
    """Hash the admin password once and derive a fallback JWT secret.

    Plaintext admin_password (from env/YAML) is replaced by its PBKDF2 hash in
    memory; tokens stay verifiable across processes in the same deployment by
    setting AGENTFORGE_AUTH_SECRET explicitly.
    """
    if server.admin_password and not _looks_hashed(server.admin_password):
        from agentforge.server.security import hash_password

        server.admin_password = hash_password(server.admin_password)
    if server.admin_password and not server.auth_secret:
        import hashlib

        server.auth_secret = hashlib.sha256(
            ("agentforge-auth:" + server.admin_password).encode()
        ).hexdigest()


def _looks_hashed(value: str) -> bool:
    parts = value.split("$")
    return len(parts) == 2 and len(parts[0]) == 32 and len(parts[1]) == 64


def load_settings(config_path: str | os.PathLike[str] | None = None) -> Settings:
    """Load settings from YAML config, falling back to env-var-only defaults.

    Lookup order: explicit ``config_path`` arg > ``AGENTFORGE_CONFIG`` env var >
    ``config.yaml`` / ``agentforge.yaml`` in cwd > built-in defaults.
    """
    candidates: list[Path] = []
    if config_path:
        candidates.append(Path(config_path))
    elif env_path := os.environ.get("AGENTFORGE_CONFIG"):
        candidates.append(Path(env_path))
    else:
        candidates.extend(Path(p) for p in DEFAULT_CONFIG_PATHS)

    raw: dict[str, Any] = {}
    used_path: Path | None = None
    for path in candidates:
        if path.is_file():
            raw = _interpolate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
            used_path = path
            break

    data_dir = Path(raw.get("data_dir", "data")).expanduser()
    settings = Settings(
        server=ServerConfig(**(raw.get("server") or {})),
        providers=[
            ProviderSpec(**p) for p in raw.get("providers", []) if isinstance(p, dict)
        ],
        default_model=raw.get("default_model", "mock/default"),
        fallback_chain=raw.get("fallback_chain", []) or [],
        agent=AgentConfig(**(raw.get("agent") or {})),
        tools={
            name: ToolLimits(**limits)
            for name, limits in (raw.get("tools") or {}).items()
            if isinstance(limits, dict)
        },
        rag=RagConfig(**(raw.get("rag") or {})),
        mcp_servers=[
            MCPServerSpec(**s) for s in raw.get("mcp_servers", []) if isinstance(s, dict)
        ],
        data_dir=data_dir,
        db_url=raw.get("db_url", ""),
        config_path=used_path,
    )

    if not settings.providers:
        settings.providers = _default_providers()

    # Environment overrides for server basics (docker-friendly).
    settings.data_dir = Path(
        os.environ.get("AGENTFORGE_DATA_DIR", str(settings.data_dir))
    ).expanduser()
    settings.server.host = os.environ.get("AGENTFORGE_HOST", settings.server.host)
    if port := os.environ.get("AGENTFORGE_PORT"):
        settings.server.port = int(port)
    settings.server.log_level = os.environ.get("AGENTFORGE_LOG_LEVEL", settings.server.log_level)
    if api_key := os.environ.get("AGENTFORGE_API_KEY"):
        settings.server.api_key = api_key
    if rpm := os.environ.get("AGENTFORGE_RATE_LIMIT_RPM"):
        settings.server.rate_limit_rpm = int(rpm)
    if v := os.environ.get("AGENTFORGE_AUTH_SECRET"):
        settings.server.auth_secret = v
    if v := os.environ.get("AGENTFORGE_ADMIN_USERNAME"):
        settings.server.admin_username = v
    if v := os.environ.get("AGENTFORGE_ADMIN_PASSWORD"):
        settings.server.admin_password = v
    if v := os.environ.get("AGENTFORGE_WEBHOOK_URL"):
        settings.server.webhook_url = v

    # Multi-source MCP config: config.yaml ⊂ ~/.agentforge/mcp.json ⊂ ./.mcp.json
    from agentforge.mcp_config import resolve_mcp_servers

    settings.mcp_servers = resolve_mcp_servers(settings.mcp_servers)

    _normalize_admin_auth(settings.server)

    return settings
