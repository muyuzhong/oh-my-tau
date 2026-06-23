from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StopReason(str, Enum):
    """Run-level termination reason (whole loop).

    Distinct from nanoagent.ai.StopReason (per-message wire stop).
    """

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

    @property
    def succeeded(self) -> bool:
        return self.reason is StopReason.COMPLETED
