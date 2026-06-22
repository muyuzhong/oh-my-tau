from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Union

from nanoagent.ai import AssistantMessage, Message, ToolResultMessage, UserMessage
from nanoagent.utils import new_id

_WIRE_MESSAGE_TYPES = (UserMessage, AssistantMessage, ToolResultMessage)


@dataclass
class CustomMessage:
    """Base for app-defined messages (UI-only / notification / artifact ...).

    Subclasses set `role` to a non-wire value; the harness's convert_to_llm
    decides how to downgrade or drop them.
    """

    role: str = "custom"
    id: str = field(default_factory=lambda: new_id("msg"))


AgentMessage = Union[Message, CustomMessage]
ConvertToLlm = Callable[[list["AgentMessage"]], list[Message]]


def default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    """Framework default: keep wire roles, drop unknown custom (mechanism).

    Specific downgrade behavior is a harness policy supplied via convert_to_llm.
    """
    return [m for m in messages if isinstance(m, _WIRE_MESSAGE_TYPES)]


__all__ = ["AgentMessage", "ConvertToLlm", "CustomMessage", "default_convert_to_llm"]
