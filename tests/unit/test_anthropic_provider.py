import httpx
from providers.anthropic import AnthropicProvider
from providers.base import MessageEnd, ModelRequest, TextDelta
from runtime.blocks import Message


async def test_text_stream():
    body = 'data: {"type":"message_start","message":{"model":"m","usage":{"input_tokens":2}}}\n\n' \
           'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"你好"}}\n\n' \
           'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3}}\n\n' \
           'data: {"type":"message_stop"}\n\n'
    provider = AnthropicProvider("key", transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body)))
    events = [e async for e in provider.stream(ModelRequest("s", [Message.user("hi")], [], "m"))]
    assert any(isinstance(e, TextDelta) for e in events)
    assert isinstance(events[-1], MessageEnd) and events[-1].usage.output_tokens == 3
