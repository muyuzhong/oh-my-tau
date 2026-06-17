import pytest
from pydantic import BaseModel

from nanoagent.ai import TextContent, UserMessage
from nanoagent.ai.provider import clear_providers
from nanoagent.ai.providers.mock import create_mock_model, register_mock
from nanoagent.agent.loop import AgentLoopConfig, agent_loop
from nanoagent.agent.result import StopReason
from nanoagent.agent.tools import AgentTool, AgentToolResult


class Args(BaseModel):
    pass


class Tool1(AgentTool):
    name = "t"
    description = "d"
    parameters = Args
    label = "T"

    async def execute(self, tool_call_id, params, signal=None):
        return AgentToolResult(content=[TextContent(text="ran")])


class DenyAll:
    async def request_approval(self, tool_call, tier):
        return False


@pytest.mark.asyncio
async def test_max_turns_terminates():
    clear_providers()
    register_mock()
    mock = create_mock_model(
        handler=lambda ctx: {"content": [{"type": "toolCall", "name": "t", "arguments": {}}]}
    )
    cfg = AgentLoopConfig(model=mock, max_turns=3)
    events = [
        e
        async for e in agent_loop(
            prompts=[UserMessage(content="go")],
            system_prompt=[],
            messages=[],
            tools=[Tool1()],
            config=cfg,
        )
    ]
    assert events[-1].result.reason is StopReason.MAX_TURNS


@pytest.mark.asyncio
async def test_wire_error_maps_to_run_error():
    clear_providers()
    register_mock()
    mock = create_mock_model(responses=[{"content": [], "error": "boom"}])
    cfg = AgentLoopConfig(model=mock)
    events = [
        e
        async for e in agent_loop(
            prompts=[UserMessage(content="go")],
            system_prompt=[],
            messages=[],
            tools=[],
            config=cfg,
        )
    ]
    end = events[-1]
    assert end.result.reason is StopReason.ERROR and end.result.error == "boom"


@pytest.mark.asyncio
async def test_approval_denied_blocks_tool():
    clear_providers()
    register_mock()
    mock = create_mock_model(
        responses=[
            {"content": [{"type": "toolCall", "name": "t", "arguments": {}}]},
            {"content": ["after"]},
        ]
    )
    cfg = AgentLoopConfig(model=mock, control=DenyAll())
    events = [
        e
        async for e in agent_loop(
            prompts=[UserMessage(content="go")],
            system_prompt=[],
            messages=[],
            tools=[Tool1()],
            config=cfg,
        )
    ]
    tool_results = [m for m in events[-1].messages if m.role == "toolResult"]
    assert tool_results[0].is_error is True
    assert "approval" in tool_results[0].content[0].text.lower()
