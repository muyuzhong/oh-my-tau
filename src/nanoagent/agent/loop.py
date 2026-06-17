from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from nanoagent.ai import Model, StreamAccumulator, StreamOptions, TextContent, ToolResultMessage, stream
from nanoagent.ai import StopReason as WireStopReason
from nanoagent.agent.context import TransformContext, assemble_context
from nanoagent.agent.control import ControlSource
from nanoagent.agent.events import (
    AgentEnd,
    AgentEvent,
    AgentStart,
    MessageEnd,
    MessageStart,
    MessageUpdate,
    ToolExecutionEnd,
    ToolExecutionStart,
    TurnEnd,
    TurnStart,
)
from nanoagent.agent.messages import AgentMessage, ConvertToLlm, default_convert_to_llm
from nanoagent.agent.result import RunResult, StopReason
from nanoagent.agent.tools import AgentTool, execute_tool_calls


@dataclass
class AgentLoopConfig:
    model: Model
    convert_to_llm: ConvertToLlm = default_convert_to_llm
    transform_context: TransformContext | None = None
    max_turns: int = 10
    control: ControlSource | None = None
    before_tool_call: Callable[..., Awaitable[dict | None]] | None = None
    get_steering_messages: Callable[[], Awaitable[list[AgentMessage]]] | None = None
    stream_fn: Callable[..., Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning: str | None = None


def _options(config: AgentLoopConfig, signal: Any) -> StreamOptions:
    return StreamOptions(
        signal=signal,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        reasoning=config.reasoning,
    )


async def _stream_one_turn(model, ctx, stream_fn, options):
    acc = StreamAccumulator(model_id=model.id, provider=model.provider, api=model.api)
    assistant = None
    async for event in stream_fn(model, ctx, options):
        acc.add(event)
        if event.type == "start":
            yield ("message_start", acc.message)
        elif event.type in ("done", "error"):
            assistant = event.message
        else:
            yield ("message_update", acc.message, event)
    yield ("__assistant__", assistant)


async def agent_loop(
    *,
    prompts: list[AgentMessage],
    system_prompt: list[str],
    messages: list[AgentMessage],
    tools: list[AgentTool],
    config: AgentLoopConfig,
    signal: Any = None,
) -> AsyncIterator[AgentEvent]:
    history: list[AgentMessage] = [*messages, *prompts]
    produced: list[AgentMessage] = [*prompts]
    stream_fn = config.stream_fn or stream

    yield AgentStart()
    for p in prompts:
        yield MessageStart(message=p)
        yield MessageEnd(message=p)

    turn = 0
    while True:
        if signal is not None and signal.aborted:
            last_id = produced[-1].id if produced else None
            yield AgentEnd(
                messages=produced,
                result=RunResult(reason=StopReason.ABORTED, final_message_id=last_id),
            )
            return
        if turn >= config.max_turns:
            last_id = produced[-1].id if produced else None
            yield AgentEnd(
                messages=produced,
                result=RunResult(reason=StopReason.MAX_TURNS, final_message_id=last_id),
            )
            return
        turn += 1
        yield TurnStart()

        ctx = await assemble_context(
            system_prompt, history, tools, config.convert_to_llm, config.transform_context, signal
        )
        assistant = None
        async for item in _stream_one_turn(config.model, ctx, stream_fn, _options(config, signal)):
            if item[0] == "message_start":
                yield MessageStart(message=item[1])
            elif item[0] == "message_update":
                yield MessageUpdate(message=item[1], assistant_event=item[2])
            else:
                assistant = item[1]
        assert assistant is not None
        history.append(assistant)
        produced.append(assistant)
        yield MessageEnd(message=assistant)

        if assistant.stop_reason in (WireStopReason.ERROR, WireStopReason.ABORTED):
            yield TurnEnd(message=assistant, tool_results=[])
            reason = (
                StopReason.ABORTED
                if assistant.stop_reason == WireStopReason.ABORTED
                else StopReason.ERROR
            )
            yield AgentEnd(
                messages=produced,
                result=RunResult(
                    reason=reason, final_message_id=assistant.id, error=assistant.error_message
                ),
            )
            return

        tool_calls = [c for c in assistant.content if getattr(c, "type", None) == "toolCall"]
        runnable = assistant.stop_reason in (WireStopReason.TOOL_USE, WireStopReason.STOP)
        if not (runnable and tool_calls):
            yield TurnEnd(message=assistant, tool_results=[])
            yield AgentEnd(
                messages=produced,
                result=RunResult(reason=StopReason.COMPLETED, final_message_id=assistant.id),
            )
            return

        for c in tool_calls:
            yield ToolExecutionStart(tool_call_id=c.id, tool_name=c.name, args=c.arguments)

        approved: list = []
        denied_results: dict[str, ToolResultMessage] = {}
        for c in tool_calls:
            ok = True
            if config.control is not None:
                ok = await config.control.request_approval(c, "exec")
            if ok:
                approved.append(c)
            else:
                denied_results[c.id] = ToolResultMessage(
                    tool_call_id=c.id,
                    tool_name=c.name,
                    content=[TextContent(text="Tool approval denied")],
                    is_error=True,
                )

        executed = await execute_tool_calls(
            approved, tools, signal=signal, before_tool_call=config.before_tool_call
        )
        executed_by_id = {r.tool_call_id: r for r in executed}
        tool_results = [denied_results.get(c.id) or executed_by_id[c.id] for c in tool_calls]
        for c, r in zip(tool_calls, tool_results):
            yield ToolExecutionEnd(
                tool_call_id=c.id, tool_name=c.name, result=r, is_error=r.is_error
            )
        for r in tool_results:
            yield MessageStart(message=r)
            history.append(r)
            produced.append(r)
            yield MessageEnd(message=r)
        yield TurnEnd(message=assistant, tool_results=tool_results)
