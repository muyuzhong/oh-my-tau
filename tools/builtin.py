"""用于 runtime 验收的最小内置工具集，完整工具层留给后续章节。

文件与 Shell 副作用一律经注入的 ExecutionEnv 端口执行，工具自身不直接触达
宿主机：这样工作区边界可强制、取消可传播，并能用伪端口完整测试工具。
"""
from __future__ import annotations

import time
from typing import Optional

from core.tool import Tool, ToolResult
from runtime.execution_env import ExecutionEnv, LocalExecutionEnv

MAX_FILE_CHARS = 20_000


class ReadFileTool(Tool):
    timeout_seconds = 10

    def __init__(self, env: Optional[ExecutionEnv] = None):
        self.env = env or LocalExecutionEnv()

    def name(self): return "read_file"
    def description(self): return "读取 UTF-8 文本文件，超长内容截断"
    def input_schema(self): return {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

    async def call(self, params):
        start = time.perf_counter()
        result = await self.env.read_text(params["path"])
        if not result.ok:
            return ToolResult(False, result.error or "读取失败", time.perf_counter() - start, "ReadError")
        text = result.content
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS] + "\n[已截断]"
        return ToolResult(True, text, time.perf_counter() - start)


class RunCommandTool(Tool):
    """执行系统 shell 命令，因此必须经过控制平面审批。"""
    timeout_seconds = 30
    requires_approval = True

    def __init__(self, env: Optional[ExecutionEnv] = None):
        self.env = env or LocalExecutionEnv()

    def name(self): return "run_command"
    def description(self): return "执行 shell 命令并返回退出码及合并输出"
    def input_schema(self): return {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}

    async def call(self, params):
        start = time.perf_counter()
        result = await self.env.run_shell(params["command"], timeout=self.timeout_seconds)
        details = result.output
        if result.error:
            details = f"{result.error}\n{details}" if details else result.error
        return ToolResult(result.ok, f"[exit {result.exit_code}]\n{details}", time.perf_counter() - start,
                          None if result.ok else "ShellError")
