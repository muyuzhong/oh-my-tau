from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from nanoagent.ai.messages import AssistantMessage, ToolCall


@dataclass
class StreamStart:
    type: str = field(default="start", init=False)


@dataclass
class TextStart:
    content_index: int
    type: str = field(default="text_start", init=False)


@dataclass
class TextDelta:
    content_index: int
    delta: str
    type: str = field(default="text_delta", init=False)


@dataclass
class TextEnd:
    content_index: int
    text: str
    type: str = field(default="text_end", init=False)


@dataclass
class ThinkingStart:
    content_index: int
    type: str = field(default="thinking_start", init=False)


@dataclass
class ThinkingDelta:
    content_index: int
    delta: str
    type: str = field(default="thinking_delta", init=False)


@dataclass
class ThinkingEnd:
    content_index: int
    thinking: str
    type: str = field(default="thinking_end", init=False)


@dataclass
class ToolCallStart:
    content_index: int
    type: str = field(default="toolcall_start", init=False)


@dataclass
class ToolCallDelta:
    content_index: int
    delta: str
    type: str = field(default="toolcall_delta", init=False)


@dataclass
class ToolCallEnd:
    content_index: int
    tool_call: ToolCall
    type: str = field(default="toolcall_end", init=False)


@dataclass
class StreamDone:
    message: AssistantMessage
    type: str = field(default="done", init=False)


@dataclass
class StreamError:
    message: AssistantMessage
    type: str = field(default="error", init=False)


AssistantMessageEvent = Union[
    StreamStart,
    TextStart,
    TextDelta,
    TextEnd,
    ThinkingStart,
    ThinkingDelta,
    ThinkingEnd,
    ToolCallStart,
    ToolCallDelta,
    ToolCallEnd,
    StreamDone,
    StreamError,
]
