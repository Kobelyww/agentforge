"""Knowledge-base search tool."""

from __future__ import annotations

from agentforge.tools.base import Tool, ToolContext, ToolResult


class RagSearchTool(Tool):
    name = "rag_search"
    description = (
        "在会话绑定的知识库（用户上传的文档）中进行混合检索（向量 + BM25），"
        "返回最相关的文本片段。当需要引用用户资料、文档内容时使用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索查询语句"},
        },
        "required": ["query"],
    }

    def __init__(self, *, top_k: int = 5, timeout: float = 10.0) -> None:
        self._top_k = top_k
        self.timeout = timeout

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        query = str(args.get("query", "")).strip()
        if not query:
            return ToolResult(ok=False, output="", error="empty query")
        if ctx.retriever is None:
            return ToolResult(ok=False, output="", error="knowledge base is not available")

        results = await ctx.retriever.search(query, k=self._top_k)
        if not results:
            return ToolResult(ok=True, output="（知识库中没有找到相关内容）", meta={"hits": 0})

        lines = [f"[{i + 1}] 来源: {r.document_name} (score={r.score:.3f})\n{r.text}" for i, r in enumerate(results)]
        return ToolResult(
            ok=True,
            output="\n\n".join(lines),
            meta={"hits": len(results), "mode": ctx.retriever.mode},
        )
