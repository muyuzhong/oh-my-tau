import pytest
from pydantic import BaseModel

from nanoagent.ai import TextContent, ToolCall
from nanoagent.agent.tools import AgentTool, AgentToolResult, execute_tool_calls


class EchoArgs(BaseModel):
    text: str


class EchoTool(AgentTool):
    name = "echo"
    description = "echo back"
    parameters = EchoArgs
    label = "Echo"

    async def execute(self, tool_call_id, params, signal=None):
        return AgentToolResult(content=[TextContent(text=params.text)])


@pytest.mark.asyncio
async def test_executes_and_returns_result():
    calls = [ToolCall(id="t1", name="echo", arguments={"text": "hi"})]
    results = await execute_tool_calls(calls, [EchoTool()])
    assert results[0].content[0].text == "hi" and results[0].is_error is False


@pytest.mark.asyncio
async def test_unknown_tool_is_error_not_raise():
    results = await execute_tool_calls(
        [ToolCall(id="t1", name="nope", arguments={})], [EchoTool()]
    )
    assert results[0].is_error is True


@pytest.mark.asyncio
async def test_validation_error_is_error_not_raise():
    results = await execute_tool_calls(
        [ToolCall(id="t1", name="echo", arguments={})], [EchoTool()]
    )
    assert results[0].is_error is True


def test_to_wire_emits_json_schema():
    wire = EchoTool().to_wire()
    assert wire.name == "echo" and wire.parameters["properties"]["text"]["type"] == "string"


def test_to_wire_schema_cache_is_not_externally_mutable():
    tool = EchoTool()
    wire = tool.to_wire()
    wire.parameters["properties"].clear()

    next_wire = tool.to_wire()

    assert next_wire.parameters["properties"]["text"]["type"] == "string"
