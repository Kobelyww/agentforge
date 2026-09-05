"""Web page fetching tool (SSRF-guarded, redirect-safe)."""

from __future__ import annotations

import httpx

from agentforge.tools.base import Tool, ToolContext, ToolResult
from agentforge.tools.web_guard import extract_text, resolve_redirect, validate_url


class WebFetchTool(Tool):
    """Fetch a web page with SSRF guards and HTML-to-text extraction.

    Redirects are followed manually (max 4) so every hop re-runs the SSRF
    check — following redirects inside httpx would let a public URL bounce
    to an internal service.
    """

    name = "web_fetch"
    description = "抓取网页并提取纯文本内容。传入 url 字段。用于查询公开网页资料。"
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "完整 HTTP(S) URL"},
        },
        "required": ["url"],
    }

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        max_bytes: int = 2 * 1024 * 1024,
        allowed_domains: list[str] | None = None,
    ) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.allowed_domains = allowed_domains or []
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=False,
            headers={"User-Agent": "AgentForge/0.1 (+https://github.com/Kobelyww/agentforge)"},
        )

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        url = str(args.get("url", "")).strip()
        if error := validate_url(url, self.allowed_domains):
            return ToolResult(ok=False, output="", error=error)

        current = url
        for _ in range(4):
            try:
                resp = await self._client.get(current)
            except httpx.TimeoutException:
                return ToolResult(ok=False, output="", error=f"fetch timed out after {self.timeout}s")
            except httpx.HTTPError as exc:
                return ToolResult(ok=False, output="", error=f"fetch failed: {exc}")

            if resp.is_redirect:
                location = resp.headers.get("location", "")
                if not location:
                    return ToolResult(
                        ok=False, output="", error=f"redirect without location (HTTP {resp.status_code})"
                    )
                current, error = resolve_redirect(current, location, self.allowed_domains)
                if error:
                    return ToolResult(ok=False, output="", error=error)
                continue
            break
        else:
            return ToolResult(ok=False, output="", error="too many redirects")

        if resp.status_code != 200:
            return ToolResult(ok=False, output="", error=f"HTTP {resp.status_code} for {current}")

        content_type = resp.headers.get("content-type", "text/plain")
        body = resp.content[: self.max_bytes]
        text = extract_text(body.decode(resp.encoding or "utf-8", errors="replace"), content_type)
        output = text[:12000]
        suffix = "…(truncated)" if len(text) > 12000 or len(resp.content) > self.max_bytes else ""
        return ToolResult(
            ok=True,
            output=(output or "(empty page)") + suffix,
            meta={"url": current, "content_type": content_type, "bytes": len(resp.content)},
        )

    async def aclose(self) -> None:
        await self._client.aclose()
