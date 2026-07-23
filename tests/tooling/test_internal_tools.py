from __future__ import annotations

import unittest
from pathlib import Path

from lion_code.tooling.context import ToolContext
from lion_code.tooling.internal import (
    create_agent_tool,
    create_enter_plan_mode_tool,
    create_exit_plan_mode_tool,
    create_schedule_wakeup_tool,
    create_skill_tool,
)
from lion_code.tooling.registry import ToolRegistry
from lion_code.tooling.runtime import ToolRuntime
from lion_code.tooling.types import ToolResult


class _Controller:
    def __init__(self):
        self.calls = []

    async def run_subagent_tool(self, arguments):
        self.calls.append(("agent", dict(arguments)))
        return ToolResult(content="agent result")

    async def run_skill_tool(self, arguments):
        self.calls.append(("skill", dict(arguments)))
        return ToolResult(content="skill result")

    async def enter_plan_mode_tool(self):
        self.calls.append(("enter", {}))
        return ToolResult(content="entered")

    async def exit_plan_mode_tool(self):
        self.calls.append(("exit", {}))
        return ToolResult(content="exited")

    async def schedule_wakeup_tool(self, arguments):
        self.calls.append(("schedule", dict(arguments)))
        return ToolResult(content="scheduled")


def _runtime(tool):
    registry = ToolRegistry()
    registry.register(tool, activate=True)
    controller = _Controller()
    context = ToolContext(
        session_id="session",
        cwd=Path.cwd(),
        controller=controller,
        registry=registry,
        permission_mode="default",
        plan_file_path=None,
        read_file_state={},
    )
    return ToolRuntime(registry, context), controller


class TestInternalTools(unittest.IsolatedAsyncioTestCase):
    async def test_agent_tool_calls_controller(self):
        runtime, controller = _runtime(create_agent_tool())

        result = await runtime.execute(
            tool_call_id="call-1",
            name="agent",
            arguments={"prompt": "inspect", "description": "repo"},
        )

        self.assertEqual(result.content, "agent result")
        self.assertEqual(controller.calls[0][0], "agent")

    async def test_skill_tool_calls_controller(self):
        runtime, controller = _runtime(create_skill_tool())

        result = await runtime.execute(
            tool_call_id="call-1",
            name="skill",
            arguments={"skill_name": "demo"},
        )

        self.assertEqual(result.content, "skill result")
        self.assertEqual(controller.calls, [("skill", {"skill_name": "demo"})])

    async def test_plan_tools_call_distinct_controller_methods(self):
        for tool, expected in (
            (create_enter_plan_mode_tool(), "enter"),
            (create_exit_plan_mode_tool(), "exit"),
        ):
            runtime, controller = _runtime(tool)

            result = await runtime.execute(
                tool_call_id="call-1",
                name=tool.name,
                arguments={},
            )

            self.assertEqual(result.content, f"{expected}ed")
            self.assertEqual(controller.calls, [(expected, {})])

    async def test_schedule_wakeup_calls_controller(self):
        runtime, controller = _runtime(create_schedule_wakeup_tool())
        arguments = {"delaySeconds": 60, "reason": "later", "prompt": "work"}

        result = await runtime.execute(
            tool_call_id="call-1",
            name="schedule_wakeup",
            arguments=arguments,
        )

        self.assertEqual(result.content, "scheduled")
        self.assertEqual(controller.calls, [("schedule", arguments)])


if __name__ == "__main__":
    unittest.main()
