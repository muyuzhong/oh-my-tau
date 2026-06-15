"""Mono 运行时核心：流事件累积器与事件驱动 AgentLoop。"""
from __future__ import annotations

import json

from providers.base import MessageEnd, ProviderError, TextDelta, ThinkingDelta, ToolInputDelta, ToolUseEnd, ToolUseStart
from runtime.blocks import Message, TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock
from runtime.context import ContextAssembler, ContextOverflowError, RetryPolicy, TokenLedger
from runtime.control import Abort, Approve, Pause, Steer
from runtime import events as ev
from runtime.executor import ToolExecutor, ToolRegistry
from runtime.result import RunResult, StopReason
from runtime.state import SessionState


class StreamAccumulator:
    """按工具调用 id 累积交错的流事件，最终构造一条完整 assistant 消息。"""
    def __init__(self):
        self.blocks, self._text, self._thinking, self._open_tools = [], "", "", {}
        self.usage = self.stop_reason = None

    def _flush_text(self):
        # 先推理后正文，保持 Provider 流中的语义顺序。
        if self._thinking: self.blocks.append(ThinkingBlock(self._thinking)); self._thinking = ""
        if self._text: self.blocks.append(TextBlock(self._text)); self._text = ""

    def feed(self, event):
        if isinstance(event, TextDelta): self._text += event.text
        elif isinstance(event, ThinkingDelta): self._thinking += event.thinking
        elif isinstance(event, ToolUseStart):
            self._flush_text()
            self._open_tools[event.id] = {"name": event.name, "buf": ""}
        elif isinstance(event, ToolInputDelta) and event.id in self._open_tools:
            self._open_tools[event.id]["buf"] += event.partial_json
        elif isinstance(event, ToolUseEnd):
            entry = self._open_tools.pop(event.id, None)
            if entry:
                raw = entry["buf"].strip()
                try: parsed = json.loads(raw) if raw else {}
                except json.JSONDecodeError: parsed = {"__parse_error__": raw}
                self.blocks.append(ToolUseBlock(entry["name"], parsed, event.id))
        elif isinstance(event, MessageEnd):
            self.usage, self.stop_reason = event.usage, event.stop_reason

    def result(self):
        self._flush_text()
        return Message.assistant(self.blocks, self.usage)

    def is_complete(self):
        return self.stop_reason is not None and not self._open_tools


class AgentLoop:
    """以异步生成器发布所有进度；控制面和监督者均为可选依赖。"""
    def __init__(self, provider, registry=None, *, system_prompt="You are Mono, a helpful agent.",
                 model="mock-model", max_tokens=4096, max_turns=10, state=None, ledger=None,
                 executor=None, assembler=None, supervisor=None, control=None, retry_policy=None):
        self.provider, self.registry = provider, registry or ToolRegistry()
        self.state, self.ledger = state or SessionState(), ledger or TokenLedger()
        self.executor = executor or ToolExecutor(self.registry)
        self.assembler = assembler or ContextAssembler(system_prompt, self.registry, model, max_tokens)
        self.supervisor, self.control, self.retry, self.max_turns = supervisor, control, retry_policy or RetryPolicy(), max_turns

    def _needs_approval(self, call):
        return bool(getattr(self.registry.get(call.name), "requires_approval", False))

    async def run(self, user_input):
        self.state.append(Message.user(user_input))
        yield ev.AgentStarted(self.state.session_id)
        reason = StopReason.MAX_TURNS
        error = None
        detail = None
        last_assistant_id = None
        try:
            for turn in range(self.max_turns):
                # 安全点：仅在完整消息之间应用转向、暂停与中断。
                if self.control:
                    for command in self.control.drain_nowait():
                        if isinstance(command, Steer):
                            self.state.append(Message.user(command.text)); yield ev.Steered(command.text)
                        elif isinstance(command, Pause):
                            yield ev.Paused()
                            if not isinstance(await self.control.wait_resume(), Abort): yield ev.Resumed()
                    if self.control.abort_requested: reason = StopReason.USER_ABORT; break
                if not self.ledger.budget_ok(): reason = StopReason.TOKEN_BUDGET; break
                yield ev.TurnStarted(turn)
                try:
                    request, compacted = self.assembler.build(self.state.messages)
                except ContextOverflowError as overflow:
                    yield ev.ErrorEvent(type(overflow).__name__, str(overflow))
                    reason = StopReason.CONTEXT_OVERFLOW; error = str(overflow)
                    break
                if compacted: yield ev.ContextCompacted(*compacted)

                attempt = 0
                while True:
                    accumulator, fatal, aborted = StreamAccumulator(), None, False
                    try:
                        async for stream_event in self.provider.stream(request):
                            if self.control and self.control.abort_requested: aborted = True; break
                            accumulator.feed(stream_event)
                            if isinstance(stream_event, TextDelta): yield ev.TextDeltaEvent(stream_event.text)
                            elif isinstance(stream_event, ThinkingDelta): yield ev.ThinkingDeltaEvent(stream_event.thinking)
                            elif isinstance(stream_event, ToolUseStart): yield ev.ToolCallStarted(stream_event.id, stream_event.name)
                        break
                    except ProviderError as provider_error:
                        attempt += 1
                        if not self.retry.should_retry(provider_error, attempt): fatal = provider_error; break
                        delay = self.retry.backoff_for(provider_error, attempt)
                        yield ev.InferenceRetrying(attempt, delay)
                        await self.retry.sleep(delay)
                if aborted: reason = StopReason.USER_ABORT; break
                if fatal:
                    yield ev.ErrorEvent(type(fatal).__name__, str(fatal))
                    reason = StopReason.PROVIDER_ERROR; error = str(fatal)
                    break
                if not accumulator.is_complete():
                    yield ev.ErrorEvent("IncompleteStream", "Provider 流未正常结束")
                    reason = StopReason.INCOMPLETE_STREAM; error = "Provider 流未正常结束"
                    break

                message = accumulator.result()
                if accumulator.usage: self.ledger.record(accumulator.usage)
                self.state.append(message)
                last_assistant_id = message.message_id
                yield ev.AssistantMessageEnd(accumulator.stop_reason or "end_turn")
                if accumulator.stop_reason == "max_tokens":
                    reason = StopReason.MAX_TOKENS
                    break
                calls = message.get_tool_calls()
                if not calls: reason = StopReason.COMPLETED; break

                needs, approved, denied = [c for c in calls if self._needs_approval(c)], [c for c in calls if not self._needs_approval(c)], []
                if needs:
                    if not self.control: denied += needs
                    else:
                        yield ev.ApprovalRequested(needs)
                        pending, approved_ids = {c.id for c in needs}, set()
                        while pending:
                            decision = await self.control.wait_decision()
                            if isinstance(decision, Abort): aborted = True; break
                            ids = set(decision.ids) if decision.ids is not None else set(pending)
                            if isinstance(decision, Approve): approved_ids |= ids & pending
                            pending -= ids
                        if aborted: reason = StopReason.USER_ABORT; break
                        approved += [c for c in needs if c.id in approved_ids]
                        denied = [c for c in needs if c.id not in approved_ids]
                for call in approved: yield ev.ToolExecutionStarted(call.id, call.name)
                results = await self.executor.execute_all(approved)
                results += [ToolResultBlock(c.id, "用户拒绝执行该工具。", True, "PermissionDenied") for c in denied]
                by_id, names = {r.tool_use_id: r for r in results}, {c.id: c.name for c in calls}
                ordered = [by_id[c.id] for c in calls]
                for result in ordered:
                    yield ev.ToolResultReceived(result.tool_use_id, names[result.tool_use_id], result.is_error, result.content[:120])
                self.state.append(Message.tool_results(ordered))
                if self.supervisor:
                    verdict = self.supervisor.review(self.state)
                    if verdict.action == "terminate":
                        reason = StopReason.SUPERVISOR_TERMINATE; detail = verdict.reason
                        break
                    if verdict.action == "inject":
                        self.state.append(Message.user(verdict.message)); yield ev.SupervisorInjected(verdict.reason or "supervisor")
                yield ev.TurnEnded(turn)
        except Exception as unexpected:
            yield ev.ErrorEvent(type(unexpected).__name__, str(unexpected))
            reason = StopReason.FATAL; error = str(unexpected)
        yield ev.AgentEnded(RunResult(reason, last_assistant_id, error, detail))
