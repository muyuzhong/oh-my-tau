from providers.base import MessageEnd, MessageStart, ProviderAuthError, RateLimitError, TextDelta
from providers.mock import MockProvider
from runtime import events as ev
from runtime.blocks import Usage
from runtime.context import ContextAssembler, RetryPolicy, TokenLedger
from runtime.engine import AgentLoop
from runtime.executor import ToolRegistry
from runtime.result import StopReason
from runtime.state import SessionState
from runtime.supervisor import ConstraintValidator, Supervisor
from tests.helpers import EchoTool, FakeSleep, collect


def make_loop(tmp_path, script, tools=(), **kwargs):
    registry = ToolRegistry()
    for tool in tools: registry.register(tool)
    sleep = FakeSleep()
    return AgentLoop(MockProvider(script), registry, state=SessionState(transcript_dir=str(tmp_path)),
                     retry_policy=RetryPolicy(sleep=sleep), **kwargs), sleep


async def test_text_only_completes(tmp_path):
    loop, _ = make_loop(tmp_path, [MockProvider.text_turn("你好")])
    events = await collect(loop.run("hi"))
    assert events[-1].reason == "completed" and len(loop.state.messages) == 2
    # 结构化结果：COMPLETED 无 error/detail，final_message_id 指向本轮最后一条 assistant 消息。
    result = events[-1].result
    assert result.reason is StopReason.COMPLETED
    assert result.error is None and result.detail is None
    assert result.final_message_id == loop.state.messages[-1].message_id


async def test_tool_roundtrip(tmp_path):
    loop, _ = make_loop(tmp_path, [MockProvider.tool_turn("echo", {"text": "hi"}, "t1"), MockProvider.text_turn("完成")], [EchoTool()])
    events = await collect(loop.run("hi"))
    assert events[-1].reason == "completed" and any(isinstance(e, ev.ToolResultReceived) for e in events)
    assert len(loop.state.messages) == 4


async def test_retryable_error_then_success(tmp_path):
    loop, sleep = make_loop(tmp_path, [RateLimitError(0), MockProvider.text_turn("恢复")])
    events = await collect(loop.run("hi"))
    assert events[-1].reason == "completed" and sleep.calls == [0]


async def test_non_retryable_error_ends_run(tmp_path):
    loop, _ = make_loop(tmp_path, [ProviderAuthError("bad")])
    events = await collect(loop.run("hi"))
    assert events[-1].reason == "provider_error"
    assert events[-1].result.reason is StopReason.PROVIDER_ERROR
    assert events[-1].result.error  # 非空：携带 provider 错误信息


async def test_broken_tool_json_fed_back_as_error(tmp_path):
    from providers.base import MessageEnd, MessageStart, ToolInputDelta, ToolUseEnd, ToolUseStart
    from runtime.blocks import Usage
    broken = [MessageStart("mock-model"), ToolUseStart("t1", "echo"),
              ToolInputDelta("t1", '{"text": '), ToolUseEnd("t1"),
              MessageEnd("tool_use", Usage(1, 1))]
    loop, _ = make_loop(tmp_path, [broken, MockProvider.text_turn("知道了")], [EchoTool()])
    events = await collect(loop.run("hi"))
    assert any(isinstance(event, ev.ToolResultReceived) and event.is_error for event in events)
    assert events[-1].reason == "completed"


async def test_token_budget_stops_loop(tmp_path):
    loop, _ = make_loop(tmp_path, [MockProvider.tool_turn("echo", {"text": "a"})], [EchoTool()], ledger=TokenLedger(max_api_calls=1))
    events = await collect(loop.run("hi"))
    assert events[-1].reason == "token_budget"
    assert events[-1].result.reason is StopReason.TOKEN_BUDGET
    assert events[-1].result.error is None and events[-1].result.detail is None


async def test_max_turns(tmp_path):
    loop, _ = make_loop(tmp_path, [MockProvider.tool_turn("echo", {"text": "a"})] * 2, [EchoTool()], max_turns=2)
    events = await collect(loop.run("hi"))
    assert events[-1].reason == "max_turns"
    assert events[-1].result.reason is StopReason.MAX_TURNS
    assert events[-1].result.error is None and events[-1].result.detail is None


async def test_max_tokens_is_not_reported_as_completed(tmp_path):
    truncated = [MessageStart("mock-model"), TextDelta("未完成"), MessageEnd("max_tokens", Usage(1, 5))]
    loop, _ = make_loop(tmp_path, [truncated])
    events = await collect(loop.run("hi"))
    assert events[-1].reason == "max_tokens"
    assert events[-1].result.reason is StopReason.MAX_TOKENS
    assert events[-1].result.error is None


async def test_incomplete_provider_stream_is_not_reported_as_completed(tmp_path):
    incomplete = [MessageStart("mock-model"), TextDelta("半截")]
    loop, _ = make_loop(tmp_path, [incomplete])
    events = await collect(loop.run("hi"))
    assert events[-1].reason == "incomplete_stream"
    assert len(loop.state.messages) == 1
    assert events[-1].result.reason is StopReason.INCOMPLETE_STREAM
    assert events[-1].result.error == "Provider 流未正常结束"
    assert events[-1].result.final_message_id is None  # 未产出 assistant 消息


async def test_context_overflow_ends_with_explicit_reason(tmp_path):
    assembler = ContextAssembler("s", context_window=100, keep_recent=2)
    loop, _ = make_loop(tmp_path, [MockProvider.text_turn("不会调用")], assembler=assembler)
    events = await collect(loop.run("x" * 10_000))
    assert events[-1].reason == "context_overflow"
    assert loop.provider.requests == []
    assert len(loop.state.messages) == 1
    assert events[-1].result.reason is StopReason.CONTEXT_OVERFLOW
    assert events[-1].result.error  # 非空：携带溢出信息
    assert events[-1].result.final_message_id is None


async def test_supervisor_terminate_carries_detail(tmp_path):
    # ConstraintValidator(max_total_tool_calls=0)：第一轮工具调用后即超限 → 终止。
    loop, _ = make_loop(
        tmp_path,
        [MockProvider.tool_turn("echo", {"text": "a"}, "t1")],
        [EchoTool()],
        supervisor=Supervisor([ConstraintValidator(max_total_tool_calls=0)]),
    )
    events = await collect(loop.run("hi"))
    result = events[-1].result
    assert result.reason is StopReason.SUPERVISOR_TERMINATE
    # 监督者的动态原因被保留在 detail，而不是丢失或塞进 error。
    assert result.detail == "constraint:max_tool_calls(1)"
    assert result.detail.startswith("constraint:")
    assert result.error is None
    assert result.final_message_id is not None  # 已产出 assistant 消息


async def test_fatal_exception_does_not_escape_generator(tmp_path):
    # 非 ProviderError 异常在流中抛出：必须被兜底为 FATAL，生成器正常收尾、绝不外抛。
    loop, _ = make_loop(tmp_path, [RuntimeError("boom")])
    events = await collect(loop.run("hi"))  # 不抛异常本身就是被测不变量
    assert isinstance(events[-1], ev.AgentEnded)
    result = events[-1].result
    assert result.reason is StopReason.FATAL
    assert result.error and "boom" in result.error
    assert result.final_message_id is None
