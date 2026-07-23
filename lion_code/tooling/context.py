"""内部工具访问 Agent 状态时使用的受限上下文。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .types import JSONValue, ToolResult

if TYPE_CHECKING:
    from .registry import ToolRegistry


class AgentToolController(Protocol):
    """内部工具可调用的 Agent 业务能力契约。"""

    async def run_subagent_tool(
        self,
        arguments: Mapping[str, JSONValue],
    ) -> ToolResult: ...

    async def run_skill_tool(
        self,
        arguments: Mapping[str, JSONValue],
    ) -> ToolResult: ...

    async def enter_plan_mode_tool(self) -> ToolResult: ...

    async def exit_plan_mode_tool(self) -> ToolResult: ...

    async def schedule_wakeup_tool(
        self,
        arguments: Mapping[str, JSONValue],
    ) -> ToolResult: ...


@dataclass(slots=True)
class ToolContext:
    """单个 Agent 的工具执行状态；Registry 激活状态不会跨实例共享。"""

    session_id: str
    cwd: Path
    controller: AgentToolController
    registry: "ToolRegistry"
    permission_mode: str
    plan_file_path: str | None
    read_file_state: dict[str, float]
    confirm_fn: Callable[[str], Awaitable[bool]] | None = None
