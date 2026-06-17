# NanoAgent 框架（项目①）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 自建一个高解耦、低依赖的 Python agent 框架（`nanoagent`），刻意模仿 oh-my-pi 的包边界与依赖方向（`agent → ai → utils`），能脱离 harness 独立用 mock provider 跑通与测试。

**Architecture:** 三层单向 DAG。`ai` 持有 **wire `Message`**（供应商无关线路格式）+ 内容块 + provider 抽象 + 流式事件 + 累积器；`agent`（runtime）持有 **`AgentMessage`**（= wire `Message` ∪ 自定义类型）+ `convert_to_llm` 接缝 + 工具执行 + 控制面 + agent_loop + 有状态 `Agent`。loop 产出 `AgentEvent` 事件流，并以结构化 `RunResult` 收尾（事件流 + RunResult 合成）。流式用 async generator + `StreamAccumulator`（非移植 omp 的 EventStream 双接口）。

**Tech Stack:** Python 3.11+；`dataclasses`（消息/块/事件）；`pydantic` v2（工具参数 schema + 校验）；`httpx`（OpenAI-compatible provider 的异步 HTTP/SSE）；`pytest` + `pytest-asyncio`（测试）；`import-linter`（分层契约护栏）。

## Global Constraints

以下为 spec 钉死的项目级约束，每个 Task 的要求都隐含包含本节（值逐字照抄自 spec §15 / §4 / §8）：

- **包名 / import 前缀**：`nanoagent`，子模块 `nanoagent.{agent, ai, utils}`。
- **打包方式 B**：单安装包内分层子模块；`import-linter` 强制 `agent → ai → utils`，禁止反向、禁止环、禁止 `agent` import 任何 harness、禁止框架内出现"选哪个 provider / 哪把 key"。
- **语言**：Python，`asyncio`。
- **第一个真实 provider**：OpenAI-compatible（`base_url` 可切 deepseek 等）；`mock` 适配器必有。
- **边界纪律（机制 vs 策略）**：框架=机制；具体工具集 / 权限策略 / 预算数值 / 提示词 / provider 选择 = harness。框架只认 `Model` / `Tool` 抽象；**`agent` 包绝不出现具体名字**（Anthropic、read_file、claude-xxx、rm -rf）。`ai` 允许出现具体 provider 客户端代码，但藏在 provider 抽象之后，且不决定用哪个 provider / 哪把 key。
- **无副作用**：框架近纯；所有 I/O / 时钟 / 环境走注入端口。
- **只用 mock 跑通全部框架测试**：依赖真 provider/harness = 泄漏。
- **跳过的 omp 功能（YAGNI，明确不做）**：Rust natives、mnemopi 记忆、stats 遥测、catalog 模型目录、snapcompact、swarm、MCP、harmony-leak、append-only-context、cursor、auth-broker、成熟压缩、provider 专属 quirk、~40 个采样旋钮、followUp/aside 消息。
- **两级 StopReason 不混**：wire 级 `nanoagent.ai.StopReason`（每条 assistant 消息：stop/length/tool_use/error/aborted）；run 级 `nanoagent.agent.StopReason`（整轮终止：completed/max_turns/aborted/error）。

---

## File Structure

单包 `src/` 布局。每个文件单一职责，files-that-change-together 同目录。

```
NanoAgent/                              # 仓库根（= spec §15 的 nanoagent 目录，Windows 大小写不敏感）
  pyproject.toml                        # 包元数据 + 依赖 + pytest 配置
  .importlinter                         # 分层契约（agent → ai → utils）
  src/nanoagent/
    __init__.py
    utils/                              # 地基：零依赖
      __init__.py
      ids.py                            # new_id() 单调+随机 ID 生成
      logging.py                        # get_logger() 极薄封装
    ai/                                 # provider 抽象 + wire 线路格式 + 流式；依赖 utils
      __init__.py
      stop_reason.py                    # wire 级 StopReason 枚举
      messages.py                       # 内容块 + wire Message 联合 + Usage + Context
      events.py                         # AssistantMessageEvent 词汇
      model.py                          # Model 数据记录
      tools.py                          # Tool（wire schema：name/description/parameters）
      errors.py                         # ProviderError（status/code 结构化）
      accumulator.py                    # StreamAccumulator：事件折叠成 AssistantMessage
      options.py                        # StreamOptions（注入端口：api_key/base_url/signal/采样）
      provider.py                       # Provider 协议 + 注册表 + stream() 分发
      providers/
        __init__.py
        mock.py                         # MockModel + mock provider（测试地基）
        openai.py                       # OpenAI-compatible provider（真实适配器）
    agent/                              # runtime；依赖 ai, utils
      __init__.py
      messages.py                       # AgentMessage = Message | CustomMessage；default_convert_to_llm
      events.py                         # AgentEvent 词汇
      result.py                         # run 级 StopReason + RunResult 终止契约
      tools.py                          # AgentTool（Tool + execute）+ AgentToolResult + 并发执行器
      control.py                        # AbortSignal + ControlSource 协议 + steering
      context.py                        # assemble_context()；transform_context stub
      loop.py                           # agent_loop 异步生成器（心脏）+ AgentLoopConfig
      agent.py                          # 有状态 Agent 类
  tests/
    ai/
      test_messages.py  test_events.py  test_accumulator.py
      test_provider.py  test_mock.py    test_openai.py
    agent/
      test_messages.py  test_result.py  test_tools.py  test_control.py
      test_context.py   test_loop_text.py  test_loop_tools.py  test_loop_terminal.py
      test_agent.py
    test_import_contract.py             # import-linter 作为结构测试
```

**复审里程碑**：Task 1–8 完成 = `ai` 层独立成型（mock + 真实 provider 可 fake 模型）；Task 9–16 完成 = `agent` runtime 跑通全部终止路径与接缝。

---

## Phase A — `ai` 层 + 地基

### Task 1: 项目脚手架 + 分层契约

**Files:**
- Create: `pyproject.toml`
- Create: `.importlinter`
- Create: `src/nanoagent/__init__.py`, `src/nanoagent/utils/__init__.py`, `src/nanoagent/ai/__init__.py`, `src/nanoagent/agent/__init__.py`, `src/nanoagent/ai/providers/__init__.py`
- Test: `tests/test_import_contract.py`

**Interfaces:**
- Produces: 可安装的 `nanoagent` 包；`lint-imports` CLI 可运行；分层契约 `agent → ai → utils`。

- [ ] **Step 1: 写 pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "nanoagent"
version = "0.0.1"
description = "A small, decoupled agent framework (ai + agent runtime), modeled on oh-my-pi."
requires-python = ">=3.11"
dependencies = ["pydantic>=2.6", "httpx>=0.27"]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "import-linter>=2.0"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 2: 写 .importlinter 分层契约**

```ini
[importlinter]
root_package = nanoagent

[importlinter:contract:layers]
name = nanoagent layered architecture
type = layers
layers =
    nanoagent.agent
    nanoagent.ai
    nanoagent.utils
```

- [ ] **Step 3: 建空包文件**

每个 `__init__.py` 内容为空（仅占位，后续 Task 填充导出）。根 `src/nanoagent/__init__.py`：

```python
"""nanoagent: a small, decoupled agent framework."""
```

- [ ] **Step 4: 写结构测试（先失败）**

`tests/test_import_contract.py`：

```python
import subprocess
import sys


def test_import_linter_contract_holds():
    result = subprocess.run(
        [sys.executable, "-m", "importlinter.cli", "lint"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
```

- [ ] **Step 5: 安装并跑测试，验证通过**

Run: `pip install -e ".[dev]" && pytest tests/test_import_contract.py -v`
Expected: PASS（空层无违规）。若 `importlinter.cli` 模块路径报错，改用 `["lint-imports"]` 控制台脚本。

- [ ] **Step 6: 初始化 git 并提交**

```bash
git init
git add pyproject.toml .importlinter src tests
git commit -m "chore: scaffold nanoagent package with import-linter layering contract"
```

---

### Task 2: utils — ID 生成 + 日志

**Files:**
- Create: `src/nanoagent/utils/ids.py`, `src/nanoagent/utils/logging.py`
- Modify: `src/nanoagent/utils/__init__.py`
- Test: `tests/` 暂无独立测试（由后续消费方覆盖；本任务仅验证可导入与唯一性）

**Interfaces:**
- Produces: `new_id(prefix: str = "") -> str`（单调递增 + 随机后缀，进程内唯一）；`get_logger(name: str) -> logging.Logger`。

- [ ] **Step 1: 写失败测试**

`tests/utils/test_ids.py`:

```python
from nanoagent.utils import new_id


def test_new_id_unique_and_prefixed():
    ids = {new_id("msg") for _ in range(1000)}
    assert len(ids) == 1000
    assert all(i.startswith("msg_") for i in ids)
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/utils/test_ids.py -v`
Expected: FAIL with `ImportError: cannot import name 'new_id'`

- [ ] **Step 3: 实现 ids.py**

```python
import itertools
import secrets

_counter = itertools.count(1)


def new_id(prefix: str = "") -> str:
    """进程内唯一 ID：单调计数 + 随机后缀。无时钟依赖（spec：无副作用）。"""
    n = next(_counter)
    rand = secrets.token_hex(4)
    body = f"{n:012x}{rand}"
    return f"{prefix}_{body}" if prefix else body
```

- [ ] **Step 4: 实现 logging.py**

```python
import logging


def get_logger(name: str) -> logging.Logger:
    """极薄封装：命名 logger，不强加 handler（由 harness 配置）。"""
    return logging.getLogger(f"nanoagent.{name}")
```

- [ ] **Step 5: 导出**

`src/nanoagent/utils/__init__.py`:

```python
from nanoagent.utils.ids import new_id
from nanoagent.utils.logging import get_logger

__all__ = ["new_id", "get_logger"]
```

- [ ] **Step 6: 跑测试 + 提交**

Run: `pytest tests/utils/test_ids.py -v`
Expected: PASS

```bash
git add src/nanoagent/utils tests/utils
git commit -m "feat(utils): add new_id and get_logger"
```

---

### Task 3: ai — wire 内容块 + Message + Usage + Context

**Files:**
- Create: `src/nanoagent/ai/stop_reason.py`, `src/nanoagent/ai/messages.py`
- Modify: `src/nanoagent/ai/__init__.py`
- Test: `tests/ai/test_messages.py`

**Interfaces:**
- Consumes: `nanoagent.utils.new_id`。
- Produces:
  - `StopReason`（wire 级 `Enum`：`STOP/LENGTH/TOOL_USE/ERROR/ABORTED`）。
  - 内容块 dataclass：`TextContent(text)`, `ThinkingContent(thinking)`, `ImageContent(data, mime_type)`, `ToolCall(id, name, arguments)`，各带 `type` 判别字段。
  - `Usage(input=0, output=0, total_tokens=0)`。
  - 消息 dataclass（各带 `role` 判别字段 + `id`）：`UserMessage(content)`, `AssistantMessage(content, model, provider, api, usage, stop_reason, error_message=None)`, `ToolResultMessage(tool_call_id, tool_name, content, is_error=False)`。
  - `Message = UserMessage | AssistantMessage | ToolResultMessage`。
  - `Context(system_prompt, messages, tools)`。

- [ ] **Step 1: 写失败测试**

`tests/ai/test_messages.py`:

```python
from nanoagent.ai import (
    AssistantMessage, Context, StopReason, TextContent, ToolCall,
    ToolResultMessage, UserMessage, Usage,
)


def test_user_message_defaults():
    m = UserMessage(content="hi")
    assert m.role == "user"
    assert m.id  # auto-generated

def test_assistant_message_carries_wire_fields():
    m = AssistantMessage(
        content=[TextContent(text="ok"), ToolCall(id="t1", name="echo", arguments={"x": 1})],
        model="m", provider="mock", api="mock",
        usage=Usage(input=1, output=2, total_tokens=3),
        stop_reason=StopReason.TOOL_USE,
    )
    assert m.role == "assistant"
    assert m.content[1].type == "toolCall"
    assert m.stop_reason is StopReason.TOOL_USE

def test_tool_result_message():
    r = ToolResultMessage(tool_call_id="t1", tool_name="echo", content=[TextContent(text="1")])
    assert r.role == "toolResult" and r.is_error is False

def test_context_holds_messages():
    ctx = Context(system_prompt=["sys"], messages=[UserMessage(content="hi")])
    assert ctx.messages[0].role == "user" and ctx.tools == []
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/ai/test_messages.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: 实现 stop_reason.py**

```python
from enum import Enum


class StopReason(str, Enum):
    """Wire 级停止原因（每条 assistant 消息）。区别于 agent.StopReason（整轮终止）。"""
    STOP = "stop"
    LENGTH = "length"
    TOOL_USE = "tool_use"
    ERROR = "error"
    ABORTED = "aborted"
```

- [ ] **Step 4: 实现 messages.py**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union

from nanoagent.ai.stop_reason import StopReason
from nanoagent.utils import new_id


# ---- 内容块 ----
@dataclass
class TextContent:
    text: str
    type: Literal["text"] = "text"


@dataclass
class ThinkingContent:
    thinking: str
    type: Literal["thinking"] = "thinking"


@dataclass
class ImageContent:
    data: str  # base64
    mime_type: str
    type: Literal["image"] = "image"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    type: Literal["toolCall"] = "toolCall"


AssistantContent = Union[TextContent, ThinkingContent, ToolCall]
UserContent = Union[TextContent, ImageContent]


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    total_tokens: int = 0


# ---- 消息 ----
@dataclass
class UserMessage:
    content: str | list[UserContent]
    role: Literal["user"] = "user"
    id: str = field(default_factory=lambda: new_id("msg"))


@dataclass
class AssistantMessage:
    content: list[AssistantContent]
    model: str
    provider: str
    api: str
    usage: Usage
    stop_reason: StopReason
    error_message: str | None = None
    role: Literal["assistant"] = "assistant"
    id: str = field(default_factory=lambda: new_id("msg"))


@dataclass
class ToolResultMessage:
    tool_call_id: str
    tool_name: str
    content: list[UserContent]
    is_error: bool = False
    role: Literal["toolResult"] = "toolResult"
    id: str = field(default_factory=lambda: new_id("msg"))


Message = Union[UserMessage, AssistantMessage, ToolResultMessage]


@dataclass
class Context:
    system_prompt: list[str] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    tools: list[Any] = field(default_factory=list)  # list[Tool]，避免 Task 3 反向依赖 tools.py
```

- [ ] **Step 5: 导出（ai/__init__.py）**

```python
from nanoagent.ai.messages import (
    AssistantContent, AssistantMessage, Context, ImageContent, Message,
    TextContent, ThinkingContent, ToolCall, ToolResultMessage, UserContent,
    UserMessage, Usage,
)
from nanoagent.ai.stop_reason import StopReason

__all__ = [
    "StopReason", "TextContent", "ThinkingContent", "ImageContent", "ToolCall",
    "AssistantContent", "UserContent", "Usage", "UserMessage", "AssistantMessage",
    "ToolResultMessage", "Message", "Context",
]
```

- [ ] **Step 6: 跑测试 + 契约 + 提交**

Run: `pytest tests/ai/test_messages.py tests/test_import_contract.py -v`
Expected: PASS

```bash
git add src/nanoagent/ai tests/ai/test_messages.py
git commit -m "feat(ai): add wire message model, content blocks, Usage, Context"
```

---

### Task 4: ai — 流式事件词汇 + StreamAccumulator

**Files:**
- Create: `src/nanoagent/ai/events.py`, `src/nanoagent/ai/accumulator.py`
- Modify: `src/nanoagent/ai/__init__.py`
- Test: `tests/ai/test_events.py`, `tests/ai/test_accumulator.py`

**Interfaces:**
- Consumes: `AssistantMessage`, `TextContent`, `ThinkingContent`, `ToolCall`, `Usage`, `StopReason`, `Model`(仅类型，见下注)。
- Produces:
  - `AssistantMessageEvent` 联合：`StreamStart`、`TextStart/TextDelta/TextEnd(content_index)`、`ThinkingStart/ThinkingDelta/ThinkingEnd`、`ToolCallStart/ToolCallDelta/ToolCallEnd(tool_call)`、`StreamDone(message)`、`StreamError(message)`。
  - `StreamAccumulator(model_id, provider, api)`：`.add(event)`、`.message -> AssistantMessage`（当前部分消息）。
  - `async def accumulate(events: AsyncIterator[AssistantMessageEvent]) -> AssistantMessage`：消费整条流，返回 `done`/`error` 事件携带的最终消息（缺失则返回折叠出的消息）。

> 注：accumulator 不依赖 `Model`，只取 `model_id/provider/api` 三个字符串，避免与 model.py 的循环。

- [ ] **Step 1: 写失败测试**

`tests/ai/test_accumulator.py`:

```python
import pytest

from nanoagent.ai import (
    AssistantMessage, StopReason, StreamDone, StreamStart, TextDelta, TextEnd,
    TextStart, ToolCallDelta, ToolCallEnd, ToolCall, Usage, accumulate,
)


async def _gen(events):
    for e in events:
        yield e


@pytest.mark.asyncio
async def test_accumulate_returns_done_message():
    msg = AssistantMessage(content=[], model="m", provider="mock", api="mock",
                           usage=Usage(), stop_reason=StopReason.STOP)
    out = await accumulate(_gen([StreamStart(), StreamDone(message=msg)]))
    assert out is msg

@pytest.mark.asyncio
async def test_accumulate_folds_text_when_no_done_message():
    from nanoagent.ai.accumulator import StreamAccumulator
    acc = StreamAccumulator(model_id="m", provider="mock", api="mock")
    for e in [StreamStart(), TextStart(content_index=0),
              TextDelta(content_index=0, delta="he"),
              TextDelta(content_index=0, delta="llo"),
              TextEnd(content_index=0, text="hello")]:
        acc.add(e)
    assert acc.message.content[0].text == "hello"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/ai/test_accumulator.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: 实现 events.py**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from nanoagent.ai.messages import AssistantMessage, ToolCall


@dataclass
class StreamStart:
    type: str = "start"


@dataclass
class TextStart:
    content_index: int
    type: str = "text_start"


@dataclass
class TextDelta:
    content_index: int
    delta: str
    type: str = "text_delta"


@dataclass
class TextEnd:
    content_index: int
    text: str
    type: str = "text_end"


@dataclass
class ThinkingStart:
    content_index: int
    type: str = "thinking_start"


@dataclass
class ThinkingDelta:
    content_index: int
    delta: str
    type: str = "thinking_delta"


@dataclass
class ThinkingEnd:
    content_index: int
    thinking: str
    type: str = "thinking_end"


@dataclass
class ToolCallStart:
    content_index: int
    type: str = "toolcall_start"


@dataclass
class ToolCallDelta:
    content_index: int
    delta: str
    type: str = "toolcall_delta"


@dataclass
class ToolCallEnd:
    content_index: int
    tool_call: ToolCall
    type: str = "toolcall_end"


@dataclass
class StreamDone:
    message: AssistantMessage
    type: str = "done"


@dataclass
class StreamError:
    message: AssistantMessage
    type: str = "error"


AssistantMessageEvent = Union[
    StreamStart, TextStart, TextDelta, TextEnd,
    ThinkingStart, ThinkingDelta, ThinkingEnd,
    ToolCallStart, ToolCallDelta, ToolCallEnd,
    StreamDone, StreamError,
]
```

- [ ] **Step 4: 实现 accumulator.py**

```python
from __future__ import annotations

from typing import AsyncIterator

from nanoagent.ai.events import (
    AssistantMessageEvent, StreamDone, StreamError, TextEnd, TextStart,
    ThinkingEnd, ThinkingStart, ToolCallEnd, ToolCallStart,
)
from nanoagent.ai.messages import (
    AssistantMessage, TextContent, ThinkingContent, ToolCall, Usage,
)
from nanoagent.ai.stop_reason import StopReason


class StreamAccumulator:
    """把增量事件折叠成一条 AssistantMessage（消费方/UI 助手）。"""

    def __init__(self, model_id: str, provider: str, api: str):
        self._msg = AssistantMessage(
            content=[], model=model_id, provider=provider, api=api,
            usage=Usage(), stop_reason=StopReason.STOP,
        )
        self._text: dict[int, list[str]] = {}
        self._thinking: dict[int, list[str]] = {}

    @property
    def message(self) -> AssistantMessage:
        return self._msg

    def add(self, event: AssistantMessageEvent) -> None:
        t = event.type
        if t == "text_start":
            self._text[event.content_index] = []
            self._msg.content.append(TextContent(text=""))
        elif t == "text_delta":
            self._text[event.content_index].append(event.delta)
        elif t == "text_end":
            self._msg.content[event.content_index] = TextContent(text=event.text)
        elif t == "thinking_start":
            self._thinking[event.content_index] = []
            self._msg.content.append(ThinkingContent(thinking=""))
        elif t == "thinking_delta":
            self._thinking[event.content_index].append(event.delta)
        elif t == "thinking_end":
            self._msg.content[event.content_index] = ThinkingContent(thinking=event.thinking)
        elif t == "toolcall_start":
            self._msg.content.append(ToolCall(id="", name="", arguments={}))
        elif t == "toolcall_end":
            self._msg.content[event.content_index] = event.tool_call
        elif t in ("done", "error"):
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
```

- [ ] **Step 5: 导出 + 写 events 测试**

`ai/__init__.py` 追加导出全部事件类型 + `StreamAccumulator` + `accumulate`。
`tests/ai/test_events.py`:

```python
from nanoagent.ai import StreamDone, TextDelta


def test_event_type_discriminators():
    assert TextDelta(content_index=0, delta="x").type == "text_delta"
```

- [ ] **Step 6: 跑测试 + 提交**

Run: `pytest tests/ai/test_events.py tests/ai/test_accumulator.py tests/test_import_contract.py -v`
Expected: PASS

```bash
git add src/nanoagent/ai tests/ai/test_events.py tests/ai/test_accumulator.py
git commit -m "feat(ai): add streaming event vocabulary and StreamAccumulator"
```

---

### Task 5: ai — Model + Tool + errors + StreamOptions

**Files:**
- Create: `src/nanoagent/ai/model.py`, `src/nanoagent/ai/tools.py`, `src/nanoagent/ai/errors.py`, `src/nanoagent/ai/options.py`
- Modify: `src/nanoagent/ai/__init__.py`
- Test: `tests/ai/test_model_tool.py`

**Interfaces:**
- Produces:
  - `Model(id, api, provider, base_url=None, context_window=200_000, max_tokens=32_768, reasoning=False)`。
  - `Tool(name, description, parameters)`：`parameters` 为 JSON Schema `dict`（模型看到的形状）。
  - `ProviderError(message, *, status=None, code=None)`：异常，带结构化 `status`/`code`。
  - `StreamOptions(api_key=None, base_url=None, signal=None, temperature=None, max_tokens=None, reasoning=None)`：注入端口。`signal` 类型为 `Any`（实际为 `agent.AbortSignal`，但 ai 不得依赖 agent，故用结构鸭子类型：只读 `.aborted`）。

- [ ] **Step 1: 写失败测试**

`tests/ai/test_model_tool.py`:

```python
import pytest

from nanoagent.ai import Model, ProviderError, StreamOptions, Tool


def test_model_defaults():
    m = Model(id="gpt", api="openai-completions", provider="openai")
    assert m.context_window == 200_000 and m.reasoning is False

def test_tool_holds_json_schema():
    t = Tool(name="echo", description="echo back", parameters={"type": "object"})
    assert t.parameters["type"] == "object"

def test_provider_error_structured():
    err = ProviderError("rate limited", status=429, code="rate_limit")
    assert err.status == 429 and err.code == "rate_limit"
    with pytest.raises(ProviderError):
        raise err

def test_stream_options_defaults():
    assert StreamOptions().temperature is None
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/ai/test_model_tool.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: 实现 model.py / tools.py / errors.py / options.py**

`model.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Model:
    id: str
    api: str          # 分发键：mock / openai-completions / ...
    provider: str     # 展示/归因用：mock / openai / deepseek
    base_url: str | None = None
    context_window: int = 200_000
    max_tokens: int = 32_768
    reasoning: bool = False
```

`tools.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Tool:
    """模型看到的工具线路形状。execute 在 agent.AgentTool 上。"""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
```

`errors.py`:

```python
from __future__ import annotations


class ProviderError(Exception):
    """Provider 端结构化失败。下游按字段分类，不靠 message 正则。"""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
```

`options.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class StreamOptions:
    """注入端口：harness 决定 key/base_url/采样；框架不决定选哪个 provider。"""
    api_key: str | None = None
    base_url: str | None = None
    signal: Any = None       # 鸭子类型：读 .aborted（实际为 agent.AbortSignal）
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning: str | None = None
```

- [ ] **Step 4: 导出**

`ai/__init__.py` 追加：`Model`, `Tool`, `ProviderError`, `StreamOptions`。同时把 `Context.tools` 的注释类型落实为 `list[Tool]`（无需改代码）。

- [ ] **Step 5: 跑测试 + 提交**

Run: `pytest tests/ai/test_model_tool.py tests/test_import_contract.py -v`
Expected: PASS

```bash
git add src/nanoagent/ai tests/ai/test_model_tool.py
git commit -m "feat(ai): add Model, Tool, ProviderError, StreamOptions"
```

---

### Task 6: ai — Provider 协议 + 注册表 + mock provider

**Files:**
- Create: `src/nanoagent/ai/provider.py`, `src/nanoagent/ai/providers/mock.py`
- Modify: `src/nanoagent/ai/__init__.py`, `src/nanoagent/ai/providers/__init__.py`
- Test: `tests/ai/test_provider.py`, `tests/ai/test_mock.py`

**Interfaces:**
- Consumes: `Model`, `Context`, `StreamOptions`, `AssistantMessageEvent` 全套, `AssistantMessage`, `StopReason`, `Usage`。
- Produces:
  - `Provider` 协议：`def stream(model, context, options) -> AsyncIterator[AssistantMessageEvent]`。
  - 注册表：`register_provider(api: str, provider: Provider)`, `get_provider(api) -> Provider`, `clear_providers()`。
  - 分发函数：`async def stream(model, context, options=None) -> AsyncIterator[AssistantMessageEvent]`（按 `model.api` 查表）。
  - `MockModel(...)` / `create_mock_model(responses=..., handler=...) -> MockModel`；`MockResponse` 形状（`content: list[str | dict]`, `stop_reason=None`, `error=None`）；`MockModel.calls` 记录调用；`register_mock()`（注册 `"mock"` api）。

- [ ] **Step 1: 写失败测试**

`tests/ai/test_mock.py`:

```python
import pytest

from nanoagent.ai import Context, StopReason, UserMessage
from nanoagent.ai.providers.mock import create_mock_model


@pytest.mark.asyncio
async def test_mock_streams_text_then_done():
    mock = create_mock_model(responses=[{"content": ["hello"]}])
    ctx = Context(messages=[UserMessage(content="hi")])
    events = [e async for e in mock.stream(mock, ctx, None)]
    assert events[0].type == "start"
    assert events[-1].type == "done"
    assert events[-1].message.content[0].text == "hello"
    assert events[-1].message.stop_reason is StopReason.STOP
    assert len(mock.calls) == 1

@pytest.mark.asyncio
async def test_mock_tool_call_sets_tool_use():
    mock = create_mock_model(responses=[
        {"content": [{"type": "toolCall", "name": "echo", "arguments": {"x": 1}}]},
    ])
    events = [e async for e in mock.stream(mock, Context(), None)]
    done = events[-1]
    assert done.message.stop_reason is StopReason.TOOL_USE
    assert done.message.content[0].name == "echo"
```

`tests/ai/test_provider.py`:

```python
import pytest

from nanoagent.ai import Context
from nanoagent.ai.provider import clear_providers, get_provider, register_provider, stream
from nanoagent.ai.providers.mock import create_mock_model, register_mock


@pytest.mark.asyncio
async def test_dispatch_by_api():
    clear_providers()
    register_mock()
    mock = create_mock_model(responses=[{"content": ["ok"]}])
    events = [e async for e in stream(mock, Context(), None)]
    assert events[-1].message.content[0].text == "ok"

def test_get_provider_unknown_raises():
    clear_providers()
    with pytest.raises(KeyError):
        get_provider("nope")
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/ai/test_mock.py tests/ai/test_provider.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: 实现 provider.py**

```python
from __future__ import annotations

from typing import AsyncIterator, Protocol

from nanoagent.ai.events import AssistantMessageEvent
from nanoagent.ai.messages import Context
from nanoagent.ai.model import Model
from nanoagent.ai.options import StreamOptions


class Provider(Protocol):
    def stream(
        self, model: Model, context: Context, options: StreamOptions | None
    ) -> AsyncIterator[AssistantMessageEvent]: ...


_REGISTRY: dict[str, Provider] = {}


def register_provider(api: str, provider: Provider) -> None:
    _REGISTRY[api] = provider


def get_provider(api: str) -> Provider:
    if api not in _REGISTRY:
        raise KeyError(f"no provider registered for api {api!r}")
    return _REGISTRY[api]


def clear_providers() -> None:
    _REGISTRY.clear()


def stream(
    model: Model, context: Context, options: StreamOptions | None = None
) -> AsyncIterator[AssistantMessageEvent]:
    """按 model.api 分发。框架不决定 provider/key——那是 options 注入的策略。"""
    return get_provider(model.api).stream(model, context, options)
```

- [ ] **Step 4: 实现 providers/mock.py**

```python
from __future__ import annotations

from typing import Any, AsyncIterator, Callable

from nanoagent.ai.events import (
    AssistantMessageEvent, StreamDone, StreamError, StreamStart, TextDelta,
    TextEnd, TextStart, ToolCallDelta, ToolCallEnd, ToolCallStart,
)
from nanoagent.ai.messages import (
    AssistantMessage, Context, TextContent, ToolCall, Usage,
)
from nanoagent.ai.model import Model
from nanoagent.ai.options import StreamOptions
from nanoagent.ai.provider import register_provider
from nanoagent.ai.stop_reason import StopReason


class MockModel(Model):
    def __init__(self, *, id: str = "mock-model", provider: str = "mock",
                 responses: list[dict] | None = None,
                 handler: Callable[[Context], dict] | None = None):
        super().__init__(id=id, api="mock", provider=provider)
        self._responses = list(responses or [])
        self._handler = handler
        self._idx = 0
        self.calls: list[Context] = []
        self._tc = 0

    def stream(self, model: Model, context: Context,
               options: StreamOptions | None) -> AsyncIterator[AssistantMessageEvent]:
        return self._run(context)

    def _next_response(self, context: Context) -> dict:
        if self._idx < len(self._responses):
            r = self._responses[self._idx]
            self._idx += 1
            return r
        if self._handler is not None:
            return self._handler(context)
        raise AssertionError(f"mock exhausted at call {len(self.calls)}")

    async def _run(self, context: Context) -> AsyncIterator[AssistantMessageEvent]:
        self.calls.append(context)
        resp = self._next_response(context)
        msg = AssistantMessage(content=[], model=self.id, provider=self.provider,
                               api=self.api, usage=Usage(), stop_reason=StopReason.STOP)
        yield StreamStart()
        for i, block in enumerate(resp.get("content", [])):
            if isinstance(block, str):
                msg.content.append(TextContent(text=block))
                yield TextStart(content_index=i)
                yield TextDelta(content_index=i, delta=block)
                yield TextEnd(content_index=i, text=block)
            elif block.get("type") == "toolCall":
                self._tc += 1
                tc = ToolCall(id=block.get("id") or f"mock-tc-{self._tc}",
                              name=block["name"], arguments=dict(block["arguments"]))
                msg.content.append(tc)
                yield ToolCallStart(content_index=i)
                yield ToolCallDelta(content_index=i, delta=str(tc.arguments))
                yield ToolCallEnd(content_index=i, tool_call=tc)
        has_tool = any(getattr(c, "type", None) == "toolCall" for c in msg.content)
        reason = resp.get("stop_reason") or (StopReason.TOOL_USE if has_tool else StopReason.STOP)
        msg.stop_reason = reason
        if resp.get("error"):
            msg.stop_reason = StopReason.ERROR
            msg.error_message = resp["error"]
            yield StreamError(message=msg)
            return
        yield StreamDone(message=msg)


def create_mock_model(*, responses: list[dict] | None = None,
                      handler: Callable[[Context], dict] | None = None,
                      id: str = "mock-model", provider: str = "mock") -> MockModel:
    return MockModel(id=id, provider=provider, responses=responses, handler=handler)


def register_mock() -> None:
    """注册 'mock' api，分发到调用方传入的 MockModel.stream（model 自带流）。"""
    class _MockDispatch:
        def stream(self, model, context, options):
            return model.stream(model, context, options)
    register_provider("mock", _MockDispatch())
```

- [ ] **Step 5: 导出**

`ai/__init__.py` 追加：`Provider`, `register_provider`, `get_provider`, `clear_providers`, `stream`。
`providers/__init__.py`：

```python
from nanoagent.ai.providers.mock import create_mock_model, register_mock, MockModel

__all__ = ["create_mock_model", "register_mock", "MockModel"]
```

- [ ] **Step 6: 跑测试 + 契约 + 提交**

Run: `pytest tests/ai/ tests/test_import_contract.py -v`
Expected: PASS

```bash
git add src/nanoagent/ai tests/ai/test_provider.py tests/ai/test_mock.py
git commit -m "feat(ai): add Provider protocol, registry, dispatch, and mock provider"
```

---

### Task 7: ai — OpenAI-compatible 真实 provider

**Files:**
- Create: `src/nanoagent/ai/providers/openai.py`
- Modify: `src/nanoagent/ai/providers/__init__.py`
- Test: `tests/ai/test_openai.py`

**Interfaces:**
- Consumes: `Model`, `Context`, `StreamOptions`, 全套事件, `AssistantMessage`, `ToolCall`, `Usage`, `StopReason`, `ProviderError`, `httpx`。
- Produces:
  - `encode_request(model, context, options) -> dict`：把 `Context`（system_prompt + Message[] + Tool[]）编码为 OpenAI chat-completions JSON（`messages`/`tools`/`stream=True`）。纯函数，可单测。
  - `parse_sse_line(line: str) -> dict | None`：解析单条 SSE `data:` 行（`[DONE]` 返回特殊标记）。纯函数。
  - `OpenAIProvider`：`async def stream(...)` 用 `httpx.AsyncClient` 发起 SSE，逐 chunk 产出事件。
  - `register_openai()`：注册 `"openai-completions"` api。

> 测试策略：用 `encode_request` / `parse_sse_line` 纯函数单测覆盖编解码（spec §12：真实适配器的流解析单测）。**不打真实网络**——HTTP 路径用 `httpx.MockTransport` 喂假 SSE。

- [ ] **Step 1: 写失败测试（编码 + 解析纯函数）**

`tests/ai/test_openai.py`:

```python
import pytest

from nanoagent.ai import Context, TextContent, Tool, UserMessage, AssistantMessage, Usage, StopReason
from nanoagent.ai.providers.openai import encode_request, parse_sse_line


def test_encode_request_maps_system_and_user():
    ctx = Context(system_prompt=["be brief"], messages=[UserMessage(content="hi")],
                  tools=[Tool(name="echo", description="e", parameters={"type": "object"})])
    from nanoagent.ai import Model
    payload = encode_request(Model(id="gpt-x", api="openai-completions", provider="openai"), ctx, None)
    assert payload["messages"][0] == {"role": "system", "content": "be brief"}
    assert payload["messages"][1]["role"] == "user"
    assert payload["tools"][0]["function"]["name"] == "echo"
    assert payload["stream"] is True

def test_encode_request_maps_assistant_tool_calls():
    from nanoagent.ai import Model, ToolCall
    ctx = Context(messages=[AssistantMessage(
        content=[ToolCall(id="t1", name="echo", arguments={"x": 1})],
        model="m", provider="openai", api="openai-completions",
        usage=Usage(), stop_reason=StopReason.TOOL_USE)])
    payload = encode_request(Model(id="m", api="openai-completions", provider="openai"), ctx, None)
    assert payload["messages"][0]["tool_calls"][0]["function"]["name"] == "echo"

def test_parse_sse_line_done_and_data():
    assert parse_sse_line("data: [DONE]") == {"__done__": True}
    assert parse_sse_line('data: {"a": 1}') == {"a": 1}
    assert parse_sse_line(": comment") is None
    assert parse_sse_line("") is None
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/ai/test_openai.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: 实现 openai.py 的编解码纯函数 + provider**

```python
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from nanoagent.ai.events import (
    AssistantMessageEvent, StreamDone, StreamError, StreamStart, TextDelta,
    TextEnd, TextStart, ToolCallDelta, ToolCallEnd, ToolCallStart,
)
from nanoagent.ai.errors import ProviderError
from nanoagent.ai.messages import (
    AssistantMessage, Context, Message, TextContent, ToolCall, Usage,
)
from nanoagent.ai.model import Model
from nanoagent.ai.options import StreamOptions
from nanoagent.ai.provider import register_provider
from nanoagent.ai.stop_reason import StopReason

_FINISH_MAP = {"stop": StopReason.STOP, "length": StopReason.LENGTH,
               "tool_calls": StopReason.TOOL_USE}


def _encode_message(m: Message) -> dict:
    if m.role == "user":
        text = m.content if isinstance(m.content, str) else "".join(
            b.text for b in m.content if getattr(b, "type", None) == "text")
        return {"role": "user", "content": text}
    if m.role == "assistant":
        text = "".join(b.text for b in m.content if b.type == "text")
        tool_calls = [{"id": b.id, "type": "function",
                       "function": {"name": b.name, "arguments": json.dumps(b.arguments)}}
                      for b in m.content if b.type == "toolCall"]
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
        payload["tools"] = [{"type": "function",
                             "function": {"name": t.name, "description": t.description,
                                          "parameters": t.parameters}} for t in context.tools]
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
    data = line[len("data:"):].strip()
    if data == "[DONE]":
        return {"__done__": True}
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


class OpenAIProvider:
    async def stream(self, model: Model, context: Context,
                     options: StreamOptions | None) -> AsyncIterator[AssistantMessageEvent]:
        opts = options or StreamOptions()
        base = opts.base_url or model.base_url or "https://api.openai.com/v1"
        url = base.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if opts.api_key:
            headers["Authorization"] = f"Bearer {opts.api_key}"
        payload = encode_request(model, context, options)
        msg = AssistantMessage(content=[], model=model.id, provider=model.provider,
                               api=model.api, usage=Usage(), stop_reason=StopReason.STOP)
        yield StreamStart()
        text_parts: list[str] = []
        tool_acc: dict[int, dict] = {}
        finish = "stop"
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code >= 400:
                        body = (await resp.aread()).decode("utf-8", "replace")
                        raise ProviderError(f"HTTP {resp.status_code}: {body}",
                                            status=resp.status_code)
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
                            slot = tool_acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function", {})
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["args"] += fn["arguments"]
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
        idx = 0
        if text_parts:
            full = "".join(text_parts)
            msg.content.append(TextContent(text=full))
            yield TextEnd(content_index=0, text=full)
            idx = 1
        for i, slot in tool_acc.items():
            try:
                args = json.loads(slot["args"]) if slot["args"] else {}
            except json.JSONDecodeError:
                args = {}
            tc = ToolCall(id=slot["id"], name=slot["name"], arguments=args)
            msg.content.append(tc)
            yield ToolCallStart(content_index=idx + i)
            yield ToolCallEnd(content_index=idx + i, tool_call=tc)
        msg.stop_reason = _FINISH_MAP.get(finish, StopReason.STOP)
        yield StreamDone(message=msg)


def register_openai() -> None:
    register_provider("openai-completions", OpenAIProvider())
```

- [ ] **Step 4: 跑纯函数测试，验证通过**

Run: `pytest tests/ai/test_openai.py -v`
Expected: PASS

- [ ] **Step 5: 加 SSE 流解析集成测试（MockTransport，不打网络）**

`tests/ai/test_openai.py` 追加：

```python
@pytest.mark.asyncio
async def test_stream_parses_text_and_tool_calls(monkeypatch):
    import httpx
    from nanoagent.ai import Model
    from nanoagent.ai.providers.openai import OpenAIProvider

    sse = (
        'data: {"choices":[{"delta":{"content":"he"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request):
        return httpx.Response(200, text=sse)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: real_client(transport=httpx.MockTransport(handler)),
    )
    prov = OpenAIProvider()
    model = Model(id="gpt-x", api="openai-completions", provider="openai")
    events = [e async for e in prov.stream(model, Context(messages=[UserMessage(content="hi")]), None)]
    assert events[-1].type == "done"
    assert events[-1].message.content[0].text == "hello"
    assert events[-1].message.stop_reason is StopReason.STOP
```

- [ ] **Step 6: 跑全部 ai 测试 + 契约 + 提交**

Run: `pytest tests/ai/ tests/test_import_contract.py -v`
Expected: PASS

```bash
git add src/nanoagent/ai tests/ai/test_openai.py
git commit -m "feat(ai): add OpenAI-compatible provider with SSE streaming"
```

---

## Phase B — `agent` runtime

### Task 8: agent — AgentMessage + convert_to_llm 默认

**Files:**
- Create: `src/nanoagent/agent/messages.py`
- Modify: `src/nanoagent/agent/__init__.py`
- Test: `tests/agent/test_messages.py`

**Interfaces:**
- Consumes: `ai.Message`, `ai.UserMessage`, `ai.AssistantMessage`, `ai.ToolResultMessage`。
- Produces:
  - `CustomMessage`：自定义消息基类（`role: str` 不在 wire 集合内；带 `id`）。
  - `AgentMessage = Message | CustomMessage`。
  - `default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]`：过滤到 wire 三类（user/assistant/toolResult），丢弃未知 custom（镜像 omp `defaultConvertToLlm`）。
  - `ConvertToLlm = Callable[[list[AgentMessage]], list[Message]]` 类型别名。

- [ ] **Step 1: 写失败测试**

`tests/agent/test_messages.py`:

```python
from dataclasses import dataclass

from nanoagent.ai import AssistantMessage, StopReason, UserMessage, Usage
from nanoagent.agent.messages import CustomMessage, default_convert_to_llm


@dataclass
class Notification(CustomMessage):
    text: str = ""
    role: str = "notification"


def test_default_convert_filters_custom():
    msgs = [
        UserMessage(content="hi"),
        Notification(text="ui only"),
        AssistantMessage(content=[], model="m", provider="mock", api="mock",
                         usage=Usage(), stop_reason=StopReason.STOP),
    ]
    wire = default_convert_to_llm(msgs)
    assert [m.role for m in wire] == ["user", "assistant"]
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/agent/test_messages.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: 实现 messages.py**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Union

from nanoagent.ai import AssistantMessage, Message, ToolResultMessage, UserMessage
from nanoagent.utils import new_id

_WIRE_ROLES = ("user", "assistant", "toolResult")


@dataclass
class CustomMessage:
    """app 自定义消息基类（UI-only / notification / artifact ...）。
    子类设 role 为非 wire 值，并由 harness 的 convert_to_llm 决定如何降级或丢弃。"""
    role: str = "custom"
    id: str = field(default_factory=lambda: new_id("msg"))


AgentMessage = Union[Message, CustomMessage]
ConvertToLlm = Callable[[list["AgentMessage"]], list[Message]]


def default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    """框架默认：保留 wire 三类，丢弃未知 custom（机制；具体降级是 harness 策略）。"""
    return [m for m in messages if getattr(m, "role", None) in _WIRE_ROLES]  # type: ignore[return-value]
```

- [ ] **Step 4: 导出**

`agent/__init__.py`:

```python
from nanoagent.agent.messages import AgentMessage, ConvertToLlm, CustomMessage, default_convert_to_llm

__all__ = ["AgentMessage", "ConvertToLlm", "CustomMessage", "default_convert_to_llm"]
```

- [ ] **Step 5: 跑测试 + 契约 + 提交**

Run: `pytest tests/agent/test_messages.py tests/test_import_contract.py -v`
Expected: PASS（契约确认 agent→ai 合法、无反向）

```bash
git add src/nanoagent/agent tests/agent/test_messages.py
git commit -m "feat(agent): add AgentMessage union, CustomMessage, default_convert_to_llm"
```

---

### Task 9: agent — AgentEvent 词汇 + RunResult 终止契约

**Files:**
- Create: `src/nanoagent/agent/events.py`, `src/nanoagent/agent/result.py`
- Modify: `src/nanoagent/agent/__init__.py`
- Test: `tests/agent/test_result.py`

**Interfaces:**
- Consumes: `AgentMessage`, `ai.AssistantMessage`, `ai.ToolResultMessage`, `ai.AssistantMessageEvent`。
- Produces:
  - run 级 `StopReason`（`Enum`：`COMPLETED/MAX_TURNS/ABORTED/ERROR`）——**与 `ai.StopReason` 同名不同物，分别从各自包导入**。
  - `RunResult(reason: StopReason, final_message_id: str | None = None, error: str | None = None, detail: dict | None = None)`。
  - `AgentEvent` 联合：`AgentStart`、`AgentEnd(messages, result)`、`TurnStart`、`TurnEnd(message, tool_results)`、`MessageStart(message)`、`MessageUpdate(message, assistant_event)`、`MessageEnd(message)`、`ToolExecutionStart(tool_call_id, tool_name, args)`、`ToolExecutionUpdate(...)`、`ToolExecutionEnd(tool_call_id, tool_name, result, is_error)`。

- [ ] **Step 1: 写失败测试**

`tests/agent/test_result.py`:

```python
from nanoagent.agent.result import RunResult, StopReason
from nanoagent.agent.events import AgentEnd


def test_run_result_fields():
    r = RunResult(reason=StopReason.COMPLETED, final_message_id="msg_1")
    assert r.reason is StopReason.COMPLETED and r.error is None

def test_agent_end_carries_result():
    r = RunResult(reason=StopReason.MAX_TURNS)
    ev = AgentEnd(messages=[], result=r)
    assert ev.type == "agent_end" and ev.result.reason is StopReason.MAX_TURNS

def test_run_stop_reason_distinct_from_wire():
    import nanoagent.ai as ai
    assert StopReason.COMPLETED.value == "completed"
    assert ai.StopReason.STOP.value == "stop"  # 两级不混
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/agent/test_result.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: 实现 result.py**

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StopReason(str, Enum):
    """Run 级终止原因（整轮 loop）。区别于 ai.StopReason（每条消息的 wire 停止）。"""
    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    ABORTED = "aborted"
    ERROR = "error"


@dataclass
class RunResult:
    reason: StopReason
    final_message_id: str | None = None
    error: str | None = None
    detail: dict | None = None
```

- [ ] **Step 4: 实现 events.py**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

from nanoagent.ai import AssistantMessageEvent, ToolResultMessage
from nanoagent.agent.messages import AgentMessage
from nanoagent.agent.result import RunResult


@dataclass
class AgentStart:
    type: str = "agent_start"


@dataclass
class AgentEnd:
    messages: list[AgentMessage]
    result: RunResult
    type: str = "agent_end"


@dataclass
class TurnStart:
    type: str = "turn_start"


@dataclass
class TurnEnd:
    message: AgentMessage
    tool_results: list[ToolResultMessage] = field(default_factory=list)
    type: str = "turn_end"


@dataclass
class MessageStart:
    message: AgentMessage
    type: str = "message_start"


@dataclass
class MessageUpdate:
    message: AgentMessage
    assistant_event: AssistantMessageEvent
    type: str = "message_update"


@dataclass
class MessageEnd:
    message: AgentMessage
    type: str = "message_end"


@dataclass
class ToolExecutionStart:
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    type: str = "tool_execution_start"


@dataclass
class ToolExecutionUpdate:
    tool_call_id: str
    tool_name: str
    partial_result: Any
    type: str = "tool_execution_update"


@dataclass
class ToolExecutionEnd:
    tool_call_id: str
    tool_name: str
    result: Any
    is_error: bool = False
    type: str = "tool_execution_end"


AgentEvent = Union[
    AgentStart, AgentEnd, TurnStart, TurnEnd, MessageStart, MessageUpdate,
    MessageEnd, ToolExecutionStart, ToolExecutionUpdate, ToolExecutionEnd,
]
```

- [ ] **Step 5: 导出**

`agent/__init__.py` 追加全部事件类型 + `RunResult` + `StopReason`（注意：导出名 `StopReason` 会与 `ai.StopReason` 冲突——agent 包导出 run 级；消费方若同时需要两者，用 `import nanoagent.ai as ai` 限定）。

- [ ] **Step 6: 跑测试 + 契约 + 提交**

Run: `pytest tests/agent/test_result.py tests/test_import_contract.py -v`
Expected: PASS

```bash
git add src/nanoagent/agent tests/agent/test_result.py
git commit -m "feat(agent): add AgentEvent vocabulary and RunResult terminal contract"
```

---

### Task 10: agent — AgentTool + 并发执行器

**Files:**
- Create: `src/nanoagent/agent/tools.py`
- Modify: `src/nanoagent/agent/__init__.py`
- Test: `tests/agent/test_tools.py`

**Interfaces:**
- Consumes: `ai.Tool`, `ai.ToolCall`, `ai.ToolResultMessage`, `ai.TextContent`, `pydantic.BaseModel`。
- Produces:
  - `AgentToolResult(content: list[TextContent | ImageContent], is_error=False, details=None)`。
  - `AgentTool`（抽象基类）：属性 `name`, `description`, `parameters: type[BaseModel]`, `label`, `concurrency: Literal["shared","exclusive"]="shared"`；方法 `async execute(self, tool_call_id, params, signal=None) -> AgentToolResult`；`to_wire() -> Tool`（`parameters` 取 `self.parameters.model_json_schema()`）。
  - `async def execute_tool_calls(tool_calls: list[ToolCall], tools: list[AgentTool], *, signal=None, before_tool_call=None) -> list[ToolResultMessage]`：按名查工具→Pydantic 校验 args→并发执行（`shared` 并行、`exclusive` 串行）→失败/校验错→`ToolResultMessage(is_error=True)`（永不抛）。

- [ ] **Step 1: 写失败测试**

`tests/agent/test_tools.py`:

```python
import pytest
from pydantic import BaseModel

from nanoagent.ai import TextContent, ToolCall
from nanoagent.agent.tools import AgentTool, AgentToolResult, execute_tool_calls


class EchoArgs(BaseModel):
    text: str


class EchoTool(AgentTool):
    name = "echo"
    description = "echo back"
    parameters = EchoArgs
    label = "Echo"

    async def execute(self, tool_call_id, params, signal=None):
        return AgentToolResult(content=[TextContent(text=params.text)])


@pytest.mark.asyncio
async def test_executes_and_returns_result():
    calls = [ToolCall(id="t1", name="echo", arguments={"text": "hi"})]
    results = await execute_tool_calls(calls, [EchoTool()])
    assert results[0].content[0].text == "hi" and results[0].is_error is False

@pytest.mark.asyncio
async def test_unknown_tool_is_error_not_raise():
    results = await execute_tool_calls([ToolCall(id="t1", name="nope", arguments={})], [EchoTool()])
    assert results[0].is_error is True

@pytest.mark.asyncio
async def test_validation_error_is_error_not_raise():
    results = await execute_tool_calls([ToolCall(id="t1", name="echo", arguments={})], [EchoTool()])
    assert results[0].is_error is True

def test_to_wire_emits_json_schema():
    wire = EchoTool().to_wire()
    assert wire.name == "echo" and wire.parameters["properties"]["text"]["type"] == "string"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/agent/test_tools.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: 实现 tools.py**

```python
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ValidationError

from nanoagent.ai import ImageContent, TextContent, Tool, ToolCall, ToolResultMessage


@dataclass
class AgentToolResult:
    content: list[TextContent | ImageContent] = field(default_factory=list)
    is_error: bool = False
    details: Any = None


class AgentTool(ABC):
    name: str
    description: str
    parameters: type[BaseModel]
    label: str = ""
    concurrency: Literal["shared", "exclusive"] = "shared"

    def to_wire(self) -> Tool:
        return Tool(name=self.name, description=self.description,
                    parameters=self.parameters.model_json_schema())

    @abstractmethod
    async def execute(self, tool_call_id: str, params: BaseModel,
                      signal: Any = None) -> AgentToolResult: ...


def _error_result(call: ToolCall, text: str) -> ToolResultMessage:
    return ToolResultMessage(tool_call_id=call.id, tool_name=call.name,
                             content=[TextContent(text=text)], is_error=True)


async def _run_one(call: ToolCall, tool: AgentTool, signal: Any,
                   before_tool_call) -> ToolResultMessage:
    try:
        params = tool.parameters.model_validate(call.arguments)
    except ValidationError as e:
        return _error_result(call, f"Invalid arguments: {e}")
    if before_tool_call is not None:
        decision = await before_tool_call(call, params)
        if decision is not None and decision.get("block"):
            return _error_result(call, decision.get("reason") or "Tool blocked")
    try:
        result = await tool.execute(call.id, params, signal)
    except Exception as e:  # 工具异常永不传播：编码为 is_error
        return _error_result(call, f"Tool failed: {e}")
    return ToolResultMessage(tool_call_id=call.id, tool_name=call.name,
                             content=result.content, is_error=result.is_error)


async def execute_tool_calls(
    tool_calls: list[ToolCall], tools: list[AgentTool], *,
    signal: Any = None,
    before_tool_call: Callable[[ToolCall, BaseModel], Awaitable[dict | None]] | None = None,
) -> list[ToolResultMessage]:
    by_name = {t.name: t for t in tools}
    results: list[ToolResultMessage | None] = [None] * len(tool_calls)
    shared: list[asyncio.Task] = []
    for i, call in enumerate(tool_calls):
        tool = by_name.get(call.name)
        if tool is None:
            results[i] = _error_result(call, f"Unknown tool: {call.name}")
            continue
        if tool.concurrency == "exclusive":
            if shared:
                done = await asyncio.gather(*shared)
                for idx, r in done:
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

- [ ] **Step 4: 导出**

`agent/__init__.py` 追加：`AgentTool`, `AgentToolResult`, `execute_tool_calls`。

- [ ] **Step 5: 跑测试 + 契约 + 提交**

Run: `pytest tests/agent/test_tools.py tests/test_import_contract.py -v`
Expected: PASS

```bash
git add src/nanoagent/agent tests/agent/test_tools.py
git commit -m "feat(agent): add AgentTool, AgentToolResult, concurrent executor"
```

---

### Task 11: agent — 控制面（AbortSignal + ControlSource + steering）

**Files:**
- Create: `src/nanoagent/agent/control.py`
- Modify: `src/nanoagent/agent/__init__.py`
- Test: `tests/agent/test_control.py`

**Interfaces:**
- Consumes: `ai.ToolCall`, `asyncio`。
- Produces:
  - `AbortSignal`：`.abort(reason=None)`、`.aborted: bool`、`.reason`、`async wait()`。鸭子兼容 `ai.StreamOptions.signal`（只暴露 `.aborted`）。
  - `ControlSource`（协议）：`async request_approval(tool_call: ToolCall, tier: str) -> bool`。
  - 默认实现 `AllowAll`（`request_approval` 恒 `True`）——框架给"无审批"默认，审批策略是 harness。

- [ ] **Step 1: 写失败测试**

`tests/agent/test_control.py`:

```python
import pytest

from nanoagent.ai import ToolCall
from nanoagent.agent.control import AbortSignal, AllowAll


def test_abort_signal_flips():
    sig = AbortSignal()
    assert sig.aborted is False
    sig.abort("user")
    assert sig.aborted is True and sig.reason == "user"

@pytest.mark.asyncio
async def test_allow_all_approves():
    src = AllowAll()
    assert await src.request_approval(ToolCall(id="t", name="x", arguments={}), "exec") is True
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/agent/test_control.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: 实现 control.py**

```python
from __future__ import annotations

import asyncio
from typing import Any, Protocol

from nanoagent.ai import ToolCall


class AbortSignal:
    """协作式取消。鸭子兼容 ai.StreamOptions.signal（只读 .aborted）。"""

    def __init__(self):
        self._event = asyncio.Event()
        self.reason: Any = None

    @property
    def aborted(self) -> bool:
        return self._event.is_set()

    def abort(self, reason: Any = None) -> None:
        self.reason = reason
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()


class ControlSource(Protocol):
    async def request_approval(self, tool_call: ToolCall, tier: str) -> bool: ...


class AllowAll:
    """框架默认：不审批（机制给旋钮，审批策略归 harness）。"""

    async def request_approval(self, tool_call: ToolCall, tier: str) -> bool:
        return True
```

- [ ] **Step 4: 导出**

`agent/__init__.py` 追加：`AbortSignal`, `ControlSource`, `AllowAll`。

- [ ] **Step 5: 跑测试 + 提交**

Run: `pytest tests/agent/test_control.py tests/test_import_contract.py -v`
Expected: PASS

```bash
git add src/nanoagent/agent tests/agent/test_control.py
git commit -m "feat(agent): add AbortSignal, ControlSource protocol, AllowAll default"
```

---

### Task 12: agent — context 装配 + transform 接缝 stub

**Files:**
- Create: `src/nanoagent/agent/context.py`
- Modify: `src/nanoagent/agent/__init__.py`
- Test: `tests/agent/test_context.py`

**Interfaces:**
- Consumes: `ai.Context`, `AgentMessage`, `ConvertToLlm`, `AgentTool`。
- Produces:
  - `async def assemble_context(system_prompt, messages, tools, convert_to_llm, transform_context=None, signal=None) -> ai.Context`：先（可选）`transform_context(messages, signal)`（AgentMessage 级），再 `convert_to_llm`（→ wire Message[]），再把 `AgentTool.to_wire()` 填入 `Context.tools`。
  - `transform_context` 默认 = `None`（no-op）；这是最小压缩 stub 的接缝（成熟压缩是后话）。

- [ ] **Step 1: 写失败测试**

`tests/agent/test_context.py`:

```python
import pytest
from pydantic import BaseModel

from nanoagent.ai import UserMessage
from nanoagent.agent.context import assemble_context
from nanoagent.agent.messages import default_convert_to_llm
from nanoagent.agent.tools import AgentTool, AgentToolResult


class A(BaseModel):
    pass


class T(AgentTool):
    name = "t"; description = "d"; parameters = A; label = "T"
    async def execute(self, tool_call_id, params, signal=None):
        return AgentToolResult()


@pytest.mark.asyncio
async def test_assemble_builds_wire_context():
    ctx = await assemble_context(
        system_prompt=["sys"], messages=[UserMessage(content="hi")],
        tools=[T()], convert_to_llm=default_convert_to_llm)
    assert ctx.system_prompt == ["sys"]
    assert ctx.messages[0].role == "user"
    assert ctx.tools[0].name == "t"

@pytest.mark.asyncio
async def test_transform_context_runs_first():
    async def drop_all(messages, signal=None):
        return []
    ctx = await assemble_context(
        system_prompt=[], messages=[UserMessage(content="hi")], tools=[],
        convert_to_llm=default_convert_to_llm, transform_context=drop_all)
    assert ctx.messages == []
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/agent/test_context.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: 实现 context.py**

```python
from __future__ import annotations

from typing import Any, Awaitable, Callable

from nanoagent.ai import Context
from nanoagent.agent.messages import AgentMessage, ConvertToLlm
from nanoagent.agent.tools import AgentTool

TransformContext = Callable[[list[AgentMessage], Any], Awaitable[list[AgentMessage]]]


async def assemble_context(
    system_prompt: list[str],
    messages: list[AgentMessage],
    tools: list[AgentTool],
    convert_to_llm: ConvertToLlm,
    transform_context: TransformContext | None = None,
    signal: Any = None,
) -> Context:
    """装配 wire Context。transform_context = 压缩/裁剪接缝（默认 no-op stub）。"""
    msgs = messages
    if transform_context is not None:
        msgs = await transform_context(messages, signal)
    wire = convert_to_llm(msgs)
    return Context(
        system_prompt=list(system_prompt),
        messages=wire,
        tools=[t.to_wire() for t in tools],
    )
```

- [ ] **Step 4: 导出**

`agent/__init__.py` 追加：`assemble_context`, `TransformContext`。

- [ ] **Step 5: 跑测试 + 提交**

Run: `pytest tests/agent/test_context.py tests/test_import_contract.py -v`
Expected: PASS

```bash
git add src/nanoagent/agent tests/agent/test_context.py
git commit -m "feat(agent): add context assembly with transform_context seam"
```

---

### Task 13: agent — agent_loop 最小（纯文本一轮，COMPLETED）

**Files:**
- Create: `src/nanoagent/agent/loop.py`
- Modify: `src/nanoagent/agent/__init__.py`
- Test: `tests/agent/test_loop_text.py`

**Interfaces:**
- Consumes: `ai.stream`, `ai.accumulate`, `ai.AssistantMessage`, `ai.StopReason`(wire), `assemble_context`, `AbortSignal`, `AgentEvent` 全套, `RunResult`, `StopReason`(run), `ConvertToLlm`, `default_convert_to_llm`, `AgentTool`。
- Produces:
  - `AgentLoopConfig`（dataclass）：`model: Model`、`convert_to_llm=default_convert_to_llm`、`transform_context=None`、`max_turns=10`、`control=None`、`before_tool_call=None`、`get_steering_messages=None`、`stream_fn=None`（默认 `ai.stream`）、采样 `temperature/max_tokens/reasoning=None`。
  - `async def agent_loop(prompts, system_prompt, messages, tools, config, signal=None) -> AsyncIterator[AgentEvent]`：本任务只实现"装配→流式→累积 assistant→无工具→`agent_end(COMPLETED)`"。产出 `agent_start`/`turn_start`/`message_start`(prompt)/`message_end`/`message_start`(assistant)/`message_update`(每事件)/`message_end`/`turn_end`/`agent_end`。

- [ ] **Step 1: 写失败测试**

`tests/agent/test_loop_text.py`:

```python
import pytest

from nanoagent.ai import UserMessage
from nanoagent.ai.provider import clear_providers
from nanoagent.ai.providers.mock import create_mock_model, register_mock
from nanoagent.agent.loop import AgentLoopConfig, agent_loop
from nanoagent.agent.result import StopReason


@pytest.mark.asyncio
async def test_text_only_turn_completes():
    clear_providers(); register_mock()
    mock = create_mock_model(responses=[{"content": ["hi there"]}])
    cfg = AgentLoopConfig(model=mock)
    events = [e async for e in agent_loop(
        prompts=[UserMessage(content="hello")], system_prompt=["sys"],
        messages=[], tools=[], config=cfg)]
    assert events[0].type == "agent_start"
    end = events[-1]
    assert end.type == "agent_end"
    assert end.result.reason is StopReason.COMPLETED
    assert end.messages[-1].role == "assistant"
    assert end.messages[-1].content[0].text == "hi there"
    assert end.result.final_message_id == end.messages[-1].id
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/agent/test_loop_text.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: 实现 loop.py（最小骨架）**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

from nanoagent.ai import Model, StreamAccumulator, accumulate, stream
from nanoagent.ai import StopReason as WireStopReason
from nanoagent.agent.context import TransformContext, assemble_context
from nanoagent.agent.control import ControlSource
from nanoagent.agent.events import (
    AgentEnd, AgentEvent, AgentStart, MessageEnd, MessageStart, MessageUpdate,
    TurnEnd, TurnStart,
)
from nanoagent.agent.messages import AgentMessage, ConvertToLlm, default_convert_to_llm
from nanoagent.agent.result import RunResult, StopReason
from nanoagent.agent.tools import AgentTool


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


def _options(config: AgentLoopConfig, signal: Any):
    from nanoagent.ai import StreamOptions
    return StreamOptions(signal=signal, temperature=config.temperature,
                         max_tokens=config.max_tokens, reasoning=config.reasoning)


async def agent_loop(
    *, prompts: list[AgentMessage], system_prompt: list[str],
    messages: list[AgentMessage], tools: list[AgentTool],
    config: AgentLoopConfig, signal: Any = None,
) -> AsyncIterator[AgentEvent]:
    history: list[AgentMessage] = [*messages, *prompts]
    produced: list[AgentMessage] = [*prompts]
    stream_fn = config.stream_fn or stream

    yield AgentStart()
    yield TurnStart()
    for p in prompts:
        yield MessageStart(message=p)
        yield MessageEnd(message=p)

    ctx = await assemble_context(system_prompt, history, tools,
                                 config.convert_to_llm, config.transform_context, signal)
    acc = StreamAccumulator(model_id=config.model.id, provider=config.model.provider,
                            api=config.model.api)
    assistant = None
    async for event in stream_fn(config.model, ctx, _options(config, signal)):
        acc.add(event)
        if event.type == "start":
            yield MessageStart(message=acc.message)
        elif event.type in ("done", "error"):
            assistant = event.message
        else:
            yield MessageUpdate(message=acc.message, assistant_event=event)
    assert assistant is not None
    history.append(assistant)
    produced.append(assistant)
    yield MessageEnd(message=assistant)
    yield TurnEnd(message=assistant, tool_results=[])

    result = RunResult(reason=StopReason.COMPLETED, final_message_id=assistant.id)
    yield AgentEnd(messages=produced, result=result)
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/agent/test_loop_text.py -v`
Expected: PASS

- [ ] **Step 5: 导出 + 契约 + 提交**

`agent/__init__.py` 追加：`AgentLoopConfig`, `agent_loop`。

Run: `pytest tests/agent/ tests/test_import_contract.py -v`
Expected: PASS

```bash
git add src/nanoagent/agent tests/agent/test_loop_text.py
git commit -m "feat(agent): add minimal agent_loop (text-only turn, COMPLETED)"
```

---

### Task 14: agent — agent_loop + 工具调用（执行→回填→续轮）

**Files:**
- Modify: `src/nanoagent/agent/loop.py`
- Test: `tests/agent/test_loop_tools.py`

**Interfaces:**
- Consumes（新增）：`execute_tool_calls`, `ai.ToolCall`, `ToolExecutionStart/End`。
- Produces: `agent_loop` 升级为多轮——把单轮流式抽成内层，外面包 `while turn < max_turns`：assistant 有 `tool_use` 且含 toolCall → 发 `tool_execution_start` → `execute_tool_calls`（传 `before_tool_call`）→ 每个 `ToolResultMessage` 发 `message_start/end` 并回填 history → `tool_execution_end` → `turn_end(tool_results)` → 续轮；无工具 → `COMPLETED`。

- [ ] **Step 1: 写失败测试**

`tests/agent/test_loop_tools.py`:

```python
import pytest
from pydantic import BaseModel

from nanoagent.ai import UserMessage
from nanoagent.ai.provider import clear_providers
from nanoagent.ai.providers.mock import create_mock_model, register_mock
from nanoagent.agent.loop import AgentLoopConfig, agent_loop
from nanoagent.agent.result import StopReason
from nanoagent.agent.tools import AgentTool, AgentToolResult
from nanoagent.ai import TextContent


class EchoArgs(BaseModel):
    text: str


class EchoTool(AgentTool):
    name = "echo"; description = "echo"; parameters = EchoArgs; label = "Echo"
    async def execute(self, tool_call_id, params, signal=None):
        return AgentToolResult(content=[TextContent(text=f"echo:{params.text}")])


@pytest.mark.asyncio
async def test_tool_call_then_continue_completes():
    clear_providers(); register_mock()
    mock = create_mock_model(responses=[
        {"content": [{"type": "toolCall", "name": "echo", "arguments": {"text": "yo"}}]},
        {"content": ["done"]},
    ])
    cfg = AgentLoopConfig(model=mock)
    events = [e async for e in agent_loop(
        prompts=[UserMessage(content="go")], system_prompt=[], messages=[],
        tools=[EchoTool()], config=cfg)]
    types = [e.type for e in events]
    assert "tool_execution_start" in types and "tool_execution_end" in types
    end = events[-1]
    assert end.result.reason is StopReason.COMPLETED
    roles = [m.role for m in end.messages]
    assert roles == ["user", "assistant", "toolResult", "assistant"]
    assert end.messages[2].content[0].text == "echo:yo"
    assert mock.calls and len(mock.calls) == 2  # 两轮模型调用
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/agent/test_loop_tools.py -v`
Expected: FAIL（当前 loop 不执行工具，roles 不匹配）

- [ ] **Step 3: 重写 loop.py 的 agent_loop（提取单轮 + 外层多轮）**

替换 Task 13 的 `agent_loop` 函数体为：

```python
async def _stream_one_turn(model, ctx, stream_fn, options):
    acc = StreamAccumulator(model_id=model.id, provider=model.provider, api=model.api)
    assistant = None
    started = False
    async for event in stream_fn(model, ctx, options):
        acc.add(event)
        if event.type == "start":
            started = True
            yield ("message_start", acc.message)
        elif event.type in ("done", "error"):
            assistant = event.message
        else:
            yield ("message_update", acc.message, event)
    yield ("__assistant__", assistant)


async def agent_loop(
    *, prompts: list[AgentMessage], system_prompt: list[str],
    messages: list[AgentMessage], tools: list[AgentTool],
    config: AgentLoopConfig, signal: Any = None,
) -> AsyncIterator[AgentEvent]:
    from nanoagent.agent.events import ToolExecutionEnd, ToolExecutionStart
    from nanoagent.agent.tools import execute_tool_calls

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
            yield AgentEnd(messages=produced,
                           result=RunResult(reason=StopReason.ABORTED, final_message_id=last_id))
            return
        if turn >= config.max_turns:
            last_id = produced[-1].id if produced else None
            yield AgentEnd(messages=produced,
                           result=RunResult(reason=StopReason.MAX_TURNS, final_message_id=last_id))
            return
        turn += 1
        yield TurnStart()

        ctx = await assemble_context(system_prompt, history, tools,
                                     config.convert_to_llm, config.transform_context, signal)
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
            reason = (StopReason.ABORTED if assistant.stop_reason == WireStopReason.ABORTED
                      else StopReason.ERROR)
            yield AgentEnd(messages=produced,
                           result=RunResult(reason=reason, final_message_id=assistant.id,
                                            error=assistant.error_message))
            return

        tool_calls = [c for c in assistant.content if getattr(c, "type", None) == "toolCall"]
        runnable = assistant.stop_reason in (WireStopReason.TOOL_USE, WireStopReason.STOP)
        if not (runnable and tool_calls):
            yield TurnEnd(message=assistant, tool_results=[])
            yield AgentEnd(messages=produced,
                           result=RunResult(reason=StopReason.COMPLETED, final_message_id=assistant.id))
            return

        for c in tool_calls:
            yield ToolExecutionStart(tool_call_id=c.id, tool_name=c.name, args=c.arguments)
        tool_results = await execute_tool_calls(
            tool_calls, tools, signal=signal, before_tool_call=config.before_tool_call)
        for c, r in zip(tool_calls, tool_results):
            yield ToolExecutionEnd(tool_call_id=c.id, tool_name=c.name,
                                   result=r, is_error=r.is_error)
        for r in tool_results:
            yield MessageStart(message=r)
            history.append(r)
            produced.append(r)
            yield MessageEnd(message=r)
        yield TurnEnd(message=assistant, tool_results=tool_results)
```

> 注：删除 Task 13 末尾的单轮收尾代码（已被多轮逻辑取代）。`approval` 接缝在 Task 16 接入 `control.request_approval`。

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/agent/test_loop_tools.py tests/agent/test_loop_text.py -v`
Expected: PASS（两个 loop 测试都过）

- [ ] **Step 5: 契约 + 提交**

Run: `pytest tests/ tests/test_import_contract.py -v`
Expected: PASS

```bash
git add src/nanoagent/agent/loop.py tests/agent/test_loop_tools.py
git commit -m "feat(agent): agent_loop runs tools, backfills results, continues turns"
```

---

### Task 15: agent — 终止路径 + 审批接缝（MAX_TURNS / ABORTED / ERROR / approval）

**Files:**
- Modify: `src/nanoagent/agent/loop.py`
- Test: `tests/agent/test_loop_terminal.py`

**Interfaces:**
- Consumes: `control.AllowAll`, `control.AbortSignal`。
- Produces: 在 Task 14 多轮骨架上接入审批——工具执行前，若 `config.control` 存在，对每个 tool_call 调 `request_approval(call, tier="exec")`；拒绝 → 该 call 直接产出 `ToolResultMessage(is_error=True, "approval denied")`，不执行。覆盖测试：MAX_TURNS、ABORTED（执行中 abort）、wire ERROR → run ERROR、approval 拒绝。

- [ ] **Step 1: 写失败测试**

`tests/agent/test_loop_terminal.py`:

```python
import pytest
from pydantic import BaseModel

from nanoagent.ai import TextContent, UserMessage
from nanoagent.ai.provider import clear_providers
from nanoagent.ai.providers.mock import create_mock_model, register_mock
from nanoagent.agent.control import AbortSignal
from nanoagent.agent.loop import AgentLoopConfig, agent_loop
from nanoagent.agent.result import StopReason
from nanoagent.agent.tools import AgentTool, AgentToolResult


class Args(BaseModel):
    pass


class Tool1(AgentTool):
    name = "t"; description = "d"; parameters = Args; label = "T"
    async def execute(self, tool_call_id, params, signal=None):
        return AgentToolResult(content=[TextContent(text="ran")])


class DenyAll:
    async def request_approval(self, tool_call, tier):
        return False


@pytest.mark.asyncio
async def test_max_turns_terminates():
    clear_providers(); register_mock()
    # 永远要求工具 → 不会自然 COMPLETED
    mock = create_mock_model(handler=lambda ctx: {
        "content": [{"type": "toolCall", "name": "t", "arguments": {}}]})
    cfg = AgentLoopConfig(model=mock, max_turns=3)
    events = [e async for e in agent_loop(prompts=[UserMessage(content="go")],
              system_prompt=[], messages=[], tools=[Tool1()], config=cfg)]
    assert events[-1].result.reason is StopReason.MAX_TURNS

@pytest.mark.asyncio
async def test_wire_error_maps_to_run_error():
    clear_providers(); register_mock()
    mock = create_mock_model(responses=[{"content": [], "error": "boom"}])
    cfg = AgentLoopConfig(model=mock)
    events = [e async for e in agent_loop(prompts=[UserMessage(content="go")],
              system_prompt=[], messages=[], tools=[], config=cfg)]
    end = events[-1]
    assert end.result.reason is StopReason.ERROR and end.result.error == "boom"

@pytest.mark.asyncio
async def test_approval_denied_blocks_tool():
    clear_providers(); register_mock()
    mock = create_mock_model(responses=[
        {"content": [{"type": "toolCall", "name": "t", "arguments": {}}]},
        {"content": ["after"]},
    ])
    cfg = AgentLoopConfig(model=mock, control=DenyAll())
    events = [e async for e in agent_loop(prompts=[UserMessage(content="go")],
              system_prompt=[], messages=[], tools=[Tool1()], config=cfg)]
    tool_results = [m for m in events[-1].messages if m.role == "toolResult"]
    assert tool_results[0].is_error is True
    assert "approval" in tool_results[0].content[0].text.lower()
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/agent/test_loop_terminal.py -v`
Expected: FAIL（MAX_TURNS 已通；approval 拒绝路径未实现 → 失败）

- [ ] **Step 3: 在 loop.py 接入审批接缝**

把 Task 14 中"`tool_results = await execute_tool_calls(...)`"一段替换为先审批、再执行：

```python
        from nanoagent.ai import ToolResultMessage
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
                    tool_call_id=c.id, tool_name=c.name,
                    content=[TextContent(text="Tool approval denied")], is_error=True)

        executed = await execute_tool_calls(
            approved, tools, signal=signal, before_tool_call=config.before_tool_call)
        executed_by_id = {r.tool_call_id: r for r in executed}
        tool_results = [denied_results.get(c.id) or executed_by_id[c.id] for c in tool_calls]
```

在 `loop.py` 顶部 import 处补 `from nanoagent.ai import TextContent`。

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/agent/test_loop_terminal.py -v`
Expected: PASS（MAX_TURNS / wire-error→run-error / approval 拒绝 全过）

- [ ] **Step 5: 全量回归 + 契约 + 提交**

Run: `pytest tests/ tests/test_import_contract.py -v`
Expected: PASS

```bash
git add src/nanoagent/agent/loop.py tests/agent/test_loop_terminal.py
git commit -m "feat(agent): add terminal paths and approval seam to agent_loop"
```

---

### Task 16: agent — 有状态 Agent 类 + REPL 冒烟

**Files:**
- Create: `src/nanoagent/agent/agent.py`
- Modify: `src/nanoagent/agent/__init__.py`
- Test: `tests/agent/test_agent.py`

**Interfaces:**
- Consumes: `agent_loop`, `AgentLoopConfig`, `AgentEvent`, `RunResult`, `AbortSignal`, `AgentMessage`, `ai.UserMessage`, `AgentTool`, `Model`。
- Produces:
  - `AgentState(system_prompt, model, tools, messages, is_streaming=False)`。
  - `Agent`：
    - `__init__(model, *, system_prompt=None, tools=None, convert_to_llm=None, max_turns=10, control=None, stream_fn=None)`。
    - `subscribe(fn) -> unsubscribe`、`set_model/set_tools/set_system_prompt`、`abort(reason=None)`、`steer(m)`、`state`。
    - `async prompt(input: str | AgentMessage | list[AgentMessage]) -> RunResult`：构造 user 消息→驱动 `agent_loop`→把事件喂监听器→把 `produced` 追加进 `state.messages`→返回 `AgentEnd.result`。
    - steering：维护队列，`get_steering_messages` 从队列取（在轮边界注入——本最小版仅在轮开始注入，复用 loop 现有续轮）。

- [ ] **Step 1: 写失败测试（REPL 冒烟：两次 prompt 累积上下文）**

`tests/agent/test_agent.py`:

```python
import pytest

from nanoagent.ai.provider import clear_providers
from nanoagent.ai.providers.mock import create_mock_model, register_mock
from nanoagent.agent.agent import Agent
from nanoagent.agent.result import StopReason


@pytest.mark.asyncio
async def test_prompt_returns_result_and_accumulates_history():
    clear_providers(); register_mock()
    mock = create_mock_model(responses=[{"content": ["hi"]}, {"content": ["again"]}])
    agent = Agent(model=mock, system_prompt=["sys"])
    seen = []
    agent.subscribe(lambda e: seen.append(e.type))

    r1 = await agent.prompt("hello")
    assert r1.reason is StopReason.COMPLETED
    assert "agent_start" in seen and "agent_end" in seen

    r2 = await agent.prompt("more")
    assert r2.reason is StopReason.COMPLETED
    roles = [m.role for m in agent.state.messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert len(mock.calls) == 2
    # 第二次调用的上下文带上了前一轮历史
    assert len(mock.calls[1].messages) >= 3
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/agent/test_agent.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: 实现 agent.py**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from nanoagent.ai import Model, UserMessage
from nanoagent.agent.control import AbortSignal, ControlSource
from nanoagent.agent.events import AgentEnd, AgentEvent
from nanoagent.agent.loop import AgentLoopConfig, agent_loop
from nanoagent.agent.messages import AgentMessage, ConvertToLlm, default_convert_to_llm
from nanoagent.agent.result import RunResult
from nanoagent.agent.tools import AgentTool


@dataclass
class AgentState:
    system_prompt: list[str]
    model: Model
    tools: list[AgentTool] = field(default_factory=list)
    messages: list[AgentMessage] = field(default_factory=list)
    is_streaming: bool = False


class AgentBusyError(RuntimeError):
    pass


class Agent:
    def __init__(self, model: Model, *, system_prompt: list[str] | None = None,
                 tools: list[AgentTool] | None = None,
                 convert_to_llm: ConvertToLlm | None = None, max_turns: int = 10,
                 control: ControlSource | None = None,
                 stream_fn: Callable[..., Any] | None = None):
        self.state = AgentState(system_prompt=list(system_prompt or []), model=model,
                                tools=list(tools or []))
        self._convert_to_llm = convert_to_llm or default_convert_to_llm
        self._max_turns = max_turns
        self._control = control
        self._stream_fn = stream_fn
        self._listeners: set[Callable[[AgentEvent], None]] = set()
        self._signal: AbortSignal | None = None
        self._steering: list[AgentMessage] = []

    def subscribe(self, fn: Callable[[AgentEvent], None]) -> Callable[[], None]:
        self._listeners.add(fn)
        return lambda: self._listeners.discard(fn)

    def _emit(self, event: AgentEvent) -> None:
        for fn in list(self._listeners):
            fn(event)

    def set_model(self, m: Model) -> None: self.state.model = m
    def set_tools(self, t: list[AgentTool]) -> None: self.state.tools = list(t)
    def set_system_prompt(self, s: list[str]) -> None: self.state.system_prompt = list(s)

    def abort(self, reason: Any = None) -> None:
        if self._signal is not None:
            self._signal.abort(reason)

    def steer(self, m: AgentMessage) -> None:
        self._steering.append(m)

    async def _get_steering(self) -> list[AgentMessage]:
        out, self._steering = self._steering, []
        return out

    async def prompt(self, input: str | AgentMessage | list[AgentMessage]) -> RunResult:
        if self.state.is_streaming:
            raise AgentBusyError("agent is already processing")
        if isinstance(input, str):
            prompts: list[AgentMessage] = [UserMessage(content=input)]
        elif isinstance(input, list):
            prompts = input
        else:
            prompts = [input]

        cfg = AgentLoopConfig(
            model=self.state.model, convert_to_llm=self._convert_to_llm,
            max_turns=self._max_turns, control=self._control,
            get_steering_messages=self._get_steering, stream_fn=self._stream_fn)
        self._signal = AbortSignal()
        self.state.is_streaming = True
        result = RunResult(reason=__import__("nanoagent.agent.result", fromlist=["StopReason"]).StopReason.ERROR)
        try:
            async for event in agent_loop(
                prompts=prompts, system_prompt=self.state.system_prompt,
                messages=self.state.messages, tools=self.state.tools,
                config=cfg, signal=self._signal):
                self._emit(event)
                if isinstance(event, AgentEnd):
                    for m in event.messages:
                        self.state.messages.append(m)
                    result = event.result
        finally:
            self.state.is_streaming = False
            self._signal = None
        return result
```

> 注：上面 `result` 初值用动态 import 仅为避免顶部再引一个名字冲突；可直接 `from nanoagent.agent.result import RunResult, StopReason` 并写 `RunResult(reason=StopReason.ERROR)`。实现时用后者（更清晰）。

- [ ] **Step 4: 用清晰 import 重写 result 初值**

把 Step 3 里 `result = RunResult(reason=__import__(...))` 改为：顶部加 `from nanoagent.agent.result import RunResult, StopReason`，此处写 `result = RunResult(reason=StopReason.ERROR)`。

- [ ] **Step 5: 跑测试验证通过**

Run: `pytest tests/agent/test_agent.py -v`
Expected: PASS

- [ ] **Step 6: 导出 + 全量回归 + 契约 + 提交**

`agent/__init__.py` 追加：`Agent`, `AgentState`, `AgentBusyError`。

Run: `pytest tests/ tests/test_import_contract.py -v`
Expected: PASS（全绿）

```bash
git add src/nanoagent/agent tests/agent/test_agent.py
git commit -m "feat(agent): add stateful Agent class with prompt/subscribe/steer/abort"
```

---

## Self-Review

**1. Spec coverage（逐节核对）**

| spec 节 | 计划落点 |
|---|---|
| §4 包与依赖 `agent→ai→utils` | Task 1（.importlinter）+ 全程 `test_import_contract` |
| §5 ①消息模型 | Task 3（wire `ai`）+ Task 8（`AgentMessage` in `agent`）— 已按 DP1 三层修订 |
| §5 ②Provider 抽象 | Task 5–6（Model/Provider/registry/mock）+ Task 7（OpenAI） |
| §5 ③工具系统 | Task 5（wire Tool）+ Task 10（AgentTool + 并发执行器） |
| §5 ④agent 循环 | Task 13（文本）+ Task 14（工具）+ Task 15（终止/审批） |
| §5 ⑤控制面 | Task 11（AbortSignal/ControlSource）+ Task 15（接入 loop） |
| §5 ⑥终止契约 | Task 9（RunResult + run StopReason）— 已按 DP6 合成 |
| §5 events / context | Task 9（AgentEvent）/ Task 12（assemble_context + stub） |
| §6 两入口 | Task 13–15（`agent_loop`）+ Task 16（`Agent`） |
| §8 五接缝 | ①注入=AgentLoopConfig；②实现接口=AgentTool/ControlSource；③配置=max_turns；④包装=convert_to_llm/transform_context；⑤装配=Agent.__init__ |
| §10 数据流 | Task 14 多轮骨架逐条对应 |
| §11 错误/终止 | Task 7（ProviderError 编码进 StreamError）+ Task 10（工具错→is_error 不抛）+ Task 14/15（wire→run 映射，异常不逃逸） |
| §12 测试策略 | mock 驱动、自底向上、每终止路径+每接缝有测；import-linter 入测 |
| §13 开发顺序 | Task 1→16 与 spec 8 步一一对应（细分） |
| §15 已定决策 | nanoagent 包名/打包 B/Python/OpenAI-compatible+mock — 全部落实 |

**2. Placeholder scan**：无 "TODO/TBD/类似上文"；每个改代码的 step 都给了完整代码与确切命令。Task 13→14 有一次"重写函数体"，已显式标注删除旧收尾段，非占位。

**3. Type consistency（跨任务签名核对）**：
- `convert_to_llm` 签名 `list[AgentMessage] -> list[Message]`：Task 8 定义、Task 12/13 消费一致。
- `AgentTool.to_wire() -> ai.Tool`、`execute(tool_call_id, params, signal) -> AgentToolResult`：Task 10 定义、Task 12/14 消费一致。
- `execute_tool_calls(tool_calls, tools, *, signal, before_tool_call) -> list[ToolResultMessage]`：Task 10 定义、Task 14/15 消费一致。
- 两级 `StopReason`：wire（Task 3）vs run（Task 9）始终分包引用，loop（Task 14）显式 `WireStopReason` 别名映射到 run，无混用。
- `RunResult(reason, final_message_id, error, detail)`：Task 9 定义、Task 13/14/15/16 构造字段一致。
- `AgentEnd(messages, result)`：Task 9 定义、Task 13–16 一致。
- mock 响应形状 `{"content": [...], "stop_reason"?, "error"?}`：Task 6 定义、Task 13–16 测试一致。

无发现不一致。

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-18-nanoagent-framework.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — 每个 Task 派一个全新 subagent，任务间复审，迭代快。

**2. Inline Execution** — 在本会话内按 executing-plans 批执行，带 checkpoint 复审。

**Which approach?**
