"""JWT login routes (enabled when server.admin_password is configured)."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from agentforge.server.security import jwt_encode, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    settings = request.app.state.state.settings
    if not settings.server.admin_password:
        raise HTTPException(404, "login is disabled (no admin password configured)")

    user_ok = hmac.compare_digest(body.username, settings.server.admin_username)
    pass_ok = verify_password(body.password, settings.server.admin_password)
    if not (user_ok and pass_ok):
        raise HTTPException(401, "invalid credentials")

    secret = settings.server.auth_secret
    token = jwt_encode(
        {"sub": settings.server.admin_username, "scope": "api"},
        secret,
        expires_s=settings.server.token_ttl_s,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.server.token_ttl_s,
        "username": settings.server.admin_username,
    }


@router.get("/me")
async def me(request: Request):
    """Echo the caller's identity (JWT or API key); 401 when auth required and absent."""
    from agentforge.server.auth import _api_key_ok, _bearer_ok

    settings = request.app.state.state.settings
    if not settings.server.api_key and not settings.server.admin_password:
        return {"authenticated": False, "mode": "open"}
    if _api_key_ok(request):
        return {"authenticated": True, "mode": "api_key", "username": "api-key"}
    if _bearer_ok(request):
        return {"authenticated": True, "mode": "jwt", "username": settings.server.admin_username}
    raise HTTPException(401, "invalid or missing credentials")
