"""用于 runtime 验收的最小内置工具集，完整工具层留给后续章节。"""
from __future__ import annotations
import asyncio, time
from pathlib import Path
from core.tool import Tool, ToolResult

MAX_FILE_CHARS = 20_000


class ReadFileTool(Tool):
    timeout_seconds = 10
    def name(self): return "read_file"
    def description(self): return "读取 UTF-8 文本文件，超长内容截断"
    def input_schema(self): return {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    async def call(self, params):
        start = time.perf_counter()
        try: text = Path(params["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError as error: return ToolResult(False, str(error), time.perf_counter() - start, type(error).__name__)
        if len(text) > MAX_FILE_CHARS: text = text[:MAX_FILE_CHARS] + "\n[已截断]"
        return ToolResult(True, text, time.perf_counter() - start)


class RunCommandTool(Tool):
    """执行系统 shell 命令，因此必须经过控制平面审批。"""
    timeout_seconds = 30
    requires_approval = True
    def name(self): return "run_command"
    def description(self): return "执行 shell 命令并返回退出码及合并输出"
    def input_schema(self): return {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}
    async def call(self, params):
        start = time.perf_counter()
        process = await asyncio.create_subprocess_shell(params["command"], stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        output, _ = await process.communicate()
        text = output.decode("utf-8", errors="replace")
        return ToolResult(process.returncode == 0, f"[exit {process.returncode}]\n{text}", time.perf_counter() - start,
                          None if process.returncode == 0 else "NonZeroExit")
