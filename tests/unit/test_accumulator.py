from providers.base import MessageEnd, TextDelta, ThinkingDelta, ToolInputDelta, ToolUseEnd, ToolUseStart
from runtime.blocks import ThinkingBlock, Usage
from runtime.engine import StreamAccumulator


def test_text_only():
    acc = StreamAccumulator()
    for event in [TextDelta("你"), TextDelta("好"), MessageEnd("end_turn", Usage(3, 4))]: acc.feed(event)
    assert acc.result().get_text() == "你好" and acc.stop_reason == "end_turn"


def test_text_then_tool_with_split_json():
    acc = StreamAccumulator()
    for event in [TextDelta("执行"), ToolUseStart("t1", "bash"), ToolInputDelta("t1", '{"cmd"'),
                  ToolInputDelta("t1", ': "ls"}'), ToolUseEnd("t1"), MessageEnd("tool_use", Usage(1, 2))]: acc.feed(event)
    assert acc.result().get_tool_calls()[0].input == {"cmd": "ls"}


def test_broken_json_marked_as_parse_error():
    acc = StreamAccumulator()
    for event in [ToolUseStart("t1", "bash"), ToolInputDelta("t1", "{bad"), ToolUseEnd("t1")]: acc.feed(event)
    assert "__parse_error__" in acc.result().get_tool_calls()[0].input


def test_interleaved_tools_by_id():
    acc = StreamAccumulator()
    for event in [ToolUseStart("a", "a"), ToolUseStart("b", "b"), ToolInputDelta("a", '{"x":1}'),
                  ToolInputDelta("b", '{"y":2}'), ToolUseEnd("a"), ToolUseEnd("b")]: acc.feed(event)
    assert {c.id: c.input for c in acc.result().get_tool_calls()} == {"a": {"x": 1}, "b": {"y": 2}}


def test_thinking_block():
    acc = StreamAccumulator()
    for event in [ThinkingDelta("想想"), TextDelta("好")]: acc.feed(event)
    assert isinstance(acc.result().content[0], ThinkingBlock)
