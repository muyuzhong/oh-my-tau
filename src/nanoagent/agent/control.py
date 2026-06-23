from __future__ import annotations

import asyncio
from typing import Any, Protocol

from nanoagent.ai import ToolCall


class AbortSignal:
    """Cooperative cancellation. Duck-compatible with ai.StreamOptions.signal (.aborted)."""

    def __init__(self):
        self._event = asyncio.Event()
        self.reason: Any = None

    @property
    def aborted(self) -> bool:
        return self._event.is_set()

    def abort(self, reason: Any = None) -> None:
        if self.aborted:
            return
        self.reason = reason
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()


class ControlSource(Protocol):
    async def request_approval(self, tool_call: ToolCall, tier: str) -> bool: ...


class AllowAll:
    """Framework default: no approval gate (policy belongs to the harness)."""

    async def request_approval(self, tool_call: ToolCall, tier: str) -> bool:
        return True
