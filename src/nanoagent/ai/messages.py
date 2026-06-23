from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union

from nanoagent.ai.stop_reason import StopReason
from nanoagent.utils import new_id


# ---- content blocks ----
@dataclass
class TextContent:
    text: str
    type: Literal["text"] = "text"


@dataclass
class ThinkingContent:
    thinking: str
    type: Literal["thinking"] = "thinking"


@dataclass
class ImageContent:
    data: str  # base64
    mime_type: str
    type: Literal["image"] = "image"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    type: Literal["toolCall"] = "toolCall"


AssistantContent = Union[TextContent, ThinkingContent, ToolCall]
UserContent = Union[TextContent, ImageContent]


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    total_tokens: int = 0

    def __post_init__(self) -> None:
        if self.total_tokens == 0 and (self.input or self.output):
            self.total_tokens = self.input + self.output


# ---- messages ----
@dataclass
class UserMessage:
    content: str | list[UserContent]
    role: Literal["user"] = "user"
    id: str = field(default_factory=lambda: new_id("msg"))


@dataclass
class AssistantMessage:
    content: list[AssistantContent]
    model: str
    provider: str
    api: str
    usage: Usage
    stop_reason: StopReason
    error_message: str | None = None
    role: Literal["assistant"] = "assistant"
    id: str = field(default_factory=lambda: new_id("msg"))

    @classmethod
    def empty(cls, model: str, provider: str, api: str) -> AssistantMessage:
        """Seed an in-progress assistant message (no content yet) for streaming."""
        return cls(
            content=[],
            model=model,
            provider=provider,
            api=api,
            usage=Usage(),
            stop_reason=StopReason.STOP,
        )


@dataclass
class ToolResultMessage:
    tool_call_id: str
    tool_name: str
    content: list[UserContent]
    is_error: bool = False
    role: Literal["toolResult"] = "toolResult"
    id: str = field(default_factory=lambda: new_id("msg"))


Message = Union[UserMessage, AssistantMessage, ToolResultMessage]


@dataclass
class Context:
    system_prompt: list[str] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    tools: list[Any] = field(default_factory=list)  # list[Tool]; Any avoids a back-dep on tools.py

    def __post_init__(self) -> None:
        self.system_prompt = list(self.system_prompt)
        self.messages = list(self.messages)
        self.tools = list(self.tools)
