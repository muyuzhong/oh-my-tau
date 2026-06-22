from dataclasses import dataclass

from nanoagent.ai import AssistantMessage, StopReason, UserMessage, Usage
from nanoagent.agent.messages import CustomMessage, default_convert_to_llm


@dataclass
class Notification(CustomMessage):
    text: str = ""
    role: str = "notification"


def test_default_convert_filters_custom():
    msgs = [
        UserMessage(content="hi"),
        Notification(text="ui only"),
        AssistantMessage(
            content=[], model="m", provider="mock", api="mock", usage=Usage(), stop_reason=StopReason.STOP
        ),
    ]
    wire = default_convert_to_llm(msgs)
    assert [m.role for m in wire] == ["user", "assistant"]


@dataclass
class MislabelledCustom(CustomMessage):
    text: str = ""
    role: str = "user"


def test_default_convert_filters_custom_even_with_wire_role_string():
    msgs = [MislabelledCustom(text="not a wire user")]

    wire = default_convert_to_llm(msgs)

    assert wire == []
