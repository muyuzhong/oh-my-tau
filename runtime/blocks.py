"""Mono 运行时内部统一的块消息模型。

Provider 适配器、核心循环与会话转录都只交换本模块的数据类型。这样可以把
Anthropic、OpenAI 等外部协议差异限制在适配器内部，避免协议细节扩散到引擎。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _new_id(prefix: str) -> str:
    """生成便于日志检索、同时足够避免碰撞的短标识。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class TextBlock:
    """模型或用户可见的普通文本块。"""

    text: str
    type: str = "text"

    def to_dict(self) -> Dict[str, Any]:
        return {"type": "text", "text": self.text}


@dataclass
class ThinkingBlock:
    """支持 reasoning/extended-thinking 模型的推理内容块。"""

    thinking: str
    type: str = "thinking"

    def to_dict(self) -> Dict[str, Any]:
        return {"type": "thinking", "thinking": self.thinking}


@dataclass
class ToolUseBlock:
    """模型发起的一次工具调用。

    ``id`` 是调用与结果之间的稳定关联键，不能用工具名替代，因为同一轮可能
    并发调用同名工具多次。
    """

    name: str
    input: Dict[str, Any]
    id: str = field(default_factory=lambda: _new_id("tooluse"))
    type: str = "tool_use"

    def to_dict(self) -> Dict[str, Any]:
        return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}


@dataclass
class ToolResultBlock:
    """一次工具调用的可观察结果，错误也作为结果反馈给模型。"""

    tool_use_id: str
    content: str
    is_error: bool = False
    error_type: Optional[str] = None
    type: str = "tool_result"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "tool_result",
            "tool_use_id": self.tool_use_id,
            "content": self.content,
            "is_error": self.is_error,
            "error_type": self.error_type,
        }


@dataclass
class Usage:
    """Provider 返回的真实 token 用量，用于任务级预算记账。"""

    input_tokens: int = 0
    output_tokens: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


def block_from_dict(data: Dict[str, Any]):
    """根据显式 ``type`` 判别并恢复内容块，未知类型立即拒绝。"""
    block_type = data.get("type")
    if block_type == "text":
        return TextBlock(text=data["text"])
    if block_type == "thinking":
        return ThinkingBlock(thinking=data["thinking"])
    if block_type == "tool_use":
        return ToolUseBlock(name=data["name"], input=data["input"], id=data["id"])
    if block_type == "tool_result":
        return ToolResultBlock(
            tool_use_id=data["tool_use_id"],
            content=data["content"],
            is_error=data.get("is_error", False),
            error_type=data.get("error_type"),
        )
    raise ValueError(f"未知块类型: {block_type}")


@dataclass
class Message:
    """运行时消息。

    ``content`` 保留混合块列表，而不是压平成字符串；这是并行工具调用、推理块
    和无损会话恢复能够共存的基础。
    """

    role: str
    content: List[Any]
    message_id: str = field(default_factory=lambda: _new_id("msg"))
    # 使用带时区 UTC 时间，避免转录文件跨机器恢复后产生歧义。
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    usage: Optional[Usage] = None

    @classmethod
    def user(cls, text: str) -> "Message":
        return cls(role="user", content=[TextBlock(text=text)])

    @classmethod
    def assistant(cls, blocks: List[Any], usage: Optional[Usage] = None) -> "Message":
        return cls(role="assistant", content=list(blocks), usage=usage)

    @classmethod
    def tool_results(cls, results: List[ToolResultBlock]) -> "Message":
        # 主流 LLM API 都把 tool_result 视为外部世界回传给 assistant 的 user 消息。
        return cls(role="user", content=list(results))

    def get_text(self) -> str:
        return "".join(block.text for block in self.content if isinstance(block, TextBlock))

    def get_tool_calls(self) -> List[ToolUseBlock]:
        return [block for block in self.content if isinstance(block, ToolUseBlock)]

    def has_tool_calls(self) -> bool:
        return any(isinstance(block, ToolUseBlock) for block in self.content)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": [block.to_dict() for block in self.content],
            "message_id": self.message_id,
            "timestamp": self.timestamp.isoformat(),
            "usage": self.usage.to_dict() if self.usage else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        usage = Usage(**data["usage"]) if data.get("usage") else None
        return cls(
            role=data["role"],
            content=[block_from_dict(block) for block in data["content"]],
            message_id=data["message_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            usage=usage,
        )


def estimate_tokens(message: Message) -> int:
    """按序列化长度粗估 token。

    该估算只用于发送前的预算门限；中文等内容可能被低估，发送后的真实记账必须
    以 Provider 返回的 :class:`Usage` 为准。
    """
    return max(1, len(json.dumps(message.to_dict(), ensure_ascii=False)) // 4)
