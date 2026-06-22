import json

import httpx
import pytest

from nanoagent.ai import (
    AssistantMessage,
    Context,
    Model,
    StopReason,
    Tool,
    ToolCall,
    UserMessage,
    Usage,
)
from nanoagent.ai.providers.openai import OpenAIProvider, encode_request, parse_sse_line


def test_encode_request_maps_system_and_user():
    ctx = Context(
        system_prompt=["be brief"],
        messages=[UserMessage(content="hi")],
        tools=[Tool(name="echo", description="e", parameters={"type": "object"})],
    )
    payload = encode_request(
        Model(id="gpt-x", api="openai-completions", provider="openai"), ctx, None
    )
    assert payload["messages"][0] == {"role": "system", "content": "be brief"}
    assert payload["messages"][1]["role"] == "user"
    assert payload["tools"][0]["function"]["name"] == "echo"
    assert payload["stream"] is True


def test_encode_request_maps_assistant_tool_calls():
    ctx = Context(
        messages=[
            AssistantMessage(
                content=[ToolCall(id="t1", name="echo", arguments={"x": 1})],
                model="m",
                provider="openai",
                api="openai-completions",
                usage=Usage(),
                stop_reason=StopReason.TOOL_USE,
            )
        ]
    )
    payload = encode_request(Model(id="m", api="openai-completions", provider="openai"), ctx, None)
    assert payload["messages"][0]["tool_calls"][0]["function"]["name"] == "echo"


def test_parse_sse_line_done_and_data():
    assert parse_sse_line("data: [DONE]") == {"__done__": True}
    assert parse_sse_line('data: {"a": 1}') == {"a": 1}
    assert parse_sse_line(": comment") is None
    assert parse_sse_line("") is None


@pytest.mark.asyncio
async def test_stream_parses_text_and_tool_calls(monkeypatch):
    sse = (
        'data: {"choices":[{"delta":{"content":"he"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request):
        return httpx.Response(200, text=sse)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **k: real_client(transport=httpx.MockTransport(handler)),
    )
    prov = OpenAIProvider()
    model = Model(id="gpt-x", api="openai-completions", provider="openai")
    events = [
        e
        async for e in prov.stream(
            model, Context(messages=[UserMessage(content="hi")]), None
        )
    ]
    assert events[-1].type == "done"
    assert events[-1].message.content[0].text == "hello"
    assert events[-1].message.stop_reason is StopReason.STOP


@pytest.mark.asyncio
async def test_stream_emits_tool_call_argument_deltas(monkeypatch):
    chunks = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "tc1", "function": {"name": "echo", "arguments": '{"x"'}}
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ": 1}"}}]}}
            ]
        },
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]
    sse = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    sse += "data: [DONE]\n\n"

    def handler(request):
        return httpx.Response(200, text=sse)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **k: real_client(transport=httpx.MockTransport(handler)),
    )
    prov = OpenAIProvider()
    model = Model(id="gpt-x", api="openai-completions", provider="openai")
    events = [e async for e in prov.stream(model, Context(messages=[]), None)]

    assert [e.type for e in events if e.type.startswith("toolcall_")] == [
        "toolcall_start",
        "toolcall_delta",
        "toolcall_delta",
        "toolcall_end",
    ]
    assert events[-1].message.content[0].arguments == {"x": 1}
    assert events[-1].message.stop_reason is StopReason.TOOL_USE
