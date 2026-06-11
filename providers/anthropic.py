"""Anthropic Messages API 适配器：负责块消息序列化、SSE 翻译与异常归一化。"""
from __future__ import annotations
import json, os
import httpx
from providers.base import *
from runtime.blocks import TextBlock, ToolResultBlock, ToolUseBlock, Usage


def _map_status(status, headers, body):
    if status == 429: return RateLimitError(float(headers.get("retry-after", 30)), body[:200])
    if status in (401, 403): return ProviderAuthError(body[:200])
    if status == 400: return ProviderBadRequestError(body[:200])
    return ProviderServerError(f"HTTP {status}: {body[:200]}")


class AnthropicProvider(ModelProvider):
    def __init__(self, api_key=None, base_url="https://api.anthropic.com", timeout=60.0, transport=None):
        self.api_key, self.base_url, self.timeout, self.transport = api_key or os.getenv("ANTHROPIC_API_KEY", ""), base_url.rstrip("/"), timeout, transport

    def _serialize_messages(self, messages):
        output = []
        for message in messages:
            blocks = []
            for block in message.content:
                if isinstance(block, TextBlock): blocks.append({"type": "text", "text": block.text})
                elif isinstance(block, ToolUseBlock): blocks.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
                elif isinstance(block, ToolResultBlock): blocks.append({"type": "tool_result", "tool_use_id": block.tool_use_id, "content": block.content, "is_error": block.is_error})
            output.append({"role": message.role, "content": blocks})
        return output

    async def stream(self, request):
        payload = {"model": request.model, "system": request.system, "messages": self._serialize_messages(request.messages),
                   "max_tokens": request.max_tokens, "stream": True}
        if request.tools: payload["tools"] = request.tools
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        input_tokens = output_tokens = 0
        stop_reason, tool_by_index = None, {}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                async with client.stream("POST", f"{self.base_url}/v1/messages", json=payload, headers=headers) as response:
                    if response.status_code != 200:
                        raise _map_status(response.status_code, response.headers, (await response.aread()).decode(errors="replace"))
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"): continue
                        data = json.loads(line[5:].strip()); kind = data.get("type")
                        if kind == "message_start":
                            input_tokens = data["message"].get("usage", {}).get("input_tokens", 0)
                            yield MessageStart(data["message"].get("model", request.model))
                        elif kind == "content_block_start" and data["content_block"]["type"] == "tool_use":
                            block = data["content_block"]; tool_by_index[data["index"]] = block["id"]; yield ToolUseStart(block["id"], block["name"])
                        elif kind == "content_block_delta":
                            delta = data["delta"]
                            if delta["type"] == "text_delta": yield TextDelta(delta["text"])
                            elif delta["type"] == "thinking_delta": yield ThinkingDelta(delta["thinking"])
                            elif delta["type"] == "input_json_delta" and data["index"] in tool_by_index: yield ToolInputDelta(tool_by_index[data["index"]], delta["partial_json"])
                        elif kind == "content_block_stop" and data["index"] in tool_by_index: yield ToolUseEnd(tool_by_index.pop(data["index"]))
                        elif kind == "message_delta":
                            stop_reason = data["delta"].get("stop_reason") or stop_reason
                            output_tokens = data.get("usage", {}).get("output_tokens", output_tokens)
                        elif kind == "message_stop": yield MessageEnd(stop_reason or "end_turn", Usage(input_tokens, output_tokens))
        except httpx.TimeoutException as error: raise ProviderTimeoutError(str(error)) from error
        except httpx.HTTPError as error: raise ProviderServerError(str(error)) from error
