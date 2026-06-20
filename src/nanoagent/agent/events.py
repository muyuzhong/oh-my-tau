"""Agent runtime events and their ordering contract.

A *run* is the event sequence emitted by ``agent_loop(...)`` for one invocation.
It opens with exactly one ``AgentStart`` and closes with exactly one ``AgentEnd``.

Canonical ordering::

    agent_start
      # per prompt message m:
      message_start(m) ; message_end(m)
      # per turn:
      turn_start
        # per injected message s (steering; follow-up later):
        message_start(s) ; message_end(s)
        # assistant streaming:
        message_start(assistant)
        message_update(assistant, ev)*        # assistant only
        message_end(assistant)
        # if terminal: turn_end(assistant, []) ; agent_end ; stop
        # else, tool execution phase (below)
        turn_end(assistant, tool_results)     # tool_results in source order
    agent_end(messages, result)               # exactly once, terminal

Tool execution phase, for the tool calls in source order s1..sn:
  - ``tool_execution_start`` per call, in source order.
  - ``tool_execution_update``* per call, between that call's start and end,
    correlated by ``tool_call_id`` (emitted once tools stream progress; see the
    tool-lifecycle spec).
  - ``tool_execution_end`` per call, carrying ``result`` + ``is_error``.
    Sequential execution emits ends in source order; parallel execution emits
    ends in *completion* order -- consumers MUST correlate by ``tool_call_id``,
    never by position.
  - tool-result messages (``message_start``/``message_end`` for a
    ``ToolResultMessage``) are emitted after all ends of the batch, in source
    order, so transcript order is independent of completion order.

Guaranteed invariants (stable; locked by tests/agent/test_event_contract.py):
  G1  exactly one agent_start (first) and one agent_end (last).
  G2  each message_start(m) has exactly one matching message_end with the same
      ``message.id``; an assistant message keeps one id from start to end.
  G3  message_update is emitted only for the current turn's assistant message,
      strictly between its message_start and message_end.
  G4  per turn, turn_start precedes turn_end; turns do not nest.
  G5  per tool_call_id: start precedes any update precedes end; exactly one
      start and one end per executed call (denied/unknown tools still get both).
  G6  tool-result transcript messages come after all tool_execution_end of the
      batch, in source order.
  G7  agent_end.result.final_message_id is the last produced message id (or None).

  Note: G5 constrains only per-id ordering, not the global order of ends, so a
  later switch to completion-ordered parallel ends does not break this contract.

Event-derived state (Agent reduces each event into AgentState before emitting,
so subscribers observe up-to-date state):
  - from message_start(assistant) through message_update*: ``streaming_message``
    is the in-progress assistant message; ``is_streaming`` is True.
  - after message_end(m): m is in ``messages``; ``streaming_message`` is None.
  - between tool_execution_start(id) and tool_execution_end(id): id is in
    ``pending_tool_calls``.
  - run start clears ``error_message``; after agent_end it is ``result.error``.
"""

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
    AgentStart,
    AgentEnd,
    TurnStart,
    TurnEnd,
    MessageStart,
    MessageUpdate,
    MessageEnd,
    ToolExecutionStart,
    ToolExecutionUpdate,
    ToolExecutionEnd,
]
