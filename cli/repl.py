"""Mono 终端 REPL。

用法：``python -m cli.repl``。环境变量 ``MONO_PROVIDER`` 可选 mock、
anthropic、openai；真实 Provider 还需对应 API key。REPL 只负责输入输出，
所有运行语义仍由 AgentLoop、ControlPlane 和 SessionState 提供。
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from providers.anthropic import AnthropicProvider
from providers.mock import MockProvider
from providers.openai_compat import OpenAICompatProvider
from runtime import events as ev
from runtime.context import TokenLedger
from runtime.control import Abort, Approve, ControlPlane, Deny, Steer
from runtime.engine import AgentLoop
from runtime.execution_env import LocalExecutionEnv
from runtime.executor import ToolRegistry
from runtime.state import SessionState
from tools.builtin import ReadFileTool, RunCommandTool


def make_provider():
    """根据环境变量构建 Provider，并返回 Provider 与模型名。"""
    kind = os.getenv("MONO_PROVIDER", "mock").lower()
    if kind == "anthropic":
        return AnthropicProvider(), os.getenv("MONO_MODEL", "claude-sonnet-4-6")
    if kind == "openai":
        return OpenAICompatProvider(base_url=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")), os.getenv("MONO_MODEL", "deepseek-chat")
    script = [MockProvider.tool_turn("run_command", {"command": "echo hello-from-mono"}),
              MockProvider.text_turn("命令处理完成，离线演示结束。")]
    return MockProvider(script), "mock-model"


class KeyListener:
    """Windows 非阻塞按键监听：Esc 中断，输入整行后注入转向消息。"""
    def __init__(self, control, console):
        self.control, self.console, self.suspended, self.buffer, self.task = control, console, False, "", None
    def start(self): self.task = asyncio.create_task(self._loop())
    def stop(self):
        if self.task: self.task.cancel()
    async def _loop(self):
        try: import msvcrt
        except ImportError: return
        while True:
            await asyncio.sleep(.05)
            if self.suspended: continue
            while msvcrt.kbhit():
                char = msvcrt.getwch()
                if char == "\x1b":
                    self.control.submit(Abort()); self.console.print("\n[red]已请求中断[/red]")
                elif char in ("\r", "\n"):
                    if self.buffer.strip(): self.control.submit(Steer(self.buffer.strip()))
                    self.buffer = ""
                else: self.buffer += char


async def render_events(run, console, control, listener):
    """把运行时事件映射为终端输出；审批事件通过控制面反向回复引擎。"""
    async for event in run:
        if isinstance(event, ev.TextDeltaEvent): console.print(event.text, end="", highlight=False)
        elif isinstance(event, ev.ThinkingDeltaEvent): console.print(f"[dim]{event.thinking}[/dim]", end="")
        elif isinstance(event, ev.ToolCallStarted): console.print(f"\n[yellow]工具调用：{event.name}[/yellow]")
        elif isinstance(event, ev.ApprovalRequested):
            listener.suspended = True
            approved, denied = [], []
            for call in event.calls:
                console.print(Panel(json.dumps(call.input, ensure_ascii=False, indent=2), title=f"待审批：{call.name}", border_style="red"))
                answer = await asyncio.to_thread(input, "允许执行？[y/n] ")
                (approved if answer.strip().lower() == "y" else denied).append(call.id)
            listener.suspended = False
            if approved: control.submit(Approve(approved))
            if denied: control.submit(Deny(denied))
        elif isinstance(event, ev.ToolResultReceived):
            console.print(f"[{'red' if event.is_error else 'green'}]{event.name}: {event.content_preview}[/]")
        elif isinstance(event, ev.ContextCompacted): console.print(f"[magenta]上下文已压缩 {event.before_tokens}->{event.after_tokens}[/magenta]")
        elif isinstance(event, ev.ErrorEvent): console.print(f"[red]{event.error_type}: {event.message}[/red]")
        elif isinstance(event, ev.AgentEnded): console.print(f"\n[dim]本轮结束（{event.reason}）[/dim]")


async def main():
    console = Console()
    provider, model = make_provider()
    env = LocalExecutionEnv(root=Path.cwd())
    registry = ToolRegistry(); registry.register(ReadFileTool(env)); registry.register(RunCommandTool(env))
    state, ledger = SessionState(), TokenLedger()
    console.print(Panel(f"provider={type(provider).__name__} model={model}\n命令：/status /resume <id> /quit", title="Mono"))
    while True:
        try: user = (await asyncio.to_thread(input, "\nyou> ")).strip()
        except (EOFError, KeyboardInterrupt): break
        if not user: continue
        if user == "/quit": break
        if user == "/status":
            console.print(f"session={state.session_id} messages={len(state.messages)} tokens={ledger.total_tokens} api_calls={ledger.api_calls}"); continue
        if user.startswith("/resume "):
            try: state = SessionState.resume(user.split(maxsplit=1)[1]); console.print("[green]会话已恢复[/green]")
            except FileNotFoundError: console.print("[red]找不到会话转录[/red]")
            continue
        control = ControlPlane()
        listener = KeyListener(control, console); listener.start()
        try:
            await render_events(AgentLoop(provider, registry, state=state, control=control, ledger=ledger, model=model).run(user), console, control, listener)
        finally: listener.stop()


if __name__ == "__main__":
    asyncio.run(main())
