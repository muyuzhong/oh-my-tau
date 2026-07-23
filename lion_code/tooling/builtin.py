"""把现有内置工具实现适配为统一的 :class:`LionTool`。"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping

from .types import JSONValue, LionTool, ToolCapabilities, ToolResult


BUILTIN_TOOL_NAMES = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "list_files",
        "grep_search",
        "run_shell",
        "web_fetch",
    }
)


def wrap_legacy_tool(
    *,
    name: str,
    description: str,
    parameters: Mapping[str, JSONValue],
    handler: Callable[[dict], object],
    capabilities: ToolCapabilities,
) -> LionTool:
    """保持旧实现行为，只把执行结果适配为结构化结果。"""

    async def execute(
        context,
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        on_update,
    ) -> ToolResult:
        del context, tool_call_id, on_update
        result = handler(dict(arguments))
        if inspect.isawaitable(result):
            result = await result
        content = str(result)
        return ToolResult(content=content, is_error=content.startswith("Error"))

    return LionTool(
        name=name,
        label=name,
        description=description,
        parameters=parameters,
        execute_fn=execute,
        capabilities=capabilities,
        execution_mode="parallel" if capabilities.concurrency_safe else "sequential",
    )


def create_builtin_tools() -> list[LionTool]:
    """创建文件、搜索、Shell 与网页工具的统一定义。"""
    # 延迟导入允许 tools.py 在模块加载末尾从本对象反向生成兼容 Schema，避免循环
    # 导入时读取尚未初始化完成的 create_builtin_tools。
    from .. import tools as legacy_tools

    return [
        wrap_legacy_tool(
            name="read_file",
            description="Read the contents of a file. Returns the file content with line numbers.",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The path to the file to read",
                    },
                },
                "required": ["file_path"],
            },
            handler=legacy_tools._read_file,
            capabilities=ToolCapabilities(
                read_only=True,
                concurrency_safe=True,
                allowed_in_plan=True,
                tracks_read_freshness=True,
                result_policy="snippable",
            ),
        ),
        wrap_legacy_tool(
            name="write_file",
            description="Write content to a file. Creates the file if it doesn't exist, overwrites if it does.",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The path to the file to write",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write to the file",
                    },
                },
                "required": ["file_path", "content"],
            },
            handler=legacy_tools._write_file,
            capabilities=ToolCapabilities(
                mutates_workspace=True,
                requires_read_before_write=True,
            ),
        ),
        wrap_legacy_tool(
            name="edit_file",
            description="Edit a file by replacing an exact string match with new content. The old_string must match exactly (including whitespace and indentation).",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The path to the file to edit",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The exact string to find and replace",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The string to replace it with",
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
            handler=legacy_tools._edit_file,
            capabilities=ToolCapabilities(
                mutates_workspace=True,
                requires_read_before_write=True,
            ),
        ),
        wrap_legacy_tool(
            name="list_files",
            description="List files matching a glob pattern. Returns matching file paths.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": 'Glob pattern to match files (e.g., "**/*.ts", "src/**/*")',
                    },
                    "path": {
                        "type": "string",
                        "description": "Base directory to search from. Defaults to current directory.",
                    },
                },
                "required": ["pattern"],
            },
            handler=legacy_tools._list_files,
            capabilities=ToolCapabilities(
                read_only=True,
                concurrency_safe=True,
                allowed_in_plan=True,
                result_policy="snippable",
            ),
        ),
        wrap_legacy_tool(
            name="grep_search",
            description="Search for a pattern in files. Returns matching lines with file paths and line numbers.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "The regex pattern to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory or file to search in. Defaults to current directory.",
                    },
                    "include": {
                        "type": "string",
                        "description": 'File glob pattern to include (e.g., "*.ts", "*.py")',
                    },
                },
                "required": ["pattern"],
            },
            handler=legacy_tools._grep_search,
            capabilities=ToolCapabilities(
                read_only=True,
                concurrency_safe=True,
                allowed_in_plan=True,
                result_policy="snippable",
            ),
        ),
        wrap_legacy_tool(
            name="run_shell",
            description="Execute a shell command and return its output. Use this for running tests, installing packages, git operations, etc.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Timeout in milliseconds (default: 30000)",
                    },
                },
                "required": ["command"],
            },
            handler=legacy_tools._run_shell,
            capabilities=ToolCapabilities(
                executes_process=True,
                result_policy="snippable",
            ),
        ),
        wrap_legacy_tool(
            name="web_fetch",
            description="Fetch a URL and return its content as text. For HTML pages, tags are stripped to return readable text. For JSON/text responses, content is returned directly.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch"},
                    "max_length": {
                        "type": "number",
                        "description": "Maximum content length in characters (default 50000)",
                    },
                },
                "required": ["url"],
            },
            handler=legacy_tools._web_fetch,
            capabilities=ToolCapabilities(
                read_only=True,
                external_side_effect=True,
                concurrency_safe=True,
                allowed_in_plan=True,
                result_policy="persist_large",
            ),
        ),
    ]
