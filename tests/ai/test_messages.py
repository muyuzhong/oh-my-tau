from nanoagent.ai import (
    AssistantMessage,
    Context,
    StopReason,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    Usage,
)


def test_user_message_defaults():
    m = UserMessage(content="hi")
    assert m.role == "user"
    assert m.id  # auto-generated


def test_usage_derives_total_tokens_when_missing():
    usage = Usage(input=2, output=3)

    assert usage.total_tokens == 5


def test_assistant_message_carries_wire_fields():
    m = AssistantMessage(
        content=[TextContent(text="ok"), ToolCall(id="t1", name="echo", arguments={"x": 1})],
        model="m",
        provider="mock",
        api="mock",
        usage=Usage(input=1, output=2, total_tokens=3),
        stop_reason=StopReason.TOOL_USE,
    )
    assert m.role == "assistant"
    assert m.content[1].type == "toolCall"
    assert m.stop_reason is StopReason.TOOL_USE
    assert m.usage.total_tokens == 3


def test_tool_result_message():
    r = ToolResultMessage(tool_call_id="t1", tool_name="echo", content=[TextContent(text="1")])
    assert r.role == "toolResult" and r.is_error is False


def test_context_holds_messages():
    ctx = Context(system_prompt=["sys"], messages=[UserMessage(content="hi")])
    assert ctx.messages[0].role == "user" and ctx.tools == []


def test_context_copies_input_lists():
    message = UserMessage(content="hi")
    system_prompt = ["sys"]
    messages = [message]
    tools = [object()]

    ctx = Context(system_prompt=system_prompt, messages=messages, tools=tools)
    system_prompt.append("later")
    messages.clear()
    tools.append(object())

    assert ctx.system_prompt == ["sys"]
    assert ctx.messages == [message]
    assert len(ctx.tools) == 1
