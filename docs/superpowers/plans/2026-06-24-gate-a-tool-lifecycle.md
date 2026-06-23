# Gate A — 工具生命周期（spec③）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给内核工具执行补上 spec③ 的生命周期机制——一个不丢信息的执行结果载体（A0）、`after_tool_call` 钩子、并行工具的完成序 `tool_execution_end`、以及真正产出的 `tool_execution_update` 进度事件——全部由 Gate C 工作区工具的真实需求拉动。

**Architecture:** 在 `agent` 层引入 `ToolExecutionOutcome`（包住 wire `ToolResultMessage` + agent 层执行元数据 `details/terminate`，元数据不泄进 wire）。把批量执行器重构成流式 `stream_tool_executions` 异步生成器（用 `asyncio.Queue` 收集完成事件），loop 据此按完成序发 end、按到达序发 update，并保持 transcript 源序（G6）。`terminate` 字段仅预留，不接 loop 收尾（无终止型工具拉动）。

**Tech Stack:** Python 3.11+ `asyncio`；`dataclasses`；`pydantic` v2（工具参数已有）；`pytest` + `pytest-asyncio`；`import-linter`。

## Global Constraints

以下为 spec 钉死的项目级约束，每个 Task 隐含包含（值照抄自 AGENTS.md / spec）：

- **依赖方向**：`agent → ai → utils`，`lint-imports` 必须 KEPT。本计划只改 `agent` 层，不得让 `ai`/`utils` 反向依赖。
- **机制 vs 策略（四关）**：本计划只加机制（结果载体、钩子点、事件产出时机、进度通道）。具体脱敏/截断/审批/终止**规则**属 harness，不进内核。`agent`/`ai` 包不得出现具体名字。
- **工具异常不外逃**：工具抛错仍编码为 `ToolResultMessage(is_error=True)`，永不穿透 loop。
- **单一终止**：每次 run 仍以恰好一个 `AgentEnd` + `RunResult` 收尾。
- **两级 StopReason 不混**：wire 级 `nanoagent.ai.StopReason`；run 级 `nanoagent.agent.StopReason`。
- **事件契约 G1–G7 不破**（`tests/agent/test_event_contract.py`）：尤其 **G5**（每个 `tool_call_id`：start 先于 end，各一次；被拒/未知工具也照发 start+end）与 **G6**（工具结果 transcript 消息在本批所有 `tool_execution_end` 之后、按源序）。**完成序 end 只改 end 的全局顺序，G5 不锁全局序，故契约不破。**
- **测试只用 mock**：不依赖真 provider / network / 文件系统。
- **TDD**：每个机制先写失败测试，再最小实现。

---

## File Structure

```
src/nanoagent/agent/
  tools.py     # 改：AgentToolResult+terminate；新增 ToolExecutionOutcome；_run_one 返回 outcome+after_tool_call+emit；
               #     新增 stream_tool_executions（流式/完成序/进度）；execute_tool_calls 改为流式收集器
  loop.py      # 改：AgentLoopConfig 增 after_tool_call；工具执行段改为消费 stream_tool_executions（完成序 end + update）
  __init__.py  # 改：导出 ToolExecutionOutcome、stream_tool_executions
  events.py    # 仅核对 docstring（已描述 update/完成序 end，无需改类型）

tests/agent/
  test_tools.py          # 改：execute_tool_calls 返回 outcome（.message 取内容）；新增 details/terminate、after_tool_call 单测
  test_loop_tools.py     # 新增：完成序 end、进度 update 的 loop 级测试
  test_event_contract.py # 新增：并行+进度下 G1–G7 仍成立的契约测试
```

**复审里程碑**：Task 1 完成 = 结果载体就位、现有行为不变；Task 2 = 钩子；Task 3 = 完成序；Task 4 = 进度；Task 5 = 契约锁定 + 全量回归。

---

## Task 1: A0 — `ToolExecutionOutcome` 结果载体 + `terminate` 预留

把"工具 → loop"边界从只能传 `content+is_error` 升级为可携带 `details/terminate` 的 outcome。本任务**不改变任何对外事件/transcript 顺序**，只换执行器的返回类型。

**Files:**
- Modify: `src/nanoagent/agent/tools.py`
- Modify: `src/nanoagent/agent/loop.py`（工具执行段用 `.message`）
- Modify: `src/nanoagent/agent/__init__.py`（导出）
- Test: `tests/agent/test_tools.py`

**Interfaces:**
- Produces:
  - `AgentToolResult(content, is_error=False, details=None, terminate=False)` —— 新增 `terminate`。
  - `ToolExecutionOutcome(tool_call_id, tool_name, message: ToolResultMessage, details=None, terminate=False)`，只读属性 `is_error -> bool`（委托 `message.is_error`）。
  - `execute_tool_calls(...) -> list[ToolExecutionOutcome]`（源序，签名其余不变）。
- Consumes（loop）：`outcome.message`（transcript/history/TurnEnd）、`outcome.is_error`（end 事件）。

- [ ] **Step 1: 写失败测试（details/terminate 必须存活到 outcome）**

`tests/agent/test_tools.py` 追加：

```python
class _DetailTool(AgentTool):
    name = "det"
    description = "carries details"
    parameters = EchoArgs
    label = "Det"

    async def execute(self, tool_call_id, params, signal=None):
        return AgentToolResult(
            content=[TextContent(text="ok")], details={"k": 1}, terminate=True
        )


@pytest.mark.asyncio
async def test_outcome_carries_details_and_terminate():
    calls = [ToolCall(id="t1", name="det", arguments={"text": "x"})]
    outcomes = await execute_tool_calls(calls, [_DetailTool()])
    assert outcomes[0].details == {"k": 1}
    assert outcomes[0].terminate is True
    assert outcomes[0].message.content[0].text == "ok"
    assert outcomes[0].is_error is False
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/agent/test_tools.py::test_outcome_carries_details_and_terminate -v`
Expected: FAIL（`AgentToolResult` 无 `terminate`，且 `execute_tool_calls` 返回的是 `ToolResultMessage`，无 `.details`/`.message`）。

- [ ] **Step 3: 改 `tools.py` —— 加 `terminate`、`ToolExecutionOutcome`、改 `_run_one`/`execute_tool_calls`**

把 `AgentToolResult` 改为：

```python
@dataclass
class AgentToolResult:
    content: list[TextContent | ImageContent] = field(default_factory=list)
    is_error: bool = False
    details: Any = None
    terminate: bool = False
```

在 `AgentToolResult` 之后新增：

```python
@dataclass
class ToolExecutionOutcome:
    """Executor -> loop carrier.

    Wraps the wire ``ToolResultMessage`` (goes into the transcript) plus
    agent-layer execution metadata (``details``/``terminate``) that must NOT
    leak into the wire message or convert_to_llm.
    """

    tool_call_id: str
    tool_name: str
    message: ToolResultMessage
    details: Any = None
    terminate: bool = False

    @property
    def is_error(self) -> bool:
        return self.message.is_error
```

把 `_error_result` 替换为两个 helper：

```python
def _error_message(call: ToolCall, text: str) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call.id,
        tool_name=call.name,
        content=[TextContent(text=text)],
        is_error=True,
    )


def _error_outcome(call: ToolCall, text: str) -> ToolExecutionOutcome:
    return ToolExecutionOutcome(
        tool_call_id=call.id, tool_name=call.name, message=_error_message(call, text)
    )
```

把 `_run_one` 改为返回 outcome：

```python
async def _run_one(
    call: ToolCall, tool: AgentTool, signal: Any, before_tool_call
) -> ToolExecutionOutcome:
    try:
        params = tool.parameters.model_validate(call.arguments)
    except ValidationError as e:
        return _error_outcome(call, f"Invalid arguments: {e}")
    if before_tool_call is not None:
        decision = await before_tool_call(call, params)
        if decision is not None and decision.get("block"):
            return _error_outcome(call, decision.get("reason") or "Tool blocked")
    try:
        result = await tool.execute(call.id, params, signal)
    except Exception as e:  # tool exceptions never propagate: encode as is_error
        return _error_outcome(call, f"Tool failed: {e}")
    message = ToolResultMessage(
        tool_call_id=call.id,
        tool_name=call.name,
        content=result.content,
        is_error=result.is_error,
    )
    return ToolExecutionOutcome(
        tool_call_id=call.id,
        tool_name=call.name,
        message=message,
        details=result.details,
        terminate=result.terminate,
    )
```

把 `execute_tool_calls` 的类型与累加器改为 outcome（逻辑结构不变）：

```python
async def execute_tool_calls(
    tool_calls: list[ToolCall],
    tools: list[AgentTool],
    *,
    signal: Any = None,
    before_tool_call: Callable[[ToolCall, BaseModel], Awaitable[dict | None]] | None = None,
) -> list[ToolExecutionOutcome]:
    by_name = {t.name: t for t in tools}
    results: list[ToolExecutionOutcome | None] = [None] * len(tool_calls)
    shared: list[asyncio.Task] = []
    for i, call in enumerate(tool_calls):
        tool = by_name.get(call.name)
        if tool is None:
            results[i] = _error_outcome(call, f"Unknown tool: {call.name}")
            continue
        if tool.concurrency == "exclusive":
            if shared:
                for idx, r in await asyncio.gather(*shared):
                    results[idx] = r
                shared = []
            results[i] = await _run_one(call, tool, signal, before_tool_call)
        else:

            async def _wrap(idx=i, c=call, t=tool):
                return idx, await _run_one(c, t, signal, before_tool_call)

            shared.append(asyncio.create_task(_wrap()))
    for idx, r in await asyncio.gather(*shared):
        results[idx] = r
    return [r for r in results if r is not None]
```

- [ ] **Step 4: 改 `loop.py` 工具执行段 —— 用 `.message`**

定位 `loop.py` 工具执行段（当前约 `:245-278`）。把 `denied_results` 改成 outcome，并在 end/transcript/TurnEnd 处统一用 `.message`：

```python
        approved: list = []
        denied_results: dict[str, ToolExecutionOutcome] = {}
        for c in tool_calls:
            ok = True
            if config.control is not None:
                ok = await config.control.request_approval(c, "exec")
            if ok:
                approved.append(c)
            else:
                denied_results[c.id] = ToolExecutionOutcome(
                    tool_call_id=c.id,
                    tool_name=c.name,
                    message=ToolResultMessage(
                        tool_call_id=c.id,
                        tool_name=c.name,
                        content=[TextContent(text="Tool approval denied")],
                        is_error=True,
                    ),
                )

        executed = await execute_tool_calls(
            approved, tools, signal=signal, before_tool_call=config.before_tool_call
        )
        executed_by_id = {r.tool_call_id: r for r in executed}
        outcomes = [denied_results.get(c.id) or executed_by_id[c.id] for c in tool_calls]
        for c, o in zip(tool_calls, outcomes):
            yield ToolExecutionEnd(
                tool_call_id=c.id, tool_name=c.name, result=o.message, is_error=o.is_error
            )
        for o in outcomes:
            yield MessageStart(message=o.message)
            history.append(o.message)
            produced.append(o.message)
            yield MessageEnd(message=o.message)
        yield TurnEnd(message=assistant, tool_results=[o.message for o in outcomes])
```

在 `loop.py` 顶部 import 段补 `ToolExecutionOutcome`：把 `from nanoagent.agent.tools import AgentTool, execute_tool_calls` 改为 `from nanoagent.agent.tools import AgentTool, ToolExecutionOutcome, execute_tool_calls`。

> 注：`ToolExecutionEnd.result` 仍是 `o.message`（wire `ToolResultMessage`），`TurnEnd.tool_results` 仍是 `list[ToolResultMessage]`——**对外事件形状完全不变**，故 `test_event_contract.py` 不破。

- [ ] **Step 5: 改 `__init__.py` 导出**

`src/nanoagent/agent/__init__.py` 在 tools 导出处追加 `ToolExecutionOutcome`：

```python
from nanoagent.agent.tools import (
    AgentTool,
    AgentToolResult,
    ToolExecutionOutcome,
    execute_tool_calls,
)
```

并把 `ToolExecutionOutcome` 加进 `__all__`。

- [ ] **Step 6: 改既有 `test_tools.py` 断言（返回值现为 outcome）**

`tests/agent/test_tools.py::test_executes_and_returns_result` 第 26 行改为：

```python
    assert results[0].message.content[0].text == "hi" and results[0].is_error is False
```

（`test_unknown_tool_is_error_not_raise`、`test_validation_error_is_error_not_raise` 用 `results[0].is_error`，经 outcome 的 `is_error` 属性仍成立，无需改。）

- [ ] **Step 7: 跑测试验证通过**

Run: `pytest tests/agent/test_tools.py tests/agent/test_loop_tools.py tests/agent/test_event_contract.py -v`
Expected: PASS（新测试过；现有事件/transcript 顺序不变）。

- [ ] **Step 8: 全量回归 + 契约 + 提交**

Run: `pytest -q && lint-imports`
Expected: all pass；`Contracts: 1 kept, 0 broken.`

```bash
git add src/nanoagent/agent/tools.py src/nanoagent/agent/loop.py src/nanoagent/agent/__init__.py tests/agent/test_tools.py
git commit -m "feat(agent): add ToolExecutionOutcome carrier (details/terminate survive execute->loop)"
```

---

## Task 2: A3 — `after_tool_call` 钩子（与 `before_tool_call` 对称）

让 harness 在结果回填前观察/改写 outcome（脱敏/截断/审计）。钩子放在 `_run_one` 内部，与 `before_tool_call` 对称；作用于实际 `execute()` 的 outcome（含工具异常 outcome），校验/审批前的早退 outcome 不经过它。

**Files:**
- Modify: `src/nanoagent/agent/tools.py`（`_run_one`、`execute_tool_calls` 加 `after_tool_call`）
- Modify: `src/nanoagent/agent/loop.py`（`AgentLoopConfig.after_tool_call`，透传）
- Test: `tests/agent/test_loop_tools.py`

**Interfaces:**
- Produces:
  - `AfterToolCall = Callable[[ToolCall, ToolExecutionOutcome], Awaitable[ToolExecutionOutcome | None]]`。
  - `execute_tool_calls(..., after_tool_call=None)`；`AgentLoopConfig.after_tool_call`。
  - 语义：返回新 outcome 则替换；返回 `None` 则保留原 outcome。

- [ ] **Step 1: 写失败测试（钩子改写结果传到 transcript）**

`tests/agent/test_loop_tools.py` 追加（顶部已 import `TextContent`、`AgentToolResult` 等）：

```python
@pytest.mark.asyncio
async def test_after_tool_call_revises_result_before_backfill():
    clear_providers()
    register_mock()
    mock = create_mock_model(
        responses=[
            {"content": [{"type": "toolCall", "name": "echo", "arguments": {"text": "secret"}}]},
            {"content": ["done"]},
        ]
    )

    async def redact(call, outcome):
        outcome.message.content = [TextContent(text="[redacted]")]
        return outcome

    cfg = AgentLoopConfig(model=mock, after_tool_call=redact)
    events = [
        e
        async for e in agent_loop(
            prompts=[UserMessage(content="go")],
            system_prompt=[],
            messages=[],
            tools=[EchoTool()],
            config=cfg,
        )
    ]
    end = events[-1]
    tool_results = [m for m in end.messages if m.role == "toolResult"]
    assert tool_results[0].content[0].text == "[redacted]"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/agent/test_loop_tools.py::test_after_tool_call_revises_result_before_backfill -v`
Expected: FAIL（`AgentLoopConfig` 无 `after_tool_call`）。

- [ ] **Step 3: 在 `_run_one` 接钩子**

把 `_run_one` 的 execute 段改为构造 outcome 后过钩子：

```python
async def _run_one(
    call: ToolCall, tool: AgentTool, signal: Any, before_tool_call, after_tool_call=None
) -> ToolExecutionOutcome:
    try:
        params = tool.parameters.model_validate(call.arguments)
    except ValidationError as e:
        return _error_outcome(call, f"Invalid arguments: {e}")
    if before_tool_call is not None:
        decision = await before_tool_call(call, params)
        if decision is not None and decision.get("block"):
            return _error_outcome(call, decision.get("reason") or "Tool blocked")
    try:
        result = await tool.execute(call.id, params, signal)
    except Exception as e:  # tool exceptions never propagate: encode as is_error
        outcome = _error_outcome(call, f"Tool failed: {e}")
    else:
        outcome = ToolExecutionOutcome(
            tool_call_id=call.id,
            tool_name=call.name,
            message=ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=result.content,
                is_error=result.is_error,
            ),
            details=result.details,
            terminate=result.terminate,
        )
    if after_tool_call is not None:
        revised = await after_tool_call(call, outcome)
        if revised is not None:
            outcome = revised
    return outcome
```

`execute_tool_calls` 签名加 `after_tool_call=None`，并在两处调用 `_run_one` 时透传（`_run_one(call, tool, signal, before_tool_call, after_tool_call)` 与 `_wrap` 内同样补传）：

```python
async def execute_tool_calls(
    tool_calls: list[ToolCall],
    tools: list[AgentTool],
    *,
    signal: Any = None,
    before_tool_call: Callable[[ToolCall, BaseModel], Awaitable[dict | None]] | None = None,
    after_tool_call=None,
) -> list[ToolExecutionOutcome]:
    by_name = {t.name: t for t in tools}
    results: list[ToolExecutionOutcome | None] = [None] * len(tool_calls)
    shared: list[asyncio.Task] = []
    for i, call in enumerate(tool_calls):
        tool = by_name.get(call.name)
        if tool is None:
            results[i] = _error_outcome(call, f"Unknown tool: {call.name}")
            continue
        if tool.concurrency == "exclusive":
            if shared:
                for idx, r in await asyncio.gather(*shared):
                    results[idx] = r
                shared = []
            results[i] = await _run_one(call, tool, signal, before_tool_call, after_tool_call)
        else:

            async def _wrap(idx=i, c=call, t=tool):
                return idx, await _run_one(c, t, signal, before_tool_call, after_tool_call)

            shared.append(asyncio.create_task(_wrap()))
    for idx, r in await asyncio.gather(*shared):
        results[idx] = r
    return [r for r in results if r is not None]
```

- [ ] **Step 4: 在 `AgentLoopConfig` 加字段并透传**

`loop.py` `AgentLoopConfig` 增 `after_tool_call`（在 `before_tool_call` 之后）：

```python
    before_tool_call: Callable[..., Awaitable[dict | None]] | None = None
    after_tool_call: Callable[..., Awaitable[Any]] | None = None
```

Task 1 中 `executed = await execute_tool_calls(approved, tools, signal=signal, before_tool_call=config.before_tool_call)` 改为补传：

```python
        executed = await execute_tool_calls(
            approved,
            tools,
            signal=signal,
            before_tool_call=config.before_tool_call,
            after_tool_call=config.after_tool_call,
        )
```

- [ ] **Step 5: 跑测试验证通过**

Run: `pytest tests/agent/test_loop_tools.py::test_after_tool_call_revises_result_before_backfill -v`
Expected: PASS

- [ ] **Step 6: 全量回归 + 契约 + 提交**

Run: `pytest -q && lint-imports`
Expected: all pass；contract kept。

```bash
git add src/nanoagent/agent/tools.py src/nanoagent/agent/loop.py tests/agent/test_loop_tools.py
git commit -m "feat(agent): add after_tool_call hook symmetric with before_tool_call"
```

---

## Task 3: A1 — 流式执行器 + 完成序 `tool_execution_end`

把批量执行器重构为流式 `stream_tool_executions` 异步生成器（`asyncio.Queue` 收集），loop 据此按**完成序**发 end（消费者靠 `tool_call_id` 关联，G5 允许）。transcript 仍按源序（G6）。被拒工具的 end 在流之前先发。

**Files:**
- Modify: `src/nanoagent/agent/tools.py`（新增 `stream_tool_executions`；`execute_tool_calls` 改为流式收集器）
- Modify: `src/nanoagent/agent/loop.py`（工具执行段消费 stream）
- Modify: `src/nanoagent/agent/__init__.py`（导出 `stream_tool_executions`）
- Test: `tests/agent/test_loop_tools.py`

**Interfaces:**
- Produces: `stream_tool_executions(tool_calls, tools, *, signal=None, before_tool_call=None, after_tool_call=None) -> AsyncIterator[tuple[str, Any]]`，yield `("end", ToolExecutionOutcome)`（Task 4 再加 `("update", (id, name, partial))`）。
- 保持 shared 并行 / exclusive 串行语义；完成序 end。
- `execute_tool_calls` 仍返回**源序** `list[ToolExecutionOutcome]`（直接单测与 G6 需要源序）。

- [ ] **Step 1: 写失败测试（完成序 end，源序 transcript）**

`tests/agent/test_loop_tools.py` 追加：

```python
import asyncio


class _NoArgs(BaseModel):
    pass


class _SlowTool(AgentTool):
    description = "sleeps then echoes its name"
    parameters = _NoArgs

    def __init__(self, name, delay):
        self.name = name
        self.label = name
        self._delay = delay

    async def execute(self, tool_call_id, params, signal=None):
        await asyncio.sleep(self._delay)
        return AgentToolResult(content=[TextContent(text=self.name)])


@pytest.mark.asyncio
async def test_parallel_tool_ends_in_completion_order_transcript_in_source_order():
    clear_providers()
    register_mock()
    # assistant calls slow "a" (id mock-tc-1) then fast "b" (id mock-tc-2), both shared
    mock = create_mock_model(
        responses=[
            {
                "content": [
                    {"type": "toolCall", "name": "a", "arguments": {}},
                    {"type": "toolCall", "name": "b", "arguments": {}},
                ]
            },
            {"content": ["done"]},
        ]
    )
    cfg = AgentLoopConfig(model=mock)
    events = [
        e
        async for e in agent_loop(
            prompts=[UserMessage(content="go")],
            system_prompt=[],
            messages=[],
            tools=[_SlowTool("a", 0.05), _SlowTool("b", 0.0)],
            config=cfg,
        )
    ]
    end_ids = [e.tool_call_id for e in events if e.type == "tool_execution_end"]
    assert end_ids == ["mock-tc-2", "mock-tc-1"], "ends follow completion order (b before a)"

    tool_result_texts = [
        m.content[0].text for m in events[-1].messages if m.role == "toolResult"
    ]
    assert tool_result_texts == ["a", "b"], "transcript stays in source order"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/agent/test_loop_tools.py::test_parallel_tool_ends_in_completion_order_transcript_in_source_order -v`
Expected: FAIL（当前批量实现按源序发 end → `end_ids == ["mock-tc-1","mock-tc-2"]`）。

- [ ] **Step 3: 新增 `stream_tool_executions`，`execute_tool_calls` 改为收集器**

`tools.py` 在 `_run_one` 之后新增流式生成器：

```python
async def stream_tool_executions(
    tool_calls: list[ToolCall],
    tools: list[AgentTool],
    *,
    signal: Any = None,
    before_tool_call=None,
    after_tool_call=None,
):
    """Run tool calls, yielding ('end', outcome) in COMPLETION order.

    Preserves shared (parallel) / exclusive (serial, drains shared first)
    scheduling. Consumers correlate by tool_call_id, never by position.
    """
    by_name = {t.name: t for t in tools}
    queue: asyncio.Queue = asyncio.Queue()
    total = len(tool_calls)

    async def _run_and_enqueue(call: ToolCall, tool: AgentTool) -> None:
        outcome = await _run_one(call, tool, signal, before_tool_call, after_tool_call)
        await queue.put(("end", outcome))

    async def _schedule() -> None:
        shared: list[asyncio.Task] = []
        for call in tool_calls:
            tool = by_name.get(call.name)
            if tool is None:
                await queue.put(("end", _error_outcome(call, f"Unknown tool: {call.name}")))
                continue
            if tool.concurrency == "exclusive":
                if shared:
                    await asyncio.gather(*shared)
                    shared = []
                await _run_and_enqueue(call, tool)
            else:
                shared.append(asyncio.create_task(_run_and_enqueue(call, tool)))
        if shared:
            await asyncio.gather(*shared)

    scheduler = asyncio.create_task(_schedule())
    seen_ends = 0
    try:
        while seen_ends < total:
            kind, payload = await queue.get()
            yield kind, payload
            if kind == "end":
                seen_ends += 1
    finally:
        await scheduler
```

把 `execute_tool_calls` 替换为基于 stream 的源序收集器：

```python
async def execute_tool_calls(
    tool_calls: list[ToolCall],
    tools: list[AgentTool],
    *,
    signal: Any = None,
    before_tool_call: Callable[[ToolCall, BaseModel], Awaitable[dict | None]] | None = None,
    after_tool_call=None,
) -> list[ToolExecutionOutcome]:
    """Batch API: collect outcomes, return in SOURCE order."""
    by_id: dict[str, ToolExecutionOutcome] = {}
    async for kind, payload in stream_tool_executions(
        tool_calls,
        tools,
        signal=signal,
        before_tool_call=before_tool_call,
        after_tool_call=after_tool_call,
    ):
        if kind == "end":
            by_id[payload.tool_call_id] = payload
    return [by_id[c.id] for c in tool_calls]
```

- [ ] **Step 4: loop 工具执行段消费 stream（完成序 end）**

把 Task 1/2 的 "executed = await execute_tool_calls(...) → zip 发 end" 段替换为：先发被拒 end，再流式发批准的 end：

```python
        # denied tools did not execute: emit their ends up front (G5 needs start+end per id)
        for c in tool_calls:
            if c.id in denied_results:
                o = denied_results[c.id]
                yield ToolExecutionEnd(
                    tool_call_id=c.id, tool_name=c.name, result=o.message, is_error=True
                )

        executed_by_id: dict[str, ToolExecutionOutcome] = {}
        async for kind, payload in stream_tool_executions(
            approved,
            tools,
            signal=signal,
            before_tool_call=config.before_tool_call,
            after_tool_call=config.after_tool_call,
        ):
            if kind == "end":
                outcome = payload
                yield ToolExecutionEnd(
                    tool_call_id=outcome.tool_call_id,
                    tool_name=outcome.tool_name,
                    result=outcome.message,
                    is_error=outcome.is_error,
                )
                executed_by_id[outcome.tool_call_id] = outcome

        outcomes = [denied_results.get(c.id) or executed_by_id[c.id] for c in tool_calls]
        for o in outcomes:
            yield MessageStart(message=o.message)
            history.append(o.message)
            produced.append(o.message)
            yield MessageEnd(message=o.message)
        yield TurnEnd(message=assistant, tool_results=[o.message for o in outcomes])
```

在 `loop.py` 顶部 import 段把 `execute_tool_calls` 换/补为 `stream_tool_executions`：`from nanoagent.agent.tools import AgentTool, ToolExecutionOutcome, stream_tool_executions`（`execute_tool_calls` 在 loop 中已不再直接用，可移除该名）。

> G6 仍成立：所有 end（被拒 + 批准）都在 transcript 之前；transcript 按 `tool_calls` 源序。G5 仍成立：每 id 一 start（前段已发）一 end。

- [ ] **Step 5: 导出 `stream_tool_executions`**

`src/nanoagent/agent/__init__.py` 的 tools 导入与 `__all__` 追加 `stream_tool_executions`。

- [ ] **Step 6: 跑测试验证通过**

Run: `pytest tests/agent/test_loop_tools.py -v`
Expected: PASS（完成序 end 测试过；既有工具测试仍过）。

- [ ] **Step 7: 全量回归 + 契约 + 提交**

Run: `pytest -q && lint-imports`
Expected: all pass；contract kept。

```bash
git add src/nanoagent/agent/tools.py src/nanoagent/agent/loop.py src/nanoagent/agent/__init__.py tests/agent/test_loop_tools.py
git commit -m "feat(agent): stream tool executions, emit tool_execution_end in completion order"
```

---

## Task 4: A2 — 进度通道 + 产出 `tool_execution_update`

给工具一个**可选**进度发射通道（opt-in，不破坏现有工具签名），loop 把进度转成 `tool_execution_update` 事件（落在该工具 start 与 end 之间，靠 `tool_call_id` 关联）。

**Files:**
- Modify: `src/nanoagent/agent/tools.py`（`_run_one` 加 `emit`；stream 发 `("update", ...)`）
- Modify: `src/nanoagent/agent/loop.py`（消费 `("update", ...)` → `ToolExecutionUpdate`）
- Test: `tests/agent/test_loop_tools.py`

**Interfaces:**
- Produces：工具可选实现 `async def execute(self, tool_call_id, params, signal=None, emit=None)`；`emit(partial: Any) -> None` 报告进度。
- 不实现 `emit` 参数的工具**照常工作**（用 `inspect` 探测，不传 `emit`）。
- stream 额外 yield `("update", (tool_call_id, tool_name, partial))`。

- [ ] **Step 1: 写失败测试（进度 update 落在 start/end 之间）**

`tests/agent/test_loop_tools.py` 追加：

```python
class _ProgressTool(AgentTool):
    name = "prog"
    description = "emits progress"
    parameters = _NoArgs
    label = "prog"

    async def execute(self, tool_call_id, params, signal=None, emit=None):
        if emit is not None:
            emit("step1")
            emit("step2")
        return AgentToolResult(content=[TextContent(text="done")])


@pytest.mark.asyncio
async def test_tool_execution_update_emitted_between_start_and_end():
    clear_providers()
    register_mock()
    mock = create_mock_model(
        responses=[
            {"content": [{"type": "toolCall", "name": "prog", "arguments": {}}]},
            {"content": ["ok"]},
        ]
    )
    cfg = AgentLoopConfig(model=mock)
    events = [
        e
        async for e in agent_loop(
            prompts=[UserMessage(content="go")],
            system_prompt=[],
            messages=[],
            tools=[_ProgressTool()],
            config=cfg,
        )
    ]
    types = [e.type for e in events]
    start_i = types.index("tool_execution_start")
    end_i = types.index("tool_execution_end")
    updates = [
        (i, e)
        for i, e in enumerate(events)
        if e.type == "tool_execution_update" and e.tool_call_id == "mock-tc-1"
    ]
    assert [e.partial_result for _, e in updates] == ["step1", "step2"]
    assert all(start_i < i < end_i for i, _ in updates)
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/agent/test_loop_tools.py::test_tool_execution_update_emitted_between_start_and_end -v`
Expected: FAIL（无 `tool_execution_update` 产出）。

- [ ] **Step 3: `_run_one` 加可选 `emit`（inspect 探测，向后兼容）**

`tools.py` 顶部已 `import asyncio`；再加 `import inspect`。把 `_run_one` 签名与 execute 调用改为：

```python
async def _run_one(
    call: ToolCall,
    tool: AgentTool,
    signal: Any,
    before_tool_call,
    after_tool_call=None,
    emit=None,
) -> ToolExecutionOutcome:
    try:
        params = tool.parameters.model_validate(call.arguments)
    except ValidationError as e:
        return _error_outcome(call, f"Invalid arguments: {e}")
    if before_tool_call is not None:
        decision = await before_tool_call(call, params)
        if decision is not None and decision.get("block"):
            return _error_outcome(call, decision.get("reason") or "Tool blocked")
    try:
        kwargs: dict[str, Any] = {}
        if emit is not None and "emit" in inspect.signature(tool.execute).parameters:
            kwargs["emit"] = emit
        result = await tool.execute(call.id, params, signal, **kwargs)
    except Exception as e:  # tool exceptions never propagate: encode as is_error
        outcome = _error_outcome(call, f"Tool failed: {e}")
    else:
        outcome = ToolExecutionOutcome(
            tool_call_id=call.id,
            tool_name=call.name,
            message=ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=result.content,
                is_error=result.is_error,
            ),
            details=result.details,
            terminate=result.terminate,
        )
    if after_tool_call is not None:
        revised = await after_tool_call(call, outcome)
        if revised is not None:
            outcome = revised
    return outcome
```

- [ ] **Step 4: stream 注入 `emit`，yield update**

在 `stream_tool_executions` 内，把 `_run_and_enqueue` 改为给每个 call 造一个 emit 回调（推 `("update", ...)` 进队列）：

```python
    def _emit_for(call: ToolCall):
        def emit(partial: Any) -> None:
            queue.put_nowait(("update", (call.id, call.name, partial)))

        return emit

    async def _run_and_enqueue(call: ToolCall, tool: AgentTool) -> None:
        outcome = await _run_one(
            call, tool, signal, before_tool_call, after_tool_call, _emit_for(call)
        )
        await queue.put(("end", outcome))
```

（生成器主体不变：`while seen_ends < total` 仍只对 `("end", ...)` 计数，`("update", ...)` 透传不计数。）

- [ ] **Step 5: loop 消费 `("update", ...)`**

Task 3 的 `async for kind, payload in stream_tool_executions(...)` 循环里，在 `if kind == "end":` 前加 update 分支：

```python
            if kind == "update":
                tcid, tname, partial = payload
                yield ToolExecutionUpdate(
                    tool_call_id=tcid, tool_name=tname, partial_result=partial
                )
                continue
            # kind == "end"
            outcome = payload
            ...
```

在 `loop.py` events import 中补 `ToolExecutionUpdate`（与 `ToolExecutionStart/End` 同处）。

- [ ] **Step 6: 跑测试验证通过**

Run: `pytest tests/agent/test_loop_tools.py::test_tool_execution_update_emitted_between_start_and_end -v`
Expected: PASS

- [ ] **Step 7: 全量回归 + 契约 + 提交**

Run: `pytest -q && lint-imports`
Expected: all pass；contract kept。

```bash
git add src/nanoagent/agent/tools.py src/nanoagent/agent/loop.py tests/agent/test_loop_tools.py
git commit -m "feat(agent): emit tool_execution_update from opt-in tool progress channel"
```

---

## Task 5: 契约锁定 + 全量回归

把"并行完成序 end + 进度 update"下 G1–G7 仍成立写成回归，并核对 `events.py` docstring 与现实一致。

**Files:**
- Test: `tests/agent/test_event_contract.py`
- Modify（按需）: `src/nanoagent/agent/events.py`（仅 docstring 措辞）

- [ ] **Step 1: 写并行+进度的契约测试**

`tests/agent/test_event_contract.py` 追加（复用文件顶部 `_collect`；新增带进度的工具）：

```python
import asyncio


class _ProgArgs(BaseModel):
    pass


class _ProgTool(AgentTool):
    description = "sleep + progress"
    parameters = _ProgArgs

    def __init__(self, name, delay):
        self.name = name
        self.label = name
        self._delay = delay

    async def execute(self, tool_call_id, params, signal=None, emit=None):
        if emit is not None:
            emit(f"{self.name}:tick")
        await asyncio.sleep(self._delay)
        return AgentToolResult(content=[TextContent(text=self.name)])


@pytest.mark.asyncio
async def test_event_contract_holds_under_parallel_and_progress():
    clear_providers()
    register_mock()
    mock = create_mock_model(
        responses=[
            {
                "content": [
                    {"type": "toolCall", "name": "a", "arguments": {}},
                    {"type": "toolCall", "name": "b", "arguments": {}},
                ]
            },
            {"content": ["done"]},
        ]
    )
    events = await _collect(
        AgentLoopConfig(model=mock), [_ProgTool("a", 0.05), _ProgTool("b", 0.0)]
    )

    # G5: per tool_call_id, exactly one start before exactly one end
    tstart: dict[str, int] = {}
    tend: dict[str, int] = {}
    for i, e in enumerate(events):
        if e.type == "tool_execution_start":
            assert e.tool_call_id not in tstart
            tstart[e.tool_call_id] = i
        elif e.type == "tool_execution_end":
            assert e.tool_call_id not in tend
            tend[e.tool_call_id] = i
    assert set(tstart) == set(tend)
    for cid in tstart:
        assert tstart[cid] < tend[cid]

    # updates fall strictly between their own tool's start and end (by id)
    for i, e in enumerate(events):
        if e.type == "tool_execution_update":
            cid = e.tool_call_id
            assert cid in tstart and tstart[cid] < i < tend[cid]

    # G6: tool-result transcript messages come after ALL ends, in source order
    last_tool_end = max(tend.values())
    tr = [
        e.message.content[0].text
        for e in events
        if e.type == "message_start" and e.message.role == "toolResult"
    ]
    tr_idx = [
        i
        for i, e in enumerate(events)
        if e.type == "message_start" and e.message.role == "toolResult"
    ]
    assert all(i > last_tool_end for i in tr_idx)
    assert tr == ["a", "b"]  # source order regardless of completion order
```

（`test_event_contract.py` 顶部需可用 `AgentTool`、`AgentToolResult`、`TextContent`——已 import `TextContent`、`AgentTool`、`AgentToolResult`、`pydantic.BaseModel`；若缺 `AgentToolResult` 则在顶部 import 补上。）

- [ ] **Step 2: 跑契约测试**

Run: `pytest tests/agent/test_event_contract.py -v`
Expected: PASS（G5 per-id、update 落点、G6 源序全部成立）。

- [ ] **Step 3: 核对 `events.py` docstring**

打开 `src/nanoagent/agent/events.py`，确认工具执行段说明（约 `:28-40`、`:64-68`）已与实现一致：update「emitted once tools stream progress」、parallel end「completion order」。若仍有"尚未产出/将来"类措辞，删去使其描述现状；不改任何类型。

- [ ] **Step 4: 全量回归 + 契约**

Run: `pytest -q && lint-imports`
Expected: `... passed`（应为 83 + 本计划新增用例数）；`Contracts: 1 kept, 0 broken.`

- [ ] **Step 5: 提交**

```bash
git add tests/agent/test_event_contract.py src/nanoagent/agent/events.py
git commit -m "test(agent): lock event contract under parallel completion-order ends + progress"
```

---

## Self-Review

**1. Spec coverage（对 Gate A 逐条）**

| Gate A 项 | 计划落点 |
|---|---|
| A0 结果通道（details/terminate 跨边界） | Task 1（`ToolExecutionOutcome` + `AgentToolResult.terminate`） |
| A1 完成序 `tool_execution_end` | Task 3（`stream_tool_executions` + loop 完成序发 end） |
| A2 产出 `tool_execution_update` | Task 4（opt-in `emit` 通道 + loop 转 update） |
| A3 `after_tool_call` | Task 2（`_run_one` 内对称钩子） |
| `terminate` 仅预留、不接 loop 收尾 | Task 1 加字段并存活到 outcome；loop **不读** `terminate`（无终止型工具拉动，符合 §3） |
| 契约 G1–G7 不破 | Task 1（事件形状不变）+ Task 5（并行+进度契约回归） |

**2. Placeholder scan**：无 TODO/TBD；每个改码 step 给了完整代码与确切命令。Task 3/4 对 `_run_one`/`execute_tool_calls`/`stream_tool_executions` 有多次演进，均显式给出当步完整函数体，非"同上"。

**3. Type consistency（跨任务签名核对）**：
- `ToolExecutionOutcome(tool_call_id, tool_name, message, details, terminate)` + `is_error` 属性：Task 1 定义，Task 2–5 一致消费（loop 用 `.message`/`.is_error`，钩子收发同型）。
- `_run_one(call, tool, signal, before_tool_call, after_tool_call=None, emit=None)`：Task 1→2→4 递增加参，调用点同步更新。
- `execute_tool_calls(..., before_tool_call=None, after_tool_call=None) -> list[ToolExecutionOutcome]`：Task 1 改返回型、Task 2 加 after、Task 3 改为 stream 收集器，签名前后一致。
- `stream_tool_executions(...) -> AsyncIterator[tuple[str, Any]]`，yield `("end", outcome)`（Task 3）+ `("update", (id,name,partial))`（Task 4）：loop 与 `execute_tool_calls` 消费一致。
- `AgentLoopConfig.after_tool_call`（Task 2）：loop 透传一致。
- 事件 `ToolExecutionEnd(result=outcome.message, is_error=outcome.is_error)`、`ToolExecutionUpdate(tool_call_id, tool_name, partial_result)`：与 `events.py` 现有字段一致，未改类型。

无发现不一致。

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-24-gate-a-tool-lifecycle.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — 每个 Task 派一个全新 subagent，任务间两段复审，迭代快。

**2. Inline Execution** — 在本会话内按 executing-plans 批执行，带 checkpoint 复审。

**Which approach?**
