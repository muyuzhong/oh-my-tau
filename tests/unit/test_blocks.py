"""统一块消息模型的行为测试。"""

from runtime.blocks import (
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
    estimate_tokens,
)


def test_user_factory():
    m = Message.user("你好")
    assert m.role == "user"
    assert m.get_text() == "你好"
    assert not m.has_tool_calls()
    assert m.message_id.startswith("msg_")


def test_assistant_with_tool_calls():
    blocks = [TextBlock(text="我来执行"), ToolUseBlock(name="bash", input={"cmd": "ls"})]
    m = Message.assistant(blocks, usage=Usage(input_tokens=10, output_tokens=5))
    assert m.has_tool_calls()
    calls = m.get_tool_calls()
    assert calls[0].name == "bash"
    assert calls[0].id.startswith("tooluse_")
    assert m.usage.input_tokens == 10


def test_tool_results_factory_is_user_role():
    r = ToolResultBlock(tool_use_id="t1", content="ok")
    m = Message.tool_results([r])
    assert m.role == "user"
    assert isinstance(m.content[0], ToolResultBlock)


def test_serialization_roundtrip():
    original = Message.assistant(
        [
            ThinkingBlock(thinking="想一想"),
            TextBlock(text="执行中"),
            ToolUseBlock(name="bash", input={"cmd": "ls"}, id="tooluse_abc"),
        ],
        usage=Usage(input_tokens=3, output_tokens=4),
    )
    restored = Message.from_dict(original.to_dict())
    assert restored.to_dict() == original.to_dict()
    assert restored.get_tool_calls()[0].input == {"cmd": "ls"}
    assert restored.usage.output_tokens == 4


def test_estimate_tokens_positive():
    assert estimate_tokens(Message.user("hello world")) >= 1
