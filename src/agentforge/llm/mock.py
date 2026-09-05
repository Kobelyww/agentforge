"""Deterministic offline LLM provider.

Gives a fully functional demo (streaming + tool calling + planning +
summarisation) with zero API keys, and makes agent-loop tests reproducible in
CI. Routing is keyword/prompt-marker based:

- planner system prompt   → canned JSON plan
- executor system prompt  → step-appropriate tool call or step conclusion
- synthesizer system      → structured final answer
- tool result as last msg → final answer wrapping the tool output
- summarisation request   → condensed history summary
- arithmetic / code hints → python_repl tool call
- knowledge hints         → rag_search tool call
- otherwise               → helpful canned reply
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
from collections.abc import AsyncIterator, Sequence

from agentforge.llm.base import BaseLLM
from agentforge.llm.types import (
    ChatMessage,
    Finish,
    StreamEvent,
    TextDelta,
    ToolCallDelta,
    ToolSpec,
)

_ARITH_RE = re.compile(r"[-+*/().\d\s^%]+")
_RAG_HINTS = ("检索", "文档", "知识库", "手册", "案例", "资料", "查一下", "knowledge", "docs")
_CODE_HINTS = ("运行代码", "执行代码", "python", "代码", "run code")
_SUMMARY_HINTS = ("总结", "摘要", "summarize", "summarise", "digest")
_DIAG_HINTS = ("诊断", "异响", "振动", "频谱", "故障")

_CANNED_PLAN = {
    "thought": "先检索设备知识与历史案例，再做传感器数据分析，最后综合证据给出诊断结论。",
    "steps": [
        {
            "id": "s1",
            "title": "知识检索",
            "instruction": "使用 rag_search 检索知识库：设备手册中振动异常的常见原因、以及空压机轴承故障的历史案例。",
        },
        {
            "id": "s2",
            "title": "并行诊断",
            "instruction": "使用 dispatch_subagent 并行派出知识研究员（检索手册判据与历史案例）和数据分析师（分析 AC-017 振动频谱数据），汇总两份报告。",
        },
        {
            "id": "s3",
            "title": "诊断结论",
            "instruction": "综合知识检索与数据分析结果给出故障诊断结论（含置信度），并调用 create_work_order 生成维修工单。",
        },
    ],
    "success_criteria": "结论包含故障类型、置信度、数据依据，并已生成工单",
}


def _extract_arithmetic(text: str) -> str | None:
    """Return a safe arithmetic expression found in *text*, validated by AST."""
    candidates = [m.group(0).strip() for m in _ARITH_RE.finditer(text)]
    candidates.sort(key=len, reverse=True)
    for expr in candidates:
        expr = expr.replace("^", "**").strip()
        if not expr or not any(c.isdigit() for c in expr) or not any(c in "+-*/" for c in expr):
            continue
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError:
            continue
        allowed = (
            ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.operator,
            ast.unaryop, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
            ast.Mod, ast.Pow, ast.USub, ast.UAdd,
        )
        if all(isinstance(node, allowed) for node in ast.walk(tree)):
            return expr
    return None


class MockLLM(BaseLLM):
    name = "mock"
    default_model = "default"

    def __init__(self, *, latency: float = 0.008) -> None:
        self._latency = latency

    # ---------- helpers ----------
    async def _emit_text(self, text: str) -> AsyncIterator[StreamEvent]:
        for i in range(0, len(text), 12):
            yield TextDelta(text[i : i + 12])
            if self._latency:
                await asyncio.sleep(self._latency)

    @staticmethod
    def _last_user(messages: Sequence[ChatMessage]) -> str:
        for m in reversed(messages):
            if m.role == "user" and m.content:
                return m.content
        return ""

    @staticmethod
    def _system_text(messages: Sequence[ChatMessage]) -> str:
        return "\n".join((m.content or "") for m in messages if m.role == "system")

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        last = messages[-1] if messages else ChatMessage(role="user", content="")
        tool_names = {t.name for t in tools}
        system_text = self._system_text(messages)

        # --- tool result → wrap into a conclusion (executor-aware) ---
        if last.role == "tool":
            output = (last.content or "").strip()
            if len(output) > 400:
                output = output[:400] + "…"
            if "子代理" in system_text:
                reply = (
                    f"【子代理报告】基于授权工具的执行结果 — {output[:240]}\n"
                    "以上为本角色的独立结论，已交由主代理汇总。"
                )
            elif "步骤执行器" in system_text:
                # Step conclusion after a tool result — includes confidence so
                # the synthesizer can quote it deterministically.
                reply = (
                    f"步骤结论：基于工具返回结果 — {output[:200]}\n"
                    "初步判断轴承外圈磨损，置信度 0.87；建议结合手册判据与历史案例确认。"
                )
            else:
                reply = f"工具执行完成，结果如下：\n\n{output}\n\n以上内容由 Mock 模型基于工具输出生成。"
            async for ev in self._emit_text(reply):
                yield ev
            yield Finish("stop")
            return

        # --- orchestrated roles (plan-and-execute) ---
        if "任务规划器" in system_text:
            async for ev in self._emit_text(json.dumps(_CANNED_PLAN, ensure_ascii=False)):
                yield ev
            yield Finish("stop")
            return

        if "汇总者" in system_text:
            step_block = self._last_user(messages)
            peaks = re.findall(r"(\d+(?:\.\d+)?)\s*Hz", step_block)
            conf = re.findall(r"置信度[约为:\s]*(\d(?:\.\d+)?)", step_block)
            peak = peaks[0] if peaks else "176.9"
            confidence = conf[0] if conf else "0.87"
            reply = (
                "## 诊断结论\n"
                f"故障类型：**轴承外圈磨损**（置信度 {confidence}）。\n\n"
                "## 依据\n"
                f"1. 振动频谱在 {peak} Hz 处出现显著峰值，与轴承外圈故障特征频率（BPFO）吻合；\n"
                "2. 知识库案例库中存在同型号设备相同频谱特征的历史工单，维修后恢复正常；\n"
                "3. 设备手册 4.2 节指出该频段振幅超标（>4.5 mm/s）应安排停机检查。\n\n"
                "## 建议\n"
                "- 优先级 P2，预计停机 2 小时更换 6205-2RS 轴承；\n"
                "- 复机后 24 小时内持续监测振动 RMS 值。"
            )
            async for ev in self._emit_text(reply):
                yield ev
            yield Finish("stop")
            return

        if "步骤执行器" in system_text:
            instruction = self._last_user(messages) or (last.content or "")
            # work order step
            if "create_work_order" in tool_names and ("工单" in instruction or "生成维修" in instruction):
                yield ToolCallDelta(
                    index=0,
                    id="call_mock_wo",
                    name="create_work_order",
                    arguments_delta=json.dumps(
                        {
                            "equipment_id": "AC-017",
                            "title": "AC-017 空压机轴承外圈磨损维修",
                            "fault_type": "bearing_outer_race_wear",
                            "confidence": 0.87,
                            "priority": "P2",
                            "actions": ["停机断电", "更换 6205-2RS 轴承 x2", "复机后监测振动 RMS 24h"],
                            "parts": ["6205-2RS x2"],
                            "estimated_hours": 2,
                        },
                        ensure_ascii=False,
                    ),
                )
                yield Finish("tool_calls")
                return
            # multi-agent fan-out step: dispatch two specialists in ONE turn
            if "dispatch_subagent" in tool_names and ("并行" in instruction or "子代理" in instruction or "dispatch" in instruction):
                yield ToolCallDelta(
                    index=0,
                    id="call_mock_sub_rag",
                    name="dispatch_subagent",
                    arguments_delta=json.dumps({"specialists": [
                        {"role": "knowledge_researcher",
                         "task": "检索设备手册中振动异常的判据与 6205 轴承故障特征频率，以及空压机轴承故障的历史案例"},
                    ]}, ensure_ascii=False),
                )
                yield ToolCallDelta(
                    index=1,
                    id="call_mock_sub_data",
                    name="dispatch_subagent",
                    arguments_delta=json.dumps({"specialists": [
                        {"role": "data_analyst",
                         "task": "分析设备 AC-017 的振动传感器数据（operation=spectrum_peaks），给出主峰频率与 ISO 10816 状态"},
                    ]}, ensure_ascii=False),
                )
                yield Finish("tool_calls")
                return
            # knowledge step (checked before data analysis: instructions may
            # mention both, and 检索/手册/案例 is the stronger signal)
            if "rag_search" in tool_names and any(h in instruction for h in _RAG_HINTS):
                yield ToolCallDelta(
                    index=0,
                    id="call_mock_rag",
                    name="rag_search",
                    arguments_delta=json.dumps({"query": "振动异常 轴承故障 原因与案例"}, ensure_ascii=False),
                )
                yield Finish("tool_calls")
                return
            # sensor analysis step
            if "sensor_analysis" in tool_names and any(h in instruction for h in ("分析", "频谱", "振动", "传感器", "数据")):
                yield ToolCallDelta(
                    index=0,
                    id="call_mock_sensor",
                    name="sensor_analysis",
                    arguments_delta=json.dumps(
                        {"equipment_id": "AC-017", "operation": "spectrum_peaks"}, ensure_ascii=False
                    ),
                )
                yield Finish("tool_calls")
                return
            # plain conclusion step
            conclusion = (
                "步骤结论：综合已有信息，初判为轴承外圈磨损（置信度 0.87）。"
                "主要依据：振动频谱 176.9 Hz 峰值与外圈故障特征频率（BPFO）吻合，"
                "知识库中同型号设备历史案例支持该判断。建议安排停机更换轴承。"
            )
            async for ev in self._emit_text(conclusion):
                yield ev
            yield Finish("stop")
            return

        # --- summarisation (memory compressor) ---
        prompt_text = "\n".join((m.content or "") for m in messages)
        if any(h in prompt_text for h in _SUMMARY_HINTS) and "对话" in prompt_text:
            turns = sum(1 for m in messages if m.role == "user")
            summary = f"（Mock 摘要）此前对话约 {turns} 个用户轮次，涉及提问与工具调用，助手均已作答。"
            async for ev in self._emit_text(summary):
                yield ev
            yield Finish("stop")
            return

        question = self._last_user(messages) or (last.content or "")

        # --- arithmetic → python_repl ---
        if "python_repl" in tool_names:
            expr = _extract_arithmetic(question)
            if expr or any(h in question.lower() for h in _CODE_HINTS):
                code = f"print({expr})" if expr else "print('Hello from AgentForge sandbox!')"
                yield ToolCallDelta(
                    index=0,
                    id="call_mock_py",
                    name="python_repl",
                    arguments_delta=json.dumps({"code": code}, ensure_ascii=False),
                )
                yield Finish("tool_calls")
                return

        # --- diagnosis-flavoured question with sensor tool available ---
        if "sensor_analysis" in tool_names and any(h in question for h in _DIAG_HINTS):
            yield ToolCallDelta(
                index=0,
                id="call_mock_sensor",
                name="sensor_analysis",
                arguments_delta=json.dumps({"equipment_id": "AC-017", "operation": "spectrum_peaks"}, ensure_ascii=False),
            )
            yield Finish("tool_calls")
            return

        # --- knowledge questions → rag_search ---
        if "rag_search" in tool_names and any(h in question for h in _RAG_HINTS):
            yield ToolCallDelta(
                index=0,
                id="call_mock_rag",
                name="rag_search",
                arguments_delta=json.dumps({"query": question[:120]}, ensure_ascii=False),
            )
            yield Finish("tool_calls")
            return

        # --- canned capable reply ---
        reply = (
            f"（Mock 模型 · 离线演示模式）已收到你的消息：「{question[:80]}」。\n\n"
            "当前平台运行在内置 Mock Provider 上，无需任何 API Key 即可体验完整能力：\n"
            "- **设备诊断**：`诊断 AC-017 空压机昨晚异响` → 规划-执行-汇总全流程\n"
            "- **工具调用**：让我 `计算 12*34`，会触发 python_repl 沙箱\n"
            "- **知识库检索**：上传文档后问 `检索一下文档里关于 Agent 的内容`\n\n"
            "在 `config.yaml` 中配置真实 Provider（OpenAI / GLM / ModelArts / Anthropic）即可切换。"
        )
        async for ev in self._emit_text(reply):
            yield ev
        yield Finish("stop")
