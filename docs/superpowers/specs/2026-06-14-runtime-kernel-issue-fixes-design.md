# Runtime Kernel — Issue Fixes (Issues 3, 4, 5)

- Date: 2026-06-14
- Status: implemented and hardened
- Scope: **Runtime Kernel layer only** (per project scope split; HarnessSession and above are another owner's layer)
- Authoritative architecture: `docs/2026-06-14-initial-architecture-decisions.md` §14 (ADR-011..015). This spec is the *implementation-level* plan for the kernel-side portions of §3.2 问题三/四/五.

## 0. Scope decision

The five issues in §3.2 were verified against current code. They split by layer:

| Issue | Verdict | Disposition |
|---|---|---|
| #1 CLI assembles runtime | exists (`cli/repl.py:89-90,103,106`) | **Out of scope** — resolved by building HarnessSession (harness layer). Reported, not fixed. |
| #2 AgentLoop holds harness duties | exists (`engine.py:73-81,127-140,149-153`) | **Out of scope** — same. Reported, not fixed. |
| #3 transcript ↔ working-set boundary | headline bug already fixed (`context.py:81-93` copies, never writes back) | **Lock with a regression test** (ADR-004). Deeper "kernel owns history" restructure deferred (needs harness as consumer). |
| #4 duplicate `core` vs `runtime` types | exists — orphan files, zero main-chain consumers | **Delete orphans** (ADR-013). |
| #5 unsafe built-in tools | exists (`builtin.py:17,32`; no capability boundary; subprocess survives cancel) | **Inject `ExecutionEnv` port + safe tools + cancellation** (ADR-014). |

This spec fixes **#3 (lock), #4, #5**. Follow-up review also hardened workspace-relative path resolution,
Shell error propagation, and Windows subprocess-pipe cleanup. It explicitly does **not** build HarnessSession,
extract `run_one_turn`/`TurnSnapshot`/`RunResult`, or move history ownership out of the kernel — those are §14.8
migration steps that require the harness layer as their consumer and are out of this session's scope.

## 1. Issue 4 — delete orphan `core` types (ADR-013)

**Why:** `core/message.py`, `core/event.py`, `core/agent.py` are imported only by `core/__init__.py`. No module in `runtime/`, `providers/`, `cli/`, `tools/`, or `tests/` consumes them (`from core import` / `import core` → 0 matches). The live vocabulary is `runtime/blocks.py` (Message), `runtime/events.py`, and `AgentLoop`. Keeping the orphans risks a second, incompatible protocol.

**Changes:**
- Delete `core/message.py`, `core/event.py`, `core/agent.py`.
- Edit `core/__init__.py`: remove the `.agent` / `.event` / `.message` imports and their `__all__` entries; keep only the `.tool` exports (`Tool`, `ToolResult`, `ToolDefinition`, `ToolInputSchema`).
- **Keep `core/tool.py`** — it is the one genuinely shared cross-subsystem type (used by `runtime/executor.py`, `tools/builtin.py`, `tests/helpers.py`, `tests/unit/test_executor.py`).

**Constraint:** `import core.tool` triggers `core/__init__.py`; after deletion it must reference no deleted module, or the executor import chain breaks.

**Verify:** grep for any surviving reference to the deleted modules/symbols; `compileall`; full test run.

## 2. Issue 3 — lock the already-fixed boundary (ADR-004)

**Why:** the specific defect ("ContextAssembler replaces `state.messages`") is already gone — `build()` copies the input (`list(complete_messages)`) and only reassigns a local; `engine.py:84` never writes the derived request back. We make this an *enforced invariant* instead of an accident.

**Change:** add a regression test (in `tests/unit/test_context.py`): build a history large enough to trigger compaction, call `ContextAssembler.build(messages)`, and assert the caller's list is unmodified — same length and same element identities — while the returned `ModelRequest.messages` is a distinct, compacted list.

No production-code change.

## 3. Issue 5 — injected `ExecutionEnv` port (ADR-014)

**Why:** the root cause is not "missing approval" but that the kernel has **no side-effect capability boundary** — tools `import pathlib`/`subprocess` and touch the host directly, and `asyncio.wait_for` cancels the awaitable without killing the child process. Per ADR-014 the kernel defines a capability port; tools go through it; cancellation propagates to side effects.

**New file `runtime/execution_env.py`:**
- Result types (frozen dataclasses), **never raised**:
  - `ReadResult(ok: bool, content: str, error: str | None)`
  - `ShellResult(ok: bool, exit_code: int, output: str, error: str | None)` (`error` set for spawn failure / timeout / cancellation)
- `ExecutionEnv` interface (ABC) with:
  - `async read_text(path: str) -> ReadResult` — IO + containment only. **Truncation and the `[已截断]` marker stay in `ReadFileTool`** (presentation), preserving `test_read_file_truncates`.
  - `async run_shell(command: str, *, timeout: float) -> ShellResult`
- `LocalExecutionEnv(root: Path | None = None)` — real implementation:
  - `root=None` → permissive, behavior identical to today (keeps existing builtin tests green).
  - `root` set → resolve + containment check; `read_text` refuses paths outside `root` (returns `ok=False`, not raise); `run_shell` runs with `cwd=root`.
  - **Cancellation/timeout:** `run_shell` owns the subprocess lifecycle and guarantees the child is terminated on both its own timeout and on `asyncio.CancelledError` (kill in a `finally`/except path), then propagates cancellation. This is the cancellation-propagation fix.

**`tools/builtin.py`:**
- `ReadFileTool(env: ExecutionEnv | None = None)` and `RunCommandTool(env: ExecutionEnv | None = None)`; default `LocalExecutionEnv()`.
- Route side effects through `env` (`read_text` / `run_shell`); remove direct `pathlib`/`subprocess` use.
- `Tool.call(params)` signature unchanged → `ToolExecutor`, `core/tool.py`, and existing tool/executor tests untouched.
- `RunCommandTool.requires_approval` stays `True`.

**`cli/repl.py`:** construct `LocalExecutionEnv(root=Path.cwd())` and pass it to both tools (the live REPL tools are confined to the working dir). Minimal wiring only — no change to the assembly structure (#1 stays out of scope). Mock demo (`echo`) is unaffected.

**Tests:**
- `FakeExecutionEnv` in `tests/helpers.py` — records calls, returns canned results, no real fs/process.
- `ReadFileTool`/`RunCommandTool` route through an injected fake env (proves the boundary; no real fs needed).
- `LocalExecutionEnv(root=...)` refuses an out-of-bounds read and allows an in-bounds read.
- A long-running command that is cancelled/times out: assert the subprocess is actually terminated (not left running).
- Existing `test_builtin_tools.py` stays green unchanged (permissive default).

**Division of labor (ADR-009 ↔ ADR-014):** the kernel ships the *enforceable mechanism* (the chokepoint, a working & tested root mode, cancellation propagation). *Which* root to bind and command allow/deny *policy* are Coding Agent layer (阶段二) — not built here.

## 4. Verification gate (dev-guide §7)

```
python -m pytest -q                # 80 passed
python -m compileall -q core runtime providers tools cli tests
git diff --check
```

A change is "done" only when all three pass and the report states what changed, what was deliberately not changed, and why.

## 5. Non-goals (explicit)

- No HarnessSession; no CLI assembly refactor (#1).
- No extraction of approval/control/budget/supervisor out of `AgentLoop` (#2).
- No `run_one_turn` / `TurnSnapshot` / structured `RunResult` (§14.8 steps 1–2; deferred until the harness is the consumer).
- No command policy engine or workspace-root *binding policy* beyond the REPL default (Coding Agent layer).
- The existing uncommitted working-tree changes (runtime hardening + docs reshuffle) are pre-existing and not authored here; they are left intact.
