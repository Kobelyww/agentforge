"""Optional API-key authentication for /api routes."""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status

HEADER = "X-API-Key"


async def require_api_key(request: Request) -> None:
    """Dependency: when a key is configured on the server, require a match.

    With no key configured the API is open — meant for local dev / docker
    networks; put the service behind a gateway or set AGENTFORGE_API_KEY in
    anything exposed.
    """
    settings = request.app.state.state.settings
    expected = settings.server.api_key
    if not expected:
        return
    provided = request.headers.get(HEADER, "")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
