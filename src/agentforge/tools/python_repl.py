"""Sandboxed Python code interpreter.

Each execution runs in a fresh ``python -I`` subprocess with CPU/memory/filesize
RLIMITs, a hard wall-clock timeout, and the session workspace as CWD. This is
process-level isolation — the docs describe the production hardening path
(gVisor/Firecracker or a container pool) for hostile multi-tenant input.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import resource
import sys

from agentforge.tools.base import Tool, ToolContext, ToolResult

_RESULT_MARKER = "__AGENTFORGE_RESULT__"

# Runs in the sandboxed child: capture stdout/stderr, support expression echo,
# and emit a JSON envelope on the last line for reliable parsing.
_CHILD_TEMPLATE = r'''
import io, json, sys, traceback
code = sys.stdin.read()
out, err = io.StringIO(), io.StringIO()
status = "ok"
old = sys.stdout, sys.stderr
sys.stdout, sys.stderr = out, err
try:
    g = {"__name__": "__main__"}
    try:
        result = eval(compile(code, "<cell>", "eval"), g)
        if result is not None:
            print(repr(result))
    except SyntaxError:
        exec(compile(code, "<cell>", "exec"), g)
except BaseException:
    status = "error"
    err.write(traceback.format_exc(limit=8))
finally:
    sys.stdout, sys.stderr = old
sys.stdout.write("\n__AGENTFORGE_RESULT__" + json.dumps({"status": status, "stdout": out.getvalue(),
                             "stderr": err.getvalue()}, ensure_ascii=False))
'''


def _apply_limits(memory_mb: int, cpu_seconds: int) -> None:  # pragma: no cover - child proc
    def setrlimit(res: int, limit: int) -> None:
        # Some limits are unavailable/restricted on certain platforms (e.g.
        # RLIMIT_NPROC on macOS); best-effort is fine for a sandbox child.
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(res, (limit, limit))

    setrlimit(resource.RLIMIT_AS, memory_mb * 1024 * 1024)
    setrlimit(resource.RLIMIT_CPU, cpu_seconds)
    setrlimit(resource.RLIMIT_FSIZE, 4 * 1024 * 1024)  # cap file writes at 4 MB
    setrlimit(resource.RLIMIT_CORE, 0)


class PythonREPLTool(Tool):
    name = "python_repl"
    description = (
        "在隔离沙箱中执行 Python 代码并返回 stdout/stderr。"
        "适合数学计算、数据处理、格式转换。代码以字符串形式传入 code 字段，"
        "用 print() 输出想要的结果。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "要执行的 Python 代码"},
        },
        "required": ["code"],
    }

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        memory_limit_mb: int = 512,
        cpu_limit_seconds: int = 10,
    ) -> None:
        self.timeout = timeout
        self._memory_mb = memory_limit_mb
        self._cpu_seconds = cpu_limit_seconds

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        code = str(args.get("code", ""))
        if not code.strip():
            return ToolResult(ok=False, output="", error="empty code")

        ctx.workspace.mkdir(parents=True, exist_ok=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-c", _CHILD_TEMPLATE,
                cwd=str(ctx.workspace),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=lambda: _apply_limits(self._memory_mb, self._cpu_seconds),
            )
        except (NotImplementedError, PermissionError):  # pragma: no cover
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-c", _CHILD_TEMPLATE,
                cwd=str(ctx.workspace),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        async def communicate():
            out, err = await proc.communicate(code.encode())
            return out, err

        try:
            out, err = await asyncio.wait_for(communicate(), timeout=self.timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult(
                ok=False,
                output="",
                error=f"execution timed out after {self.timeout}s (process killed)",
                meta={"timeout": True},
            )

        stdout_text = out.decode("utf-8", errors="replace")
        envelope, leaked = self._parse_envelope(stdout_text)
        stderr_text = err.decode("utf-8", errors="replace")

        if envelope is None:
            return ToolResult(
                ok=False,
                output="",
                error=f"no result envelope (exit={proc.returncode}); stderr: {stderr_text[:500]}",
                meta={"returncode": proc.returncode},
            )

        combined = (leaked + "\n" if leaked else "") + envelope.get("stdout", "")
        ok = envelope.get("status") == "ok"
        child_err = envelope.get("stderr", "")
        result_out = combined.strip()
        if not ok and not result_out:
            return ToolResult(ok=False, output="", error=child_err[:2000] or "runtime error")
        return ToolResult(
            ok=ok,
            output=result_out[:8000] or "(no output)",
            error=None if ok else child_err[:2000],
            meta={"stderr": child_err[:2000]},
        )

    @staticmethod
    def _parse_envelope(stdout_text: str) -> tuple[dict | None, str]:
        idx = stdout_text.rfind(_RESULT_MARKER)
        if idx == -1:
            return None, ""
        leaked = stdout_text[:idx].strip()
        raw = stdout_text[idx + len(_RESULT_MARKER):].strip()
        try:
            return json.loads(raw), leaked
        except json.JSONDecodeError:
            return None, leaked
