"""MiniHarness 核心跨子系统类型：当前仅工具协议。

历史上这里还导出过 message/event/agent 等通用类型，但它们在主链零引用
（实际词汇表是 runtime/blocks 与 runtime/events），已按 ADR-013 删除，避免
形成第二套接近但不兼容的协议。
"""

from .tool import Tool, ToolDefinition, ToolInputSchema, ToolResult

__all__ = [
    "Tool",
    "ToolResult",
    "ToolDefinition",
    "ToolInputSchema",
]
