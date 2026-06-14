"""工具注册表与并发执行管道。

执行顺序固定为查找、参数校验、限流执行、错误观察化。任何工具失败都转换为
ToolResultBlock 返回模型，避免单个外部动作打断整个 Agent 循环。
"""
from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from core.tool import Tool, ToolResult
from runtime.blocks import ToolResultBlock, ToolUseBlock

TYPE_MAP = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


def validate_params(schema: dict, params: dict) -> List[str]:
    """执行 JSON Schema 的最小必填、类型和额外字段校验。"""
    if "__parse_error__" in params:
        return [f"工具参数 JSON 解析失败：{str(params['__parse_error__'])[:200]}"]
    errors = []
    for required in schema.get("required", []):
        if required not in params: errors.append(f"缺少必填参数：{required}")
    properties = schema.get("properties", {})
    for key, value in params.items():
        if key not in properties:
            errors.append(f"未知参数：{key}")
            continue
        expected = properties[key].get("type")
        if TYPE_MAP.get(expected) and not isinstance(value, TYPE_MAP[expected]):
            errors.append(f"参数 {key} 类型应为 {expected}，实际为 {type(value).__name__}")
    return errors


class ToolRegistry:
    def __init__(self): self._tools: Dict[str, Tool] = {}
    def register(self, tool: Tool) -> None: self._tools[tool.name()] = tool
    def get(self, name: str) -> Optional[Tool]: return self._tools.get(name)
    def schemas(self) -> List[dict]: return [tool.get_definition_dict() for tool in self._tools.values()]


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, max_concurrent: int = 5):
        self.registry, self.max_concurrent = registry, max_concurrent
        self._sem = None

    def _semaphore(self):
        # 延迟创建，确保 Semaphore 绑定当前正在运行的事件循环。
        if self._sem is None: self._sem = asyncio.Semaphore(self.max_concurrent)
        return self._sem

    async def execute_all(self, calls: List[ToolUseBlock]) -> List[ToolResultBlock]:
        return list(await asyncio.gather(*(self.execute_one(call) for call in calls))) if calls else []

    async def execute_one(self, call: ToolUseBlock) -> ToolResultBlock:
        tool = self.registry.get(call.name)
        if tool is None:
            return ToolResultBlock(call.id, f"工具 '{call.name}' 不存在", True, "ToolNotFound")
        errors = validate_params(tool.input_schema(), call.input)
        if errors:
            return ToolResultBlock(call.id, "参数校验失败：\n" + "\n".join(errors), True, "ParameterValidation")
        timeout = getattr(tool, "timeout_seconds", 30)
        async with self._semaphore():
            try:
                result = await asyncio.wait_for(tool.call(call.input), timeout)
                if isinstance(result, ToolResult):
                    return ToolResultBlock(call.id, str(result.content), not result.success, result.error_type)
                return ToolResultBlock(call.id, str(result))
            except asyncio.TimeoutError:
                return ToolResultBlock(call.id, f"工具执行超时（>{timeout}s）", True, "ToolTimeout")
            except Exception as error:
                return ToolResultBlock(call.id, f"{type(error).__name__}: {error}", True, type(error).__name__)
