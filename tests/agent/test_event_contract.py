import pytest
from pydantic import BaseModel

from nanoagent.ai import TextContent, UserMessage
from nanoagent.ai.provider import clear_providers
from nanoagent.ai.providers.mock import create_mock_model, register_mock
from nanoagent.agent.loop import AgentLoopConfig, agent_loop
from nanoagent.agent.tools import AgentTool, AgentToolResult


class _Args(BaseModel):
    pass


class _Tool(AgentTool):
    description = "d"
    parameters = _Args

    def __init__(self, name):
        self.name = name
        self.label = name

    async def execute(self, tool_call_id, params, signal=None):
        return AgentToolResult(content=[TextContent(text=f"ran {self.name}")])


async def _collect(cfg, tools):
    return [
        e
        async for e in agent_loop(
            prompts=[UserMessage(content="go")],
            system_prompt=[],
            messages=[],
            tools=tools,
            config=cfg,
        )
    ]


@pytest.mark.asyncio
async def test_assistant_message_identity_stable_across_stream():
    """G2 foundation: an assistant message keeps one id from message_start to message_end."""
    clear_providers()
    register_mock()
    mock = create_mock_model(responses=[{"content": ["hello"]}])
    events = await _collect(AgentLoopConfig(model=mock), [])
    starts = [e for e in events if e.type == "message_start" and e.message.role == "assistant"]
    ends = [e for e in events if e.type == "message_end" and e.message.role == "assistant"]
    assert len(starts) == len(ends) == 1
    assert starts[0].message.id == ends[0].message.id


@pytest.mark.asyncio
async def test_event_contract_invariants_g1_to_g7():
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
    events = await _collect(AgentLoopConfig(model=mock), [_Tool("a"), _Tool("b")])
    ts = [e.type for e in events]

    # G1: exactly one agent_start (first), one agent_end (last)
    assert ts[0] == "agent_start" and ts[-1] == "agent_end"
    assert ts.count("agent_start") == 1 and ts.count("agent_end") == 1

    # G4: turn_start/turn_end balanced, no nesting
    depth = 0
    for t in ts:
        if t == "turn_start":
            depth += 1
            assert depth == 1, "turns must not nest"
        elif t == "turn_end":
            depth -= 1
            assert depth == 0
    assert depth == 0
    assert ts.count("turn_start") == ts.count("turn_end") == 2

    # G2: message_start/message_end pair by id (one start per id, start before end)
    starts: dict[str, int] = {}
    ends: dict[str, int] = {}
    for i, e in enumerate(events):
        if e.type == "message_start":
            assert e.message.id not in starts, "one message_start per message id"
            starts[e.message.id] = i
        elif e.type == "message_end":
            ends[e.message.id] = i
    assert set(starts) == set(ends), "every message_start has a matching message_end by id"
    for mid in starts:
        assert starts[mid] < ends[mid]

    # G3: message_update only for the assistant message, strictly between its start and end
    for i, e in enumerate(events):
        if e.type == "message_update":
            assert e.message.role == "assistant"
            mid = e.message.id
            assert mid in starts and starts[mid] < i < ends[mid]

    # G5: per tool_call_id, start before end, exactly one of each
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

    # G6: tool-result transcript messages come after all tool_execution_end of the batch
    last_tool_end = max(tend.values())
    tool_result_starts = [
        i
        for i, e in enumerate(events)
        if e.type == "message_start" and e.message.role == "toolResult"
    ]
    assert tool_result_starts and all(i > last_tool_end for i in tool_result_starts)

    # G7: agent_end.final_message_id is the last produced message id
    last_message_end = [e for e in events if e.type == "message_end"][-1]
    assert events[-1].result.final_message_id == last_message_end.message.id
