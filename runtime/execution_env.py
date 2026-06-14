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

    def _resolve_path(self, path: str) -> Optional[Path]:
        try:
            candidate = Path(path)
            if self.root is not None and not candidate.is_absolute():
                candidate = self.root / candidate
            return candidate.resolve()
        except OSError:
            return None

    def _within_root(self, path: Path) -> bool:
        return self.root is None or path == self.root or self.root in path.parents

    async def read_text(self, path: str) -> ReadResult:
        resolved = self._resolve_path(path)
        if resolved is None or not self._within_root(resolved):
            return ReadResult(False, "", f"路径越界，超出工作区根：{path}")
        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
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
        # 等待退出用轮询 returncode，而非 wait_for(process.wait())。实测在 Windows
        # Proactor 上，被 wait_for 取消过的 process.wait() 会拖到进程自然退出才返回
        # （~4.8s），导致 kill 后仍无法及时回收；轮询不触发该缺陷，且 asyncio.sleep
        # 可被外层取消即时打断，从而让超时/取消真正落到子进程上。
        try:
            await self._poll_exit(process, timeout)
        except asyncio.CancelledError:
            # 取消同样必须落到子进程，避免遗留运行中的副作用。
            await self._terminate(process)
            raise
        if process.returncode is None:
            # 超时：杀掉直接子进程并立即返回，不读取 stdout。Windows 上被杀的 cmd.exe
            # 的孙进程可能仍持有管道写端，read() 会阻塞到孙进程自然退出；完整的进程树
            # 终止属 Coding Agent shell 工具（阶段二），不在内核端口范围内。
            await self._terminate(process)
            return ShellResult(False, -1, "", f"命令执行超时（>{timeout}s）")
        # 进程已自然退出，所有子进程已结束、管道关闭，读取缓冲输出会立即遇到 EOF。
        try:
            raw = await process.stdout.read() if process.stdout is not None else b""
        except Exception:
            raw = b""
        text = raw.decode("utf-8", errors="replace")
        code = process.returncode
        return ShellResult(code == 0, code, text, None if code == 0 else f"退出码 {code}")

    @staticmethod
    async def _poll_exit(process, timeout: float) -> None:
        """轮询等待进程退出，最多 timeout 秒；是否超时由调用方据 returncode 判断。"""
        waited, interval = 0.0, 0.05
        while process.returncode is None and waited < timeout:
            await asyncio.sleep(interval)
            waited += interval

    @staticmethod
    async def _terminate(process) -> None:
        """杀掉子进程并轮询回收；不用 await process.wait()（原因见 run_shell 注释）。"""
        if process.returncode is not None:
            LocalExecutionEnv._close_pipes(process)
            return
        try:
            process.kill()
        except ProcessLookupError:
            LocalExecutionEnv._close_pipes(process)
            return
        waited = 0.0
        while process.returncode is None and waited < 2.0:
            await asyncio.sleep(0.05)
            waited += 0.05
        LocalExecutionEnv._close_pipes(process)

    @staticmethod
    def _close_pipes(process) -> None:
        """终止路径主动关闭管道，避免 Windows Proactor transport 延迟回收。"""
        for stream in (process.stdout, process.stderr):
            transport = getattr(stream, "_transport", None)
            if transport is not None:
                transport.close()
