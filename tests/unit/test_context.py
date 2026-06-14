import pytest

from providers.base import ProviderAuthError, ProviderTimeoutError, RateLimitError
from runtime import context
from runtime.blocks import Message, TextBlock, ToolResultBlock, ToolUseBlock, Usage
from runtime.context import ContextAssembler, RetryPolicy, TokenLedger
from runtime.state import SessionState


def test_ledger_records_and_checks_budget():
    ledger = TokenLedger(100, 2)
    ledger.record(Usage(40, 20))
    assert ledger.total_tokens == 60 and ledger.budget_ok()
    ledger.record(Usage(30, 20))
    assert not ledger.budget_ok()


def test_retry_policy_decisions():
    policy = RetryPolicy(3, 1, 8)
    assert policy.should_retry(RateLimitError(5), 1)
    assert not policy.should_retry(ProviderTimeoutError(), 4)
    assert not policy.should_retry(ProviderAuthError(), 1)
    assert policy.backoff_for(RateLimitError(5), 1) == 5
    assert policy.backoff_for(ProviderTimeoutError(), 5) == 8


def state_with(tmp_path, messages):
    state = SessionState(transcript_dir=str(tmp_path))
    for message in messages: state.append(message)
    return state


def test_no_compaction_under_threshold(tmp_path):
    state = state_with(tmp_path, [Message.user("短问题")])
    request, info = ContextAssembler("s", context_window=200000).build(state.messages)
    assert info is None and request.messages[0].get_text() == "短问题"


def test_compaction_truncates_request_but_preserves_complete_history(tmp_path):
    state = state_with(tmp_path, [Message.user("查"), Message.assistant([ToolUseBlock("bash", {}, "t1")]),
                                  Message.tool_results([ToolResultBlock("t1", "x" * 5000)]),
                                  Message.assistant([TextBlock("结果")]), Message.user("继续")])
    original = [message.to_dict() for message in state.messages]

    request, info = ContextAssembler("s", context_window=1000, keep_recent=2).build(state.messages)

    assert info[0] > info[1]
    assert "[结果已截断" in request.messages[2].content[0].content
    assert [message.to_dict() for message in state.messages] == original


def test_snip_changes_request_but_preserves_complete_history(tmp_path):
    messages = []
    for i in range(12):
        messages += [Message.user(f"问题{i}" + "啰" * 100), Message.assistant([TextBlock("嗦" * 100)])]
    state = state_with(tmp_path, messages)
    original = [message.to_dict() for message in state.messages]

    request, _ = ContextAssembler("s", context_window=500, keep_recent=4).build(state.messages)

    assert request.messages[0].get_text().startswith("问题0")
    assert "[历史已压缩]" in request.messages[1].get_text()
    assert [message.to_dict() for message in state.messages] == original


def test_build_rejects_context_without_modifying_complete_history(tmp_path):
    state = state_with(tmp_path, [Message.user("x" * 10_000)])
    original = [message.to_dict() for message in state.messages]

    with pytest.raises(context.ContextOverflowError):
        ContextAssembler("s", context_window=100, keep_recent=2).build(state.messages)

    assert [message.to_dict() for message in state.messages] == original


def test_resume_matches_in_memory_history_after_compaction(tmp_path):
    messages = []
    for i in range(12):
        messages += [Message.user(f"问题{i}" + "啰" * 100), Message.assistant([TextBlock("嗦" * 100)])]
    state = state_with(tmp_path, messages)

    ContextAssembler("s", context_window=500, keep_recent=4).build(state.messages)
    restored = SessionState.resume(state.session_id, transcript_dir=str(tmp_path))

    assert [message.to_dict() for message in restored.messages] == [
        message.to_dict() for message in state.messages
    ]
