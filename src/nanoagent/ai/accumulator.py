from __future__ import annotations

from typing import AsyncIterator

from nanoagent.ai.events import AssistantMessageEvent
from nanoagent.ai.messages import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
)


class StreamAccumulator:
    """Fold incremental events into one AssistantMessage (consumer/UI helper)."""

    def __init__(self, model_id: str, provider: str, api: str):
        self._msg = AssistantMessage.empty(model_id, provider, api)

    @property
    def message(self) -> AssistantMessage:
        return self._msg

    def add(self, event: AssistantMessageEvent) -> None:
        t = event.type
        if t == "text_start":
            self._msg.content.append(TextContent(text=""))
        elif t == "text_delta":
            block = self._msg.content[event.content_index]
            if isinstance(block, TextContent):
                block.text += event.delta
        elif t == "text_end":
            self._msg.content[event.content_index] = TextContent(text=event.text)
        elif t == "thinking_start":
            self._msg.content.append(ThinkingContent(thinking=""))
        elif t == "thinking_end":
            self._msg.content[event.content_index] = ThinkingContent(thinking=event.thinking)
        elif t == "toolcall_start":
            self._msg.content.append(ToolCall(id="", name="", arguments={}))
        elif t == "toolcall_end":
            self._msg.content[event.content_index] = event.tool_call
        elif t in ("done", "error"):
            # Adopt the provider's final message, but keep the streaming id so a
            # consumer sees one stable message identity across message_start ->
            # message_update -> message_end (see agent event contract G2).
            event.message.id = self._msg.id
            self._msg = event.message


async def accumulate(events: AsyncIterator[AssistantMessageEvent]) -> AssistantMessage:
    acc: StreamAccumulator | None = None
    async for event in events:
        if acc is None:
            acc = StreamAccumulator(model_id="", provider="", api="")
        acc.add(event)
    if acc is None:
        raise ValueError("stream produced no events")
    return acc.message
