"""实时控制平面：外部系统通过收件箱向运行中的引擎注入指令。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Abort:
    """立即中止本次运行。"""


@dataclass
class Steer:
    """在安全点追加一条用户转向消息，绝不改写既有历史。"""
    text: str


@dataclass
class Approve:
    ids: Optional[List[str]] = None


@dataclass
class Deny:
    ids: Optional[List[str]] = None


@dataclass
class Pause:
    """在安全点暂停。"""


@dataclass
class Resume:
    """恢复暂停的运行。"""


class ControlPlane:
    """同时提供队列顺序语义和中断旁路标志。"""

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self.abort_requested = False

    def submit(self, command) -> None:
        if isinstance(command, Abort):
            # 流式循环需要无需 await 就能立刻看到中断请求。
            self.abort_requested = True
        self._queue.put_nowait(command)

    def drain_nowait(self) -> list:
        commands = []
        while True:
            try:
                commands.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                return commands

    async def _wait_for(self, kinds: tuple):
        # 审批等待不能吞掉 Steer/Pause 等不相关指令，因此暂存后按原顺序放回。
        stash = []
        try:
            while True:
                command = await self._queue.get()
                if isinstance(command, kinds):
                    return command
                stash.append(command)
        finally:
            for command in stash:
                self._queue.put_nowait(command)

    async def wait_decision(self):
        return await self._wait_for((Approve, Deny, Abort))

    async def wait_resume(self):
        return await self._wait_for((Resume, Abort))
