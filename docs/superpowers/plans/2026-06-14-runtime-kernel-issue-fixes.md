# Runtime Kernel Issue-Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the kernel-side fixes for Issues #3, #4, #5 from `docs/superpowers/specs/2026-06-14-runtime-kernel-issue-fixes-design.md`, staying inside the Runtime Kernel layer.

**Architecture:** #4 deletes orphan `core` types so a single message vocabulary remains (ADR-013). #5 introduces an injected `ExecutionEnv` capability port so all file/shell side effects go through one chokepoint with workspace containment and subprocess kill-on-cancel (ADR-014). #3 is already fixed and already locked by existing tests, so it is verify-only.

**Tech Stack:** Python ≥3.10, `pytest` + `pytest-asyncio` (`asyncio_mode=auto`), `asyncio.create_subprocess_shell`.

> **Commit policy for this plan:** The working tree already contains pre-existing uncommitted work (runtime hardening + docs reshuffle) that is **not** authored here. To avoid entangling it, **do not `git commit` during execution.** Implement and verify in the working tree; at the end, present the diff of plan-authored files and let the user choose the commit strategy. Each task therefore ends with a *test/verify* step, not a commit.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `core/message.py`, `core/event.py`, `core/agent.py` | orphan duplicate types, zero main-chain consumers | **delete** |
| `core/__init__.py` | export only the genuinely-shared tool protocol | modify |
| `runtime/execution_env.py` | `ExecutionEnv` port + `LocalExecutionEnv` (containment + kill-on-cancel) | **create** |
| `tools/builtin.py` | builtin tools route side effects through the injected port | modify |
| `cli/repl.py` | bind `cwd` as workspace root, inject env into tools | modify (2 lines) |
| `tests/unit/test_execution_env.py` | env contract tests | **create** |
| `tests/helpers.py` | add `FakeExecutionEnv` test double | modify |
| `tests/unit/test_builtin_tools.py` | add env-routing tests | modify |

---

## Task 1: Delete orphan `core` types (Issue #4, ADR-013)

**Files:**
- Delete: `core/message.py`, `core/event.py`, `core/agent.py`
- Modify: `core/__init__.py`

- [ ] **Step 1: Confirm zero consumers (safety net before deleting)**

Run:
```bash
cd "D:/harness agent/Mono" && grep -rn "core\.message\|core\.event\|core\.agent\|from core import\|import core\b" --include=*.py . | grep -v "core/__init__.py"
```
Expected: no output (only `core/__init__.py` references them, which we fix in Step 3).

- [ ] **Step 2: Delete the three orphan files**

Run:
```bash
cd "D:/harness agent/Mono" && git rm core/message.py core/event.py core/agent.py
```
Expected: `rm 'core/message.py'` etc. (If `git rm` warns about uncommitted-unrelated changes elsewhere, that's fine — it only removes these three.)

- [ ] **Step 3: Rewrite `core/__init__.py` to export only the tool protocol**

Replace the entire file with:
```python
"""MiniHarness 核心跨子系统类型：当前仅工具协议。

历史上这里还导出过 message/event/agent 等通用类型，但它们在主链零引用
（实际词汇表是 runtime/blocks 与 runtime/events），已按 ADR-013 删除，避免
形成第二套接近但不兼容的协议。
"""

from .tool import Tool, ToolDefinition, ToolInputSchema, ToolResult

__all__ = [
    "Tool",
    "ToolResult",
    "ToolDefinition",
    "ToolInputSchema",
]
```

- [ ] **Step 4: Verify imports + full suite still green**

Run:
```bash
cd "D:/harness agent/Mono" && python -m compileall -q core runtime providers tools cli tests && python -m pytest -q
```
Expected: compileall silent (success); pytest `67 passed` (unchanged — nothing consumed the deleted types).

---

## Task 2: `ExecutionEnv` capability port (Issue #5, ADR-014)

**Files:**
- Create: `tests/unit/test_execution_env.py`
- Create: `runtime/execution_env.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_execution_env.py`:
```python
import asyncio
import sys
import time

from runtime.execution_env import LocalExecutionEnv, ReadResult, ShellResult


async def test_read_text_returns_content(tmp_path):
    path = tmp_path / "a.txt"; path.write_text("你好world", encoding="utf-8")
    result = await LocalExecutionEnv().read_text(str(path))
    assert result.ok and result.content == "你好world"


async def test_read_text_missing_is_error_not_raise(tmp_path):
    result = await LocalExecutionEnv().read_text(str(tmp_path / "nope.txt"))
    assert result.ok is False and result.content == "" and result.error


async def test_read_text_rejects_out_of_root(tmp_path):
    root = tmp_path / "ws"; root.mkdir()
    outside = tmp_path / "secret.txt"; outside.write_text("x", encoding="utf-8")
    result = await LocalExecutionEnv(root=root).read_text(str(outside))
    assert result.ok is False and "越界" in result.error


async def test_read_text_allows_in_root(tmp_path):
    root = tmp_path / "ws"; root.mkdir()
    inside = root / "ok.txt"; inside.write_text("内部", encoding="utf-8")
    result = await LocalExecutionEnv(root=root).read_text(str(inside))
    assert result.ok and result.content == "内部"


async def test_run_shell_success():
    result = await LocalExecutionEnv().run_shell("echo mono-shell", timeout=30)
    assert result.ok and result.exit_code == 0 and "mono-shell" in result.output


async def test_run_shell_timeout_does_not_hang():
    cmd = f'"{sys.executable}" -c "import time; time.sleep(5)"'
    start = time.perf_counter()
    result = await LocalExecutionEnv().run_shell(cmd, timeout=0.3)
    elapsed = time.perf_counter() - start
    assert result.ok is False and "超时" in result.error
    assert elapsed < 3.0


async def test_terminate_kills_running_process():
    process = await asyncio.create_subprocess_shell(
        f'"{sys.executable}" -c "import time; time.sleep(30)"',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    assert process.returncode is None
    await LocalExecutionEnv._terminate(process)
    assert process.returncode is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd "D:/harness agent/Mono" && python -m pytest tests/unit/test_execution_env.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'runtime.execution_env'`.

- [ ] **Step 3: Create `runtime/execution_env.py`**

```python
"""执行能力端口：文件系统与 Shell 副作用经此注入，工具不直接触达宿主机。

设计要点（ADR-014）：
- Result 风格，绝不抛异常；调用方拿到结构化结果，循环不被打断。
- 取消与超时必须传播到底层副作用（子进程终止）。asyncio.wait_for 只取消
  awaitable，并不保证子进程被杀，因此终止逻辑必须落在本端口内。
- 工作区根 root 为可选策略：绑定后越界读取被拒绝、Shell 以 root 为工作目录；
  root=None 时不施加边界（保持运行时中立，由上层决定是否绑定）。
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ReadResult:
    ok: bool
    content: str
    error: Optional[str] = None


@dataclass(frozen=True)
class ShellResult:
    ok: bool
    exit_code: int
    output: str
    error: Optional[str] = None


class ExecutionEnv(ABC):
    """文件系统与 Shell 能力端口。实现必须 Result 化、不抛异常。"""

    @abstractmethod
    async def read_text(self, path: str) -> ReadResult:
        ...

    @abstractmethod
    async def run_shell(self, command: str, *, timeout: float) -> ShellResult:
        ...


class LocalExecutionEnv(ExecutionEnv):
    """真实本地实现。root=None 不限制；绑定 root 后强制工作区边界。"""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root).resolve() if root is not None else None

    def _within_root(self, path: str) -> bool:
        if self.root is None:
            return True
        try:
            resolved = Path(path).resolve()
        except OSError:
            return False
        return resolved == self.root or self.root in resolved.parents

    async def read_text(self, path: str) -> ReadResult:
        if not self._within_root(path):
            return ReadResult(False, "", f"路径越界，超出工作区根：{path}")
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            return ReadResult(False, "", str(error))
        return ReadResult(True, text)

    async def run_shell(self, command: str, *, timeout: float) -> ShellResult:
        cwd = str(self.root) if self.root is not None else None
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
            )
        except OSError as error:
            return ShellResult(False, -1, "", f"启动失败：{error}")
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout)
        except asyncio.TimeoutError:
            await self._terminate(process)
            return ShellResult(False, -1, "", f"命令执行超时（>{timeout}s）")
        except asyncio.CancelledError:
            # 取消同样必须落到子进程，避免遗留运行中的副作用。
            await self._terminate(process)
            raise
        text = stdout.decode("utf-8", errors="replace")
        code = process.returncode if process.returncode is not None else -1
        return ShellResult(code == 0, code, text, None if code == 0 else f"退出码 {code}")

    @staticmethod
    async def _terminate(process) -> None:
        if process.returncode is not None:
            return
        try:
            process.kill()
        except ProcessLookupError:
            return
        try:
            await process.wait()
        except Exception:
            pass
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd "D:/harness agent/Mono" && python -m pytest tests/unit/test_execution_env.py -q`
Expected: PASS (7 passed).

---

## Task 3: Route builtin tools through the port (Issue #5)

**Files:**
- Modify: `tests/helpers.py` (add `FakeExecutionEnv`)
- Modify: `tests/unit/test_builtin_tools.py` (add routing tests)
- Modify: `tools/builtin.py`

- [ ] **Step 1: Add `FakeExecutionEnv` to `tests/helpers.py`**

Add this import near the top (after the existing `from core.tool import ...`):
```python
from runtime.execution_env import ReadResult, ShellResult
```
And append this class to the file:
```python
class FakeExecutionEnv:
    """伪执行端口：记录调用并返回预置结果，无需真实文件系统或子进程。"""
    def __init__(self, read_result=None, shell_result=None):
        self.read_result = read_result or ReadResult(True, "假内容")
        self.shell_result = shell_result or ShellResult(True, 0, "假输出")
        self.reads, self.shells = [], []

    async def read_text(self, path):
        self.reads.append(path)
        return self.read_result

    async def run_shell(self, command, *, timeout):
        self.shells.append((command, timeout))
        return self.shell_result
```

- [ ] **Step 2: Add failing routing tests to `tests/unit/test_builtin_tools.py`**

Add these imports at the top:
```python
from runtime.execution_env import ReadResult, ShellResult
from tests.helpers import FakeExecutionEnv
```
And append these tests:
```python
async def test_read_file_routes_through_env():
    env = FakeExecutionEnv(read_result=ReadResult(True, "经由端口"))
    result = await ReadFileTool(env).call({"path": "anything"})
    assert result.success and result.content == "经由端口" and env.reads == ["anything"]


async def test_read_file_env_error_becomes_tool_error():
    env = FakeExecutionEnv(read_result=ReadResult(False, "", "越界"))
    result = await ReadFileTool(env).call({"path": "x"})
    assert not result.success and "越界" in result.content


async def test_run_command_routes_through_env():
    env = FakeExecutionEnv(shell_result=ShellResult(True, 0, "假输出"))
    result = await RunCommandTool(env).call({"command": "ls"})
    assert result.success and "假输出" in result.content and env.shells == [("ls", 30)]
```

- [ ] **Step 3: Run to verify the new tests fail**

Run: `cd "D:/harness agent/Mono" && python -m pytest tests/unit/test_builtin_tools.py -q`
Expected: the 3 new tests FAIL with `TypeError: ReadFileTool() takes no arguments` / `RunCommandTool() takes no arguments` (current tools have no `__init__`). Existing 5 tests still pass.

- [ ] **Step 4: Rewrite `tools/builtin.py` to take an injected env**

Replace the entire file with:
```python
"""用于 runtime 验收的最小内置工具集，完整工具层留给后续章节。

文件与 Shell 副作用一律经注入的 ExecutionEnv 端口执行，工具自身不直接触达
宿主机：这样工作区边界可强制、取消可传播，并能用伪端口完整测试工具。
"""
from __future__ import annotations

import time
from typing import Optional

from core.tool import Tool, ToolResult
from runtime.execution_env import ExecutionEnv, LocalExecutionEnv

MAX_FILE_CHARS = 20_000


class ReadFileTool(Tool):
    timeout_seconds = 10

    def __init__(self, env: Optional[ExecutionEnv] = None):
        self.env = env or LocalExecutionEnv()

    def name(self): return "read_file"
    def description(self): return "读取 UTF-8 文本文件，超长内容截断"
    def input_schema(self): return {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

    async def call(self, params):
        start = time.perf_counter()
        result = await self.env.read_text(params["path"])
        if not result.ok:
            return ToolResult(False, result.error or "读取失败", time.perf_counter() - start, "ReadError")
        text = result.content
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS] + "\n[已截断]"
        return ToolResult(True, text, time.perf_counter() - start)


class RunCommandTool(Tool):
    """执行系统 shell 命令，因此必须经过控制平面审批。"""
    timeout_seconds = 30
    requires_approval = True

    def __init__(self, env: Optional[ExecutionEnv] = None):
        self.env = env or LocalExecutionEnv()

    def name(self): return "run_command"
    def description(self): return "执行 shell 命令并返回退出码及合并输出"
    def input_schema(self): return {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}

    async def call(self, params):
        start = time.perf_counter()
        result = await self.env.run_shell(params["command"], timeout=self.timeout_seconds)
        return ToolResult(result.ok, f"[exit {result.exit_code}]\n{result.output}", time.perf_counter() - start,
                          None if result.ok else "NonZeroExit")
```

- [ ] **Step 5: Run to verify the builtin tests pass (new + existing)**

Run: `cd "D:/harness agent/Mono" && python -m pytest tests/unit/test_builtin_tools.py -q`
Expected: PASS (8 passed — 5 existing using real fs/echo via the permissive default + 3 new via the fake env).

---

## Task 4: Wire the REPL to bind a workspace root (Issue #5)

**Files:**
- Modify: `cli/repl.py`

- [ ] **Step 1: Add imports**

In `cli/repl.py`, add to the imports block:
```python
from pathlib import Path
```
and:
```python
from runtime.execution_env import LocalExecutionEnv
```

- [ ] **Step 2: Inject env into the tools (replace the registry line in `main()`)**

Replace:
```python
    registry = ToolRegistry(); registry.register(ReadFileTool()); registry.register(RunCommandTool())
```
with:
```python
    env = LocalExecutionEnv(root=Path.cwd())
    registry = ToolRegistry(); registry.register(ReadFileTool(env)); registry.register(RunCommandTool(env))
```

- [ ] **Step 3: Verify it compiles**

Run: `cd "D:/harness agent/Mono" && python -m compileall -q cli/repl.py`
Expected: silent (success). (REPL is interactive; the mock demo's `echo hello-from-mono` runs fine under `cwd` as root.)

---

## Task 5: Verify Issue #3 boundary is already locked (no code change)

**Files:** none (verification only)

- [ ] **Step 1: Confirm the existing tests enforce the no-mutation invariant**

Run: `cd "D:/harness agent/Mono" && python -m pytest tests/unit/test_context.py -q`
Expected: PASS. The invariant (ADR-004, "派生而非改写") is already locked by `test_compaction_truncates_request_but_preserves_complete_history`, `test_snip_changes_request_but_preserves_complete_history`, and `test_build_rejects_context_without_modifying_complete_history`, each asserting `state.messages` is byte-identical (`to_dict`) before and after `build()`. No new test is added — an identity-based assertion would test internals, which the dev-guide discourages in favor of observable behavior.

---

## Task 6: Full verification gate + report

- [ ] **Step 1: Run the complete gate (dev-guide §7)**

Run:
```bash
cd "D:/harness agent/Mono" && python -m pytest -q && python -m compileall -q core runtime providers tools cli tests && git diff --check
```
Expected: pytest `77 passed` (67 prior + 7 env + 3 tool routing; minus 0 — no tests removed), compileall silent, `git diff --check` silent (no whitespace errors).

- [ ] **Step 2: Show the plan-authored diff and report**

Run:
```bash
cd "D:/harness agent/Mono" && git status && git --no-pager diff -- core tools cli runtime/execution_env.py tests/helpers.py tests/unit/test_builtin_tools.py tests/unit/test_execution_env.py
```
Then report per-issue: what was changed, what was deliberately not changed (#1/#2 = harness layer; #3 already fixed), and how each was verified. Offer commit options (the working tree also holds pre-existing unrelated changes).

---

## Self-Review

**Spec coverage:** #4 → Task 1. #5 port → Task 2; tools → Task 3; REPL → Task 4. #3 → Task 5 (verify-only, justified). Verification gate → Task 6. #1/#2 explicitly out of scope (spec §0, §5). ✓

**Placeholder scan:** none — every code step shows complete file content or exact replacement. ✓

**Type consistency:** `ReadResult(ok, content, error)` / `ShellResult(ok, exit_code, output, error)` used identically across `execution_env.py`, `FakeExecutionEnv`, and tool tests. `ExecutionEnv.read_text(path)` / `run_shell(command, *, timeout)` signatures match tool call sites and the fake. `ReadFileTool(env)` / `RunCommandTool(env)` constructor matches REPL and tests. ✓

**Count check:** new tests = 7 (env) + 3 (tools) = 10 → 67 + 10 = 77 expected.
