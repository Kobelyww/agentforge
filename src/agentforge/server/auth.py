"""Authentication for /api routes — three coexisting modes.

1. **Open dev mode** (default): no api_key and no admin_password configured —
   everything allowed, meant for local dev / docker networks behind a gateway.
2. **API key**: ``server.api_key`` set → requests must present a matching
   ``X-API-Key`` header (constant-time compare). Machine-to-machine.
3. **JWT login**: ``server.admin_password`` set → ``POST /api/auth/login``
   exchanges credentials for a hand-rolled HS256 token (see security.py);
   requests may present ``Authorization: Bearer <token>``. Human operators.

API key and JWT can be enabled together; either authenticates.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status

from agentforge.server.security import jwt_decode

HEADER = "X-API-Key"


def _api_key_ok(request: Request) -> bool:
    expected = request.app.state.state.settings.server.api_key
    if not expected:
        return False
    provided = request.headers.get(HEADER, "")
    return bool(provided) and hmac.compare_digest(provided, expected)


def _bearer_ok(request: Request) -> bool:
    settings = request.app.state.state.settings
    secret = settings.server.auth_secret
    password = settings.server.admin_password
    if not secret or not password:
        return False
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    claims = jwt_decode(auth[len("Bearer "):].strip(), secret)
    return claims is not None and claims.get("sub") == settings.server.admin_username


async def require_api_key(request: Request) -> None:
    """Dependency: enforce whichever auth modes are configured on the server."""
    settings = request.app.state.state.settings
    if not settings.server.api_key and not settings.server.admin_password:
        return  # open dev mode
    if _api_key_ok(request) or _bearer_ok(request):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid or missing credentials",
        headers={"WWW-Authenticate": "ApiKey,Bearer"},
    )
