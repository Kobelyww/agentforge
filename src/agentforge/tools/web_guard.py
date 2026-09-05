"""Security helpers for outbound web fetching: SSRF guards + HTML text extraction."""

from __future__ import annotations

import ipaddress
import re
import socket
from html import unescape
from urllib.parse import urljoin, urlparse

_BLOCKED_NETS = [
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
        "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16", "198.18.0.0/15",
        "224.0.0.0/4", "240.0.0.0/4", "::1/128", "fc00::/7", "fe80::/10",
    )
]

_MAX_HOSTS_CACHE: dict[str, list[ipaddress.IPv4Address | ipaddress.IPv6Address]] = {}


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return any(addr in net for net in _BLOCKED_NETS)


def _domain_allowed(host: str, allowed_domains: list[str]) -> bool:
    if not allowed_domains:
        return True
    host = host.lower().lstrip(".")
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in allowed_domains)


def validate_url(url: str, allowed_domains: list[str] | None = None) -> str | None:
    """Return an error string if the URL must not be fetched, else None."""
    if not url:
        return "empty url"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"scheme {parsed.scheme!r} not allowed (http/https only)"
    host = parsed.hostname
    if not host:
        return "URL has no hostname"
    if not _domain_allowed(host, allowed_domains or []):
        return f"domain {host!r} is not in the allowlist"
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror:
        return f"cannot resolve host {host!r}"
    for info in infos:
        ip = str(info[4][0])
        if _is_private_ip(ip):
            return f"host {host!r} resolves to a private address ({ip}) — blocked (SSRF guard)"
    return None


def resolve_redirect(current_url: str, location: str, allowed_domains: list[str] | None) -> tuple[str, str | None]:
    """Validate and resolve a redirect target (re-runs the SSRF guard)."""
    target = urljoin(current_url, location)
    return target, validate_url(target, allowed_domains)


_SCRIPT_RE = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]*\n[ \t\n]*")


def extract_text(body: str, content_type: str) -> str:
    """HTML → readable text without a heavyweight parser dependency."""
    if "html" not in content_type.lower():
        if "json" in content_type.lower():
            return body
        return body

    text = _SCRIPT_RE.sub(" ", body)
    text = _COMMENT_RE.sub(" ", text)
    # Keep the textual hint of structural tags for readability.
    text = re.sub(r"<(br|/p|/div|/h[1-6]|/li|/tr)\b[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = unescape(text)
    text = _WS_RE.sub("\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
