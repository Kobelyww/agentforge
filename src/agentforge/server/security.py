"""Password hashing and JWT tokens implemented on stdlib primitives only.

- PBKDF2-HMAC-SHA256 (100k iterations) for password storage, format ``salt$hash``.
- Hand-rolled HS256 JWT (base64url + HMAC) — deliberately dependency-free:
  the whole auth stack is auditable in one screen. Expired/tampered tokens
  return None; signature comparison is constant-time.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

_PBKDF2_ITERATIONS = 100_000


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), _PBKDF2_ITERATIONS
    )
    return hmac.compare_digest(actual, expected)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def jwt_encode(claims: dict, secret: str, *, expires_s: int = 3600) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {**claims, "iat": now, "exp": now + expires_s}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    )
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def jwt_decode(token: str, secret: str) -> dict | None:
    """Verify signature + expiry; returns claims or None."""
    try:
        signing_input, signature_b64 = token.rsplit(".", 1)
        header_b64, payload_b64 = signing_input.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64url_decode(signature_b64)):
        return None
    try:
        header = json.loads(_b64url_decode(header_b64))
        claims = json.loads(_b64url_decode(payload_b64))
    except (json.JSONDecodeError, ValueError):
        return None
    if header.get("alg") != "HS256":
        return None
    if int(claims.get("exp", 0)) < int(time.time()):
        return None
    return claims
