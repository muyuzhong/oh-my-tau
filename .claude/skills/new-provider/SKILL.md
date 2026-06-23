---
name: new-provider
description: Scaffold a nanoagent.ai.providers adapter — map a provider's stream chunks onto nanoagent stream events, register it, and add a network-free test. Use when adding support for a new model API/provider.
disable-model-invocation: true
---

# Add a NanoAgent provider adapter

Provider adapters live under `src/nanoagent/ai/providers/`. They are the **only**
place provider-specific wire details belong. Keep policy out: no API-key discovery,
no default-provider selection, no token budgets — those are harness concerns.

Reference implementations:
- `src/nanoagent/ai/providers/mock.py` — minimal, canonical event sequence.
- `src/nanoagent/ai/providers/openai.py` — real HTTP/SSE adapter via `httpx`.

## Steps

1. **Create** `src/nanoagent/ai/providers/<name>.py`.

2. **Implement a dispatch object** with one method:
   ```python
   def stream(self, model, context, options) -> AsyncIterator[AssistantMessageEvent]
   ```
   (See `_MockDispatch` in `mock.py` and the module-level `stream` in `openai.py`.)

3. **Emit the event sequence** (from `nanoagent.ai.events`), accumulating into a
   single `AssistantMessage` as you go:
   ```
   StreamStart
     per content block, by content_index:
       text:     TextStart    -> TextDelta*    -> TextEnd
       thinking: ThinkingStart -> ThinkingDelta* -> ThinkingEnd
       tool:     ToolCallStart -> ToolCallDelta* -> ToolCallEnd(tool_call=ToolCall(...))
   StreamDone(message=<final AssistantMessage>)      # success
   StreamError(message=<msg with stop_reason=ERROR>) # failure (return after)
   ```
   Build the message with `AssistantMessage.empty(model.id, model.provider, model.api)`.

4. **Map the provider finish reason** to `nanoagent.ai.StopReason`
   (`STOP`, `LENGTH`, `TOOL_USE`, `ERROR`) and set `message.stop_reason`. See
   `_FINISH_MAP` in `openai.py`. Do not confuse this with `nanoagent.agent.StopReason`.

5. **Populate `message.usage`** with a `Usage(input=..., output=..., total_tokens=...)`
   when the provider reports it.

6. **Register** the adapter so the loop can find it:
   ```python
   from nanoagent.ai.provider import register_provider
   register_provider("<api>", <DispatchInstance>())
   ```

7. **Add a test** at `tests/ai/test_<name>.py`. Drive HTTP adapters with a fake
   `httpx` transport (see `tests/ai/test_openai.py`); never hit the network or use
   real API keys.

## Boundaries (must hold)

- All provider-specific code stays under `nanoagent.ai.providers`.
- `ai` must not import `agent`. Keep `.importlinter` passing.
- No provider/model/key selection or product policy in the adapter.

## Verify

```bash
lint-imports
pytest -q
```
