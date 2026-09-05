"""ASGI middlewares: request-id + structured access log, token-bucket rate limit."""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from agentforge.logging import request_id_var
from agentforge.observability.metrics import HTTP_LATENCY, HTTP_REQUESTS

access_logger = logging.getLogger("agentforge.access")

_STATIC_PATH_PREFIXES = ("/assets", "/favicon", "/metrics", "/healthz", "/readyz")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, emit one structured access-log line, record metrics."""

    async def dispatch(self, request: Request, call_next) -> Response:
        import uuid

        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request_id_var.set(request_id)
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = time.perf_counter() - started
            HTTP_REQUESTS.labels(
                method=request.method, path=request.url.path, status=500
            ).inc()
            HTTP_LATENCY.labels(method=request.method, path=request.url.path).observe(duration)
            access_logger.exception("request crashed", extra={"path": request.url.path, "duration_ms": round(duration * 1000, 1)})
            raise
        duration = time.perf_counter() - started
        response.headers["X-Request-ID"] = request_id
        HTTP_REQUESTS.labels(
            method=request.method, path=request.url.path, status=response.status_code
        ).inc()
        HTTP_LATENCY.labels(method=request.method, path=request.url.path).observe(duration)
        if not request.url.path.startswith(_STATIC_PATH_PREFIXES):
            access_logger.info(
                "%s %s -> %s",
                request.method,
                request.url.path,
                response.status_code,
                extra={"duration_ms": round(duration * 1000, 1)},
            )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline hardening headers on every response.

    CSP is tuned for the bundled SPA: same-origin assets, inline styles
    (React style props), data: images (emoji favicon), no third-party origins.
    """

    HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Content-Security-Policy": (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        ),
    }

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for key, value in self.HEADERS.items():
            response.headers.setdefault(key, value)
        return response


class TokenBucketRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-client token bucket (keyed by API key else IP).

    Refill is lazy: computed from elapsed time on each request. Prunes stale
    buckets periodically so the dict does not grow unbounded.
    """

    def __init__(self, app, requests_per_minute: int) -> None:
        super().__init__(app)
        self.capacity = float(max(requests_per_minute, 1))
        self.refill_per_second = self.capacity / 60.0
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)
        self._last_prune = time.monotonic()

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path.startswith(("/healthz", "/readyz", "/metrics")):
            return await call_next(request)

        key = request.headers.get("X-API-Key") or (request.client.host if request.client else "?")
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill_per_second)
        if tokens < 1.0:
            retry_after = (1.0 - tokens) / self.refill_per_second
            return JSONResponse(
                {"error": {"code": "rate_limited", "message": "too many requests"}},
                status_code=429,
                headers={"Retry-After": f"{retry_after:.0f}"},
            )
        self._buckets[key] = (tokens - 1.0, now)

        if now - self._last_prune > 300:
            stale = [k for k, (_, ts) in self._buckets.items() if now - ts > 600]
            for k in stale:
                del self._buckets[k]
            self._last_prune = now

        return await call_next(request)
