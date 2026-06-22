from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from nanoagent.ai.events import (
    AssistantMessageEvent,
    StreamDone,
    StreamError,
    StreamStart,
    TextDelta,
    TextEnd,
    TextStart,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
)
from nanoagent.ai.errors import ProviderError
from nanoagent.ai.messages import AssistantMessage, Context, Message, TextContent, ToolCall
from nanoagent.ai.model import Model
from nanoagent.ai.options import StreamOptions
from nanoagent.ai.provider import register_provider
from nanoagent.ai.stop_reason import StopReason

_FINISH_MAP = {
    "stop": StopReason.STOP,
    "length": StopReason.LENGTH,
    "tool_calls": StopReason.TOOL_USE,
}


def _encode_message(m: Message) -> dict:
    if m.role == "user":
        text = (
            m.content
            if isinstance(m.content, str)
            else "".join(b.text for b in m.content if getattr(b, "type", None) == "text")
        )
        return {"role": "user", "content": text}
    if m.role == "assistant":
        text = "".join(b.text for b in m.content if b.type == "text")
        tool_calls = [
            {
                "id": b.id,
                "type": "function",
                "function": {"name": b.name, "arguments": json.dumps(b.arguments)},
            }
            for b in m.content
            if b.type == "toolCall"
        ]
        out: dict = {"role": "assistant", "content": text or None}
        if tool_calls:
            out["tool_calls"] = tool_calls
        return out
    # toolResult
    text = "".join(b.text for b in m.content if getattr(b, "type", None) == "text")
    return {"role": "tool", "tool_call_id": m.tool_call_id, "content": text}


def encode_request(model: Model, context: Context, options: StreamOptions | None) -> dict:
    messages: list[dict] = []
    for sp in context.system_prompt:
        messages.append({"role": "system", "content": sp})
    messages.extend(_encode_message(m) for m in context.messages)
    payload: dict[str, Any] = {"model": model.id, "messages": messages, "stream": True}
    if context.tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in context.tools
        ]
    if options:
        if options.temperature is not None:
            payload["temperature"] = options.temperature
        if options.max_tokens is not None:
            payload["max_tokens"] = options.max_tokens
    return payload


def parse_sse_line(line: str) -> dict | None:
    line = line.strip()
    if not line or line.startswith(":"):
        return None
    if not line.startswith("data:"):
        return None
    data = line[len("data:") :].strip()
    if data == "[DONE]":
        return {"__done__": True}
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


class OpenAIProvider:
    async def stream(
        self, model: Model, context: Context, options: StreamOptions | None
    ) -> AsyncIterator[AssistantMessageEvent]:
        opts = options or StreamOptions()
        base = opts.base_url or model.base_url or "https://api.openai.com/v1"
        url = base.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if opts.api_key:
            headers["Authorization"] = f"Bearer {opts.api_key}"
        payload = encode_request(model, context, options)
        msg = AssistantMessage.empty(model.id, model.provider, model.api)
        yield StreamStart()
        text_parts: list[str] = []
        tool_acc: dict[int, dict] = {}
        finish = "stop"
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code >= 400:
                        body = (await resp.aread()).decode("utf-8", "replace")
                        raise ProviderError(
                            f"HTTP {resp.status_code}: {body}", status=resp.status_code
                        )
                    async for line in resp.aiter_lines():
                        chunk = parse_sse_line(line)
                        if not chunk or chunk.get("__done__"):
                            continue
                        delta = chunk["choices"][0].get("delta", {})
                        if delta.get("content"):
                            if not text_parts:
                                yield TextStart(content_index=0)
                            text_parts.append(delta["content"])
                            yield TextDelta(content_index=0, delta=delta["content"])
                        for tc in delta.get("tool_calls", []):
                            idx = tc["index"]
                            if idx not in tool_acc:
                                tool_acc[idx] = {
                                    "id": "",
                                    "name": "",
                                    "args": "",
                                    "content_index": (1 if text_parts else 0) + idx,
                                }
                                yield ToolCallStart(content_index=tool_acc[idx]["content_index"])
                            slot = tool_acc[idx]
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function", {})
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["args"] += fn["arguments"]
                                yield ToolCallDelta(
                                    content_index=slot["content_index"],
                                    delta=fn["arguments"],
                                )
                        if chunk["choices"][0].get("finish_reason"):
                            finish = chunk["choices"][0]["finish_reason"]
        except ProviderError as e:
            msg.stop_reason = StopReason.ERROR
            msg.error_message = str(e)
            yield StreamError(message=msg)
            return
        except httpx.HTTPError as e:
            msg.stop_reason = StopReason.ERROR
            msg.error_message = str(e)
            yield StreamError(message=msg)
            return
        if text_parts:
            full = "".join(text_parts)
            msg.content.append(TextContent(text=full))
            yield TextEnd(content_index=0, text=full)
        for _, slot in sorted(tool_acc.items()):
            try:
                args = json.loads(slot["args"]) if slot["args"] else {}
            except json.JSONDecodeError:
                args = {}
            tc = ToolCall(id=slot["id"], name=slot["name"], arguments=args)
            msg.content.append(tc)
            yield ToolCallEnd(content_index=slot["content_index"], tool_call=tc)
        msg.stop_reason = _FINISH_MAP.get(finish, StopReason.STOP)
        yield StreamDone(message=msg)


def register_openai() -> None:
    register_provider("openai-completions", OpenAIProvider())
