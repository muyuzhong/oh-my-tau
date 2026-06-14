import asyncio

from core.tool import ToolResult
from runtime.blocks import ToolUseBlock
from runtime.executor import ToolExecutor, ToolRegistry, validate_params
from tests.helpers import EchoTool


def make_executor(*tools, **kwargs):
    registry = ToolRegistry()
    for tool in tools: registry.register(tool)
    return ToolExecutor(registry, **kwargs)


def test_validate_params():
    schema = {"properties": {"text": {"type": "string"}, "n": {"type": "integer"}}, "required": ["text"]}
    assert validate_params(schema, {"text": "hi", "n": 3}) == []
    assert "缺少必填参数" in validate_params(schema, {})[0]
    assert "类型应为" in validate_params(schema, {"text": 42})[0]
    assert "未知参数" in validate_params(schema, {"text": "a", "bad": 1})[0]


async def test_tool_not_found():
    result = await make_executor().execute_one(ToolUseBlock("nope", {}, "t1"))
    assert result.is_error and result.error_type == "ToolNotFound"


async def test_success_maps_tool_result():
    result = await make_executor(EchoTool()).execute_one(ToolUseBlock("echo", {"text": "hi"}, "t2"))
    assert not result.is_error and result.content == "echo:hi"


async def test_invalid_params_rejected_before_execution():
    result = await make_executor(EchoTool()).execute_one(ToolUseBlock("echo", {}, "t3"))
    assert result.error_type == "ParameterValidation"


async def test_exception_becomes_observation():
    class Boom(EchoTool):
        def name(self): return "boom"
        async def call(self, params): raise RuntimeError("炸了")
    result = await make_executor(Boom()).execute_one(ToolUseBlock("boom", {"text": "x"}, "t4"))
    assert result.error_type == "RuntimeError" and "炸了" in result.content


async def test_timeout_does_not_retry_tool_with_possible_side_effects():
    class Slow(EchoTool):
        timeout_seconds = .01
        calls = 0
        def name(self): return "slow"
        async def call(self, params):
            type(self).calls += 1
            await asyncio.sleep(1)
    result = await make_executor(Slow()).execute_one(ToolUseBlock("slow", {"text": "x"}, "t5"))
    assert result.error_type == "ToolTimeout" and Slow.calls == 1


async def test_execute_all_concurrent_and_ordered():
    class Gate(EchoTool):
        active = max_active = 0
        def name(self): return "gate"
        async def call(self, params):
            type(self).active += 1
            type(self).max_active = max(type(self).max_active, type(self).active)
            await asyncio.sleep(.02)
            type(self).active -= 1
            return ToolResult(True, params["text"], 0)
    calls = [ToolUseBlock("gate", {"text": str(i)}, f"g{i}") for i in range(3)]
    results = await make_executor(Gate(), max_concurrent=2).execute_all(calls)
    assert [r.tool_use_id for r in results] == ["g0", "g1", "g2"] and Gate.max_active == 2
