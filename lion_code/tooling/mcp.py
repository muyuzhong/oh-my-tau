"""把 MCP 远端工具适配为统一的 LionTool。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..mcp_client import DiscoveredMcpTool
from .types import LionTool, ToolCapabilities, ToolResult

if TYPE_CHECKING:
    from ..mcp_client import McpManager


def create_mcp_tool(
    manager: "McpManager",
    definition: DiscoveredMcpTool,
) -> LionTool:
    """创建保守授权、顺序执行的 MCP 工具对象。"""
    public_name = f"mcp__{definition.server_name}__{definition.remote_name}"

    async def execute(context, tool_call_id, arguments, on_update):
        del context, tool_call_id, on_update
        content = await manager.call_remote_tool(
            server_name=definition.server_name,
            tool_name=definition.remote_name,
            arguments=dict(arguments),
        )
        return ToolResult(
            content=content,
            details={
                "source": "mcp",
                "server_name": definition.server_name,
                "remote_name": definition.remote_name,
            },
        )

    return LionTool(
        name=public_name,
        label=definition.remote_name,
        description=definition.description,
        parameters=definition.input_schema,
        execute_fn=execute,
        capabilities=ToolCapabilities(
            external_side_effect=True,
            requires_confirmation=True,
            concurrency_safe=False,
        ),
    )
