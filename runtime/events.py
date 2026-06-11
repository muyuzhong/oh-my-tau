"""AgentLoop 对消费者发布的细粒度运行时事件词汇表。"""
from dataclasses import dataclass
from typing import List


@dataclass
class AgentStarted: session_id: str
@dataclass
class TurnStarted: turn: int
@dataclass
class TextDeltaEvent: text: str
@dataclass
class ThinkingDeltaEvent: thinking: str
@dataclass
class ToolCallStarted:
    tool_use_id: str
    name: str
@dataclass
class AssistantMessageEnd: stop_reason: str
@dataclass
class ApprovalRequested: calls: List
@dataclass
class ToolExecutionStarted:
    tool_use_id: str
    name: str
@dataclass
class ToolResultReceived:
    tool_use_id: str
    name: str
    is_error: bool
    content_preview: str
@dataclass
class ContextCompacted:
    before_tokens: int
    after_tokens: int
@dataclass
class InferenceRetrying:
    attempt: int
    delay: float
@dataclass
class SupervisorInjected: reason: str
@dataclass
class Steered: text: str
@dataclass
class Paused: pass
@dataclass
class Resumed: pass
@dataclass
class TurnEnded: turn: int
@dataclass
class ErrorEvent:
    error_type: str
    message: str
@dataclass
class AgentEnded:
    """reason 是稳定的机器可读终止原因。"""
    reason: str
