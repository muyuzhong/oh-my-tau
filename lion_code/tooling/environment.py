"""父子 Agent 共享外部工具连接时使用的生命周期边界。"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..mcp_client import McpManager


@dataclass(slots=True)
class ToolEnvironment:
    """持有共享 MCP Manager，并只允许根环境负责关闭。"""

    mcp_manager: McpManager = field(default_factory=McpManager)
    owns_mcp_manager: bool = True
    _closed: bool = field(default=False, init=False, repr=False)

    async def close(self) -> None:
        """由根环境幂等地释放 MCP 连接；子视图关闭不影响父环境。"""
        if not self.owns_mcp_manager or self._closed:
            return
        self._closed = True
        await self.mcp_manager.disconnect_all()

    def child_view(self) -> "ToolEnvironment":
        """创建复用连接但不拥有关闭权的子环境。"""
        return ToolEnvironment(
            mcp_manager=self.mcp_manager,
            owns_mcp_manager=False,
        )
