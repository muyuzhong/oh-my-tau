from __future__ import annotations

import asyncio
import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ValidationError

from nanoagent.ai import ImageContent, TextContent, Tool, ToolCall, ToolResultMessage


@dataclass
class AgentToolResult:
    content: list[TextContent | ImageContent] = field(default_factory=list)
    is_error: bool = False
    details: Any = None


class AgentTool(ABC):
    name: str
    description: str
    parameters: type[BaseModel]
    label: str = ""
    concurrency: Literal["shared", "exclusive"] = "shared"
    _wire: Tool | None = None

    def to_wire(self) -> Tool:
        # Tool definition is static; model_json_schema() isn't free, so build
        # the wire shape once and reuse it across turns/runs. Return a copy so
        # downstream provider adapters cannot mutate the cached schema.
        if self._wire is None:
            self._wire = Tool(
                name=self.name,
                description=self.description,
                parameters=self.parameters.model_json_schema(),
            )
        return Tool(
            name=self._wire.name,
            description=self._wire.description,
            parameters=copy.deepcopy(self._wire.parameters),
        )

    @abstractmethod
    async def execute(
        self, tool_call_id: str, params: BaseModel, signal: Any = None
    ) -> AgentToolResult: ...


def _error_result(call: ToolCall, text: str) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call.id,
        tool_name=call.name,
        content=[TextContent(text=text)],
        is_error=True,
    )


async def _run_one(call: ToolCall, tool: AgentTool, signal: Any, before_tool_call) -> ToolResultMessage:
    try:
        params = tool.parameters.model_validate(call.arguments)
    except ValidationError as e:
        return _error_result(call, f"Invalid arguments: {e}")
    if before_tool_call is not None:
        decision = await before_tool_call(call, params)
        if decision is not None and decision.get("block"):
            return _error_result(call, decision.get("reason") or "Tool blocked")
    try:
        result = await tool.execute(call.id, params, signal)
    except Exception as e:  # tool exceptions never propagate: encode as is_error
        return _error_result(call, f"Tool failed: {e}")
    return ToolResultMessage(
        tool_call_id=call.id,
        tool_name=call.name,
        content=result.content,
        is_error=result.is_error,
    )


async def execute_tool_calls(
    tool_calls: list[ToolCall],
    tools: list[AgentTool],
    *,
    signal: Any = None,
    before_tool_call: Callable[[ToolCall, BaseModel], Awaitable[dict | None]] | None = None,
) -> list[ToolResultMessage]:
    by_name = {t.name: t for t in tools}
    results: list[ToolResultMessage | None] = [None] * len(tool_calls)
    shared: list[asyncio.Task] = []
    for i, call in enumerate(tool_calls):
        tool = by_name.get(call.name)
        if tool is None:
            results[i] = _error_result(call, f"Unknown tool: {call.name}")
            continue
        if tool.concurrency == "exclusive":
            if shared:
                for idx, r in await asyncio.gather(*shared):
                    results[idx] = r
                shared = []
            results[i] = await _run_one(call, tool, signal, before_tool_call)
        else:

            async def _wrap(idx=i, c=call, t=tool):
                return idx, await _run_one(c, t, signal, before_tool_call)

            shared.append(asyncio.create_task(_wrap()))
    for idx, r in await asyncio.gather(*shared):
        results[idx] = r
    return [r for r in results if r is not None]
