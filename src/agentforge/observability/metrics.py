"""Prometheus metrics."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "agentforge_http_requests_total", "HTTP requests", ["method", "path", "status"]
)
HTTP_LATENCY = Histogram(
    "agentforge_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

LLM_CALLS = Counter(
    "agentforge_llm_calls_total", "LLM provider calls", ["provider", "model", "status"]
)
LLM_LATENCY = Histogram(
    "agentforge_llm_latency_seconds", "LLM call latency", ["provider"],
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)
LLM_TOKENS = Counter(
    "agentforge_llm_tokens_total", "LLM token throughput", ["provider", "direction"]
)

TOOL_INVOCATIONS = Counter(
    "agentforge_tool_invocations_total", "Tool invocations", ["tool", "status"]
)
TOOL_LATENCY = Histogram(
    "agentforge_tool_latency_seconds", "Tool execution latency", ["tool"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20),
)

AGENT_ITERATIONS = Counter(
    "agentforge_agent_iterations_total", "ReAct iterations executed", ["outcome"]
)
ACTIVE_STREAMS = Gauge(
    "agentforge_active_chat_streams", "Currently open chat SSE streams"
)
