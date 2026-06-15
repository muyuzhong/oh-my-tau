# Runtime Kernel — Structured `RunResult` Termination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the kernel's bare-string termination (`yield ev.AgentEnded(reason)`) with a structured `RunResult` (reason enum + `final_message_id` + `error` + `detail`), so upper layers stop string-matching, while keeping every existing consumer green.

**Architecture:** A new focused module `runtime/result.py` defines `StopReason(str, Enum)` and a frozen `RunResult`. `AgentEnded` evolves to carry a `RunResult` and exposes a back-compat `.reason` property. `engine.run` threads three locals (`reason`/`error`/`detail`) plus `last_assistant_id` to a **single terminal exit**. Because `StopReason` subclasses `str` and defines `__str__`, all `event.reason == "..."` comparisons and the CLI's f-string display keep working with zero edits to consumers. This is **gap C** of the 2026-06-15 kernel gap review — self-contained inside the kernel, zero cross-layer dependency.

**Tech Stack:** Python ≥3.10, `pytest` + `pytest-asyncio` (`asyncio_mode=auto`, so bare `async def test_*` needs no decorator). Authoritative design: `docs/superpowers/specs/2026-06-15-runtime-kernel-runresult-design.md` (ADR-015 / §14.7).

---

## Commit policy

The working tree is **clean** at plan start (`git status --short` empty) and `__pycache__`/`.pytest_cache` are gitignored, so this plan uses **one commit per task** (writing-plans default: frequent commits). Each commit step lists **explicit paths** in `git add` — never `git add -A` — so generated caches never enter a commit.

## Critical implementation hazard (read before Task 2)

Python 3 **unbinds the `as` target at the end of every `except` block** (implemented as an implicit `finally: del <name>`, and it fires even when you `break`/`return` out of the block). The current `engine.run` uses `except ... as error` in **three** places (`ContextOverflowError as error`, `ProviderError as error`, `Exception as error`). The new design introduces a function-scoped local **also named `error`** that must survive to the final `yield`. If the new local shares the name with any `except` target, it is silently deleted and the final `yield` raises `NameError` on the overflow/provider/fatal paths.

**Resolution (applied in Task 2):** rename every `except ... as <name>` target so none is `error` — use `overflow`, `provider_error`, `unexpected`. The result-local `error` is assigned *from* those (`error = str(overflow)`, etc.) and is never itself an `except` target, so it is never deleted. The inner-loop `fatal` local (provider error object) keeps its name; only the `except ProviderError as` target is renamed to `provider_error`.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `runtime/result.py` | `StopReason` enum + frozen `RunResult` — the kernel's termination contract, its own bounded unit | **create** |
| `tests/unit/test_result.py` | unit tests for the two new types (equality, display, defaults, frozen) | **create** |
| `runtime/events.py` | `AgentEnded` carries `RunResult`, keeps `.reason` property for back-compat | modify |
| `runtime/engine.py` | thread `reason`/`error`/`detail`/`last_assistant_id` to one structured exit; rename `except` targets | modify |
| `tests/integration/test_engine.py` | extend the 7 terminal-path tests with structured assertions; add the fatal-non-escape test | modify |
| `tests/integration/test_control.py` | extend the abort test with structured assertions; add the supervisor-terminate test | modify |
| `docs/...-runresult-design.md`, `docs/2026-06-14-initial-architecture-decisions.md`, `docs/independent-development-guide.md` | flip status to implemented; update the test baseline | modify (Task 4) |

`runtime/cli/repl.py` is intentionally **not** touched: `.reason` is preserved and `StopReason.__str__` renders the value, so `repl.py:85` displays `本轮结束（completed）` unchanged.

Import direction (no cycle): `result.py` (no `runtime` deps) ← `events.py` ← `engine.py`.

**Test-count ledger:** baseline 80 → +5 (Task 1) → 85 → +0 (Task 2 extends existing tests in place) → 85 → +2 (Task 3) → **87 passed**.

---

## Task 1: Create `runtime/result.py` + unit tests (ADR-015 types)

**Files:**
- Create: `tests/unit/test_result.py`
- Create: `runtime/result.py`

- [ ] **Step 1: Write the failing unit tests**

Create `tests/unit/test_result.py`:
```python
"""StopReason / RunResult 终止契约的单元测试（ADR-015）。"""
from dataclasses import FrozenInstanceError

import pytest

from runtime.result import RunResult, StopReason


def test_stop_reason_equals_legacy_string():
    # str 枚举：== 旧字符串仍成立，保护既有 13 处 `.reason == "..."` 断言与 CLI 展示。
    assert StopReason.COMPLETED == "completed"
    assert StopReason.PROVIDER_ERROR == "provider_error"
    assert StopReason.USER_ABORT == "user_abort"


def test_stop_reason_str_renders_value():
    # 锁定跨版本显示：str() 必须渲染为值，否则 CLI 会退化为 "StopReason.COMPLETED"。
    assert str(StopReason.COMPLETED) == "completed"


def test_stop_reason_fstring_renders_value():
    assert f"{StopReason.COMPLETED}" == "completed"


def test_run_result_defaults_to_none():
    result = RunResult(StopReason.COMPLETED)
    assert result.final_message_id is None
    assert result.error is None
    assert result.detail is None


def test_run_result_is_frozen():
    result = RunResult(StopReason.COMPLETED)
    with pytest.raises(FrozenInstanceError):
        result.reason = StopReason.FATAL
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd "D:/harness agent/Mono" && python -m pytest tests/unit/test_result.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'runtime.result'`.

- [ ] **Step 3: Create `runtime/result.py`**

```python
"""内核终止契约：结构化运行结果与终止原因枚举（ADR-015）。

内核每次运行以一个 RunResult 收尾，让上层无需解析裸字符串即可区分
「正常未完成 / 预算耗尽 / Provider 失败 / 监督策略终止 / 意外异常」。
StopReason 继承 str，使 == 旧字符串仍成立，保护既有断言与 CLI 展示；
显式 __str__ 让 str()/format()/f-string 跨 3.10–3.13 都渲染为值而非成员名。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class StopReason(str, Enum):
    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    MAX_TOKENS = "max_tokens"
    USER_ABORT = "user_abort"
    TOKEN_BUDGET = "token_budget"
    CONTEXT_OVERFLOW = "context_overflow"
    PROVIDER_ERROR = "provider_error"
    INCOMPLETE_STREAM = "incomplete_stream"
    SUPERVISOR_TERMINATE = "supervisor_terminate"
    FATAL = "fatal"

    def __str__(self) -> str:  # 让 str()/format()/f-string 渲染为值，CLI 展示不退化
        return self.value


@dataclass(frozen=True)
class RunResult:
    reason: StopReason
    final_message_id: Optional[str] = None
    error: Optional[str] = None    # 仅真正错误：provider_error / fatal / incomplete_stream / context_overflow
    detail: Optional[str] = None   # 非错误终止的具体说明：supervisor 的 constraint:max_tool_calls(52) 等
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd "D:/harness agent/Mono" && python -m pytest tests/unit/test_result.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Verify the full suite is still green (nothing else consumes the new module yet)**

Run: `cd "D:/harness agent/Mono" && python -m pytest -q`
Expected: `85 passed` (80 prior + 5 new).

- [ ] **Step 6: Commit**

```bash
cd "D:/harness agent/Mono" && git add runtime/result.py tests/unit/test_result.py && git commit -m "feat(runtime): StopReason enum and structured RunResult (ADR-015)"
```

---

## Task 2: Evolve `AgentEnded` + thread the engine to one structured exit

**Files:**
- Modify: `runtime/events.py`
- Modify: `runtime/engine.py`
- Modify: `tests/integration/test_engine.py` (extend terminal-path tests)
- Modify: `tests/integration/test_control.py` (extend abort test)

- [ ] **Step 1: Write the driving structured test (extend `test_text_only_completes`)**

In `tests/integration/test_engine.py`, add this import after the existing `from runtime.engine import AgentLoop` line:
```python
from runtime.result import StopReason
```
Then replace the existing `test_text_only_completes` (currently `tests/integration/test_engine.py:20-23`):
```python
async def test_text_only_completes(tmp_path):
    loop, _ = make_loop(tmp_path, [MockProvider.text_turn("你好")])
    events = await collect(loop.run("hi"))
    assert events[-1].reason == "completed" and len(loop.state.messages) == 2
```
with:
```python
async def test_text_only_completes(tmp_path):
    loop, _ = make_loop(tmp_path, [MockProvider.text_turn("你好")])
    events = await collect(loop.run("hi"))
    assert events[-1].reason == "completed" and len(loop.state.messages) == 2
    # 结构化结果：COMPLETED 无 error/detail，final_message_id 指向本轮最后一条 assistant 消息。
    result = events[-1].result
    assert result.reason is StopReason.COMPLETED
    assert result.error is None and result.detail is None
    assert result.final_message_id == loop.state.messages[-1].message_id
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd "D:/harness agent/Mono" && python -m pytest tests/integration/test_engine.py::test_text_only_completes -q`
Expected: FAIL — `AttributeError: 'AgentEnded' object has no attribute 'result'`.

- [ ] **Step 3: Rewrite `runtime/events.py` so `AgentEnded` carries `RunResult`**

Replace the import block and the final `AgentEnded` class. Change the top of the file from:
```python
"""AgentLoop 对消费者发布的细粒度运行时事件词汇表。"""
from dataclasses import dataclass
from typing import List
```
to:
```python
"""AgentLoop 对消费者发布的细粒度运行时事件词汇表。"""
from dataclasses import dataclass
from typing import List

from runtime.result import RunResult, StopReason
```
and replace the final class (currently `runtime/events.py:54-57`):
```python
@dataclass
class AgentEnded:
    """reason 是稳定的机器可读终止原因。"""
    reason: str
```
with:
```python
@dataclass
class AgentEnded:
    """以结构化 RunResult 收尾；.reason 保留给既有消费者（向后兼容）。"""
    result: RunResult

    @property
    def reason(self) -> StopReason:
        return self.result.reason
```

- [ ] **Step 4: Update `runtime/engine.py` — import, then thread `run` to one structured exit**

Add the import after the existing `from runtime.executor import ToolExecutor, ToolRegistry` line:
```python
from runtime.result import RunResult, StopReason
```

Then replace the entire `run` method (currently `runtime/engine.py:66-158`) with the version below. **Note the three renamed `except` targets** (`overflow`, `provider_error`, `unexpected`) — see the "Critical implementation hazard" section; do not collapse them back to `error`.

```python
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
```

Key changes vs. today, all faithful to design §3/§4:
- `reason` initialised to `StopReason.MAX_TURNS` (matches the old default `"max_turns"`).
- `error`/`detail`/`last_assistant_id` introduced; `last_assistant_id` set right after the assistant message is appended.
- every bare `reason = "..."` replaced by the enum member; the supervisor-terminate path now sets `reason = SUPERVISOR_TERMINATE` **and** preserves the dynamic `verdict.reason` in `detail` (previously the bare string *was* `verdict.reason`).
- the three `except ... as error` renamed to `overflow` / `provider_error` / `unexpected` (hazard fix).
- the `ErrorEvent`s already emitted before error terminations are left as-is (streaming view keeps its incremental error; `RunResult.error` makes the terminal result self-contained).
- one single terminal `yield ev.AgentEnded(RunResult(...))`.

- [ ] **Step 5: Run the driving test, then the full suite**

Run: `cd "D:/harness agent/Mono" && python -m pytest tests/integration/test_engine.py::test_text_only_completes -q`
Expected: PASS.

Run: `cd "D:/harness agent/Mono" && python -m pytest -q`
Expected: `85 passed` — the 13 existing `.reason == "..."` assertions still pass (now via the `.reason` property returning a `StopReason` that `==` the old string), plus the new structured assertions in `test_text_only_completes`.

- [ ] **Step 6: Extend the remaining six terminal-path tests in `test_engine.py`**

These tests already assert the bare `.reason`; keep that line and add the structured-field assertions from the §4 mapping. Replace each function with the version shown.

Replace `test_non_retryable_error_ends_run` (`tests/integration/test_engine.py:39-41`):
```python
async def test_non_retryable_error_ends_run(tmp_path):
    loop, _ = make_loop(tmp_path, [ProviderAuthError("bad")])
    events = await collect(loop.run("hi"))
    assert events[-1].reason == "provider_error"
    assert events[-1].result.reason is StopReason.PROVIDER_ERROR
    assert events[-1].result.error  # 非空：携带 provider 错误信息
```

Replace `test_token_budget_stops_loop` (`tests/integration/test_engine.py:56-58`):
```python
async def test_token_budget_stops_loop(tmp_path):
    loop, _ = make_loop(tmp_path, [MockProvider.tool_turn("echo", {"text": "a"})], [EchoTool()], ledger=TokenLedger(max_api_calls=1))
    events = await collect(loop.run("hi"))
    assert events[-1].reason == "token_budget"
    assert events[-1].result.reason is StopReason.TOKEN_BUDGET
    assert events[-1].result.error is None and events[-1].result.detail is None
```

Replace `test_max_turns` (`tests/integration/test_engine.py:61-63`):
```python
async def test_max_turns(tmp_path):
    loop, _ = make_loop(tmp_path, [MockProvider.tool_turn("echo", {"text": "a"})] * 2, [EchoTool()], max_turns=2)
    events = await collect(loop.run("hi"))
    assert events[-1].reason == "max_turns"
    assert events[-1].result.reason is StopReason.MAX_TURNS
    assert events[-1].result.error is None and events[-1].result.detail is None
```

Replace `test_max_tokens_is_not_reported_as_completed` (`tests/integration/test_engine.py:66-69`):
```python
async def test_max_tokens_is_not_reported_as_completed(tmp_path):
    truncated = [MessageStart("mock-model"), TextDelta("未完成"), MessageEnd("max_tokens", Usage(1, 5))]
    loop, _ = make_loop(tmp_path, [truncated])
    events = await collect(loop.run("hi"))
    assert events[-1].reason == "max_tokens"
    assert events[-1].result.reason is StopReason.MAX_TOKENS
    assert events[-1].result.error is None
```

Replace `test_incomplete_provider_stream_is_not_reported_as_completed` (`tests/integration/test_engine.py:72-77`):
```python
async def test_incomplete_provider_stream_is_not_reported_as_completed(tmp_path):
    incomplete = [MessageStart("mock-model"), TextDelta("半截")]
    loop, _ = make_loop(tmp_path, [incomplete])
    events = await collect(loop.run("hi"))
    assert events[-1].reason == "incomplete_stream"
    assert len(loop.state.messages) == 1
    assert events[-1].result.reason is StopReason.INCOMPLETE_STREAM
    assert events[-1].result.error == "Provider 流未正常结束"
    assert events[-1].result.final_message_id is None  # 未产出 assistant 消息
```

Replace `test_context_overflow_ends_with_explicit_reason` (`tests/integration/test_engine.py:80-86`):
```python
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
```

(`test_tool_roundtrip`, `test_retryable_error_then_success`, and `test_broken_tool_json_fed_back_as_error` all end in `COMPLETED`, already covered structurally by `test_text_only_completes`; leave them as-is so they keep proving the bare-`.reason` back-compat path.)

- [ ] **Step 7: Extend the abort test in `test_control.py`**

In `tests/integration/test_control.py`, add this import after `from runtime.engine import AgentLoop`:
```python
from runtime.result import StopReason
```
Then replace `test_abort_mid_stream_discards_partial` (`tests/integration/test_control.py:19-25`):
```python
async def test_abort_mid_stream_discards_partial(tmp_path):
    loop, control = make_loop(tmp_path, [MockProvider.text_turn("很长")])
    events = []
    async for event in loop.run("hi"):
        events.append(event)
        if isinstance(event, ev.TextDeltaEvent): control.submit(Abort())
    assert events[-1].reason == "user_abort" and len(loop.state.messages) == 1
    # 在产出任何 assistant 消息前中断：final_message_id 为 None。
    assert events[-1].result.reason is StopReason.USER_ABORT
    assert events[-1].result.final_message_id is None
```

- [ ] **Step 8: Run the full suite**

Run: `cd "D:/harness agent/Mono" && python -m pytest -q`
Expected: `85 passed` (no new test functions added in this task; 8 existing tests now also assert the structured result).

- [ ] **Step 9: Commit**

```bash
cd "D:/harness agent/Mono" && git add runtime/events.py runtime/engine.py tests/integration/test_engine.py tests/integration/test_control.py && git commit -m "feat(runtime): kernel terminates with structured RunResult (ADR-015)"
```

---

## Task 3: Lock the two safety invariants — supervisor detail + fatal non-escape

Both behaviors are produced by the Task 2 engine; these tests are new **contract locks** (they need fixtures no existing test has). Each is genuinely red without Task 2 (no `.result` attribute) and asserts a §14.10 invariant.

**Files:**
- Modify: `tests/integration/test_engine.py` (add supervisor + fatal tests)

- [ ] **Step 1: Add the supervisor-terminate import**

In `tests/integration/test_engine.py`, add after the existing `from runtime.executor import ToolRegistry` line:
```python
from runtime.supervisor import ConstraintValidator, Supervisor
```
(`StopReason` was already imported in Task 2 Step 1.)

- [ ] **Step 2: Append the supervisor-terminate test**

Append to `tests/integration/test_engine.py`:
```python
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
```

- [ ] **Step 3: Append the fatal-non-escape test**

`MockProvider` raises any script entry that is an `Exception`. A plain `RuntimeError` is **not** a `ProviderError`, so it is not caught by the retry `except`; it must fall through to the engine's catch-all and become `FATAL` without escaping the generator. Append to `tests/integration/test_engine.py`:
```python
async def test_fatal_exception_does_not_escape_generator(tmp_path):
    # 非 ProviderError 异常在流中抛出：必须被兜底为 FATAL，生成器正常收尾、绝不外抛。
    loop, _ = make_loop(tmp_path, [RuntimeError("boom")])
    events = await collect(loop.run("hi"))  # 不抛异常本身就是被测不变量
    assert isinstance(events[-1], ev.AgentEnded)
    result = events[-1].result
    assert result.reason is StopReason.FATAL
    assert result.error and "boom" in result.error
    assert result.final_message_id is None
```

- [ ] **Step 4: Run the two new tests**

Run: `cd "D:/harness agent/Mono" && python -m pytest tests/integration/test_engine.py::test_supervisor_terminate_carries_detail tests/integration/test_engine.py::test_fatal_exception_does_not_escape_generator -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full suite**

Run: `cd "D:/harness agent/Mono" && python -m pytest -q`
Expected: `87 passed`.

- [ ] **Step 6: Commit**

```bash
cd "D:/harness agent/Mono" && git add tests/integration/test_engine.py && git commit -m "test(runtime): lock supervisor-detail and fatal-non-escape invariants (ADR-015)"
```

---

## Task 4: Full verification gate + docs sync + report

**Files:**
- Modify: `docs/superpowers/specs/2026-06-15-runtime-kernel-runresult-design.md`
- Modify: `docs/2026-06-14-initial-architecture-decisions.md`
- Modify: `docs/independent-development-guide.md`

- [ ] **Step 1: Run the complete verification gate (dev-guide §7)**

Run:
```bash
cd "D:/harness agent/Mono" && python -m pytest -q && python -m compileall -q core runtime providers tools cli tests && git diff --check
```
Expected: pytest `87 passed`; compileall silent (success); `git diff --check` silent (no whitespace/conflict markers).

- [ ] **Step 2: Confirm the CLI display did not regress (no code change, just proof)**

Run:
```bash
cd "D:/harness agent/Mono" && python -c "from runtime.result import StopReason; print(f'本轮结束（{StopReason.COMPLETED}）')"
```
Expected output: `本轮结束（completed）` — proves `repl.py:85` renders the value, not `StopReason.COMPLETED`, so the CLI needs no edit.

- [ ] **Step 3: Flip the spec status to implemented**

In `docs/superpowers/specs/2026-06-15-runtime-kernel-runresult-design.md`, change the status line (line 4) from:
```markdown
- Status: design approved, ready for implementation plan
```
to:
```markdown
- Status: implemented (2026-06-16); see docs/superpowers/plans/2026-06-16-runtime-kernel-runresult.md
```

- [ ] **Step 4: Mark ADR-015 done in the architecture doc**

In `docs/2026-06-14-initial-architecture-decisions.md`, under `#### ADR-015`, append one line after its `**后果**` paragraph (after line 857):
```markdown

**实现状态（2026-06-16）**：已实现。`runtime/result.py` 定义 `StopReason` 与 `RunResult`，`AgentEnded` 携带 `RunResult` 并保留 `.reason` 兼容属性；`engine.run` 收敛为单一结构化出口。gap C 关闭；A/B/D/E 仍按 §14.8 推进。
```

- [ ] **Step 5: Update the test baseline in the dev guide**

In `docs/independent-development-guide.md`, the "当前验证基线" block (lines 46-49) reads `80 passed`. Update it to:
```text
python -m pytest -q
87 passed
```
Also append `- 结构化运行终止 RunResult（ADR-015）` to the "现有能力包括" list (after line 42, the `ExecutionEnv` bullet).

- [ ] **Step 6: Final gate after doc edits + commit**

Run: `cd "D:/harness agent/Mono" && python -m pytest -q && git diff --check`
Expected: `87 passed`; `git diff --check` silent.

```bash
cd "D:/harness agent/Mono" && git add docs/superpowers/specs/2026-06-15-runtime-kernel-runresult-design.md docs/2026-06-14-initial-architecture-decisions.md docs/independent-development-guide.md && git commit -m "docs(spec): mark ADR-015 RunResult implemented; baseline 80 -> 87"
```

- [ ] **Step 7: Report**

Report:
- **What changed:** new `runtime/result.py` (`StopReason`/`RunResult`); `AgentEnded` carries `RunResult` + `.reason` property; `engine.run` threads `reason`/`error`/`detail`/`last_assistant_id` to one structured exit (with the `except`-target rename); structured assertions added across 8 terminal-path tests + 2 new invariant tests; docs synced.
- **What was deliberately NOT changed:** `cli/repl.py` (display preserved); gaps A/B/D/E (history & budget ownership, `run_one_turn`/`TurnSnapshot`, `RuntimePolicy`) — they require the HarnessSession layer as consumer or are separate kernel slices, per §14.8 and the spec's non-goals.
- **How verified:** `pytest -q` 80 → 87 passed; `compileall` clean; `git diff --check` clean; CLI display proof in Step 2; the §14.10 "异常绝不逃逸" invariant locked by `test_fatal_exception_does_not_escape_generator`.

---

## Self-Review

**Spec coverage** (against `2026-06-15-...-runresult-design.md`):
- §1 `runtime/result.py` types → Task 1. ✓
- §2 `AgentEnded` evolution + `.reason` property → Task 2 Step 3. ✓
- §3 engine threading (single exit, 3 locals + `last_assistant_id`) → Task 2 Step 4. ✓
- §4 termination→field mapping → covered by the per-path assertions: COMPLETED (T2 S1), PROVIDER_ERROR/TOKEN_BUDGET/MAX_TURNS/MAX_TOKENS/INCOMPLETE_STREAM/CONTEXT_OVERFLOW (T2 S6), USER_ABORT (T2 S7), SUPERVISOR_TERMINATE + FATAL (T3). All 10 reasons asserted. ✓
- §5 tests, incl. unit `test_result.py`, supervisor-terminate, fatal-non-escape, cross-version display → Task 1 + Task 3. ✓
- §6 verification gate → Task 4 Step 1. ✓
- §7 non-goals (no `run_one_turn`/`RuntimePolicy`/history move; no CLI refactor) → respected; nothing in this plan touches them. ✓

**Placeholder scan:** none — every code/test step shows complete file content or a full function replacement; every command has an expected result.

**Type consistency:** `StopReason` members and `RunResult(reason, final_message_id, error, detail)` field order are identical across `result.py`, the engine's final `yield`, and all assertions. `AgentEnded(result=...)` is the only constructor call (engine); `.reason` property feeds all 13 legacy assertions and `repl.py:85`. `Message.message_id` (verified at `blocks.py:119`) is the source of `final_message_id`.

**Discrepancy note (corrected from spec):** the design says "9 existing `.reason ==` assertions"; the actual current count is **13** (`grep -rn "\.reason ==" tests/`). The mechanism (str-enum equality) covers all of them regardless of count, so the plan targets 13, not 9. The design's `final_message_id` semantics, `error`/`detail` split, and `__str__` display fix are adopted verbatim.

**Count check:** new tests = 5 (Task 1) + 0 (Task 2 extends in place) + 2 (Task 3) = 7 → 80 + 7 = **87 passed**.
