from __future__ import annotations

import inspect
import unittest
from unittest.mock import AsyncMock, patch

from lion_code.agent import Agent


class TestAgentInternalRuntime(unittest.IsolatedAsyncioTestCase):
    def _agent(self):
        with patch("lion_code.agent.load_pre_tool_use_hooks", return_value=[]):
            return Agent(api_key="test-key")

    async def test_internal_tool_uses_runtime_controller(self):
        agent = self._agent()
        agent._execute_agent_tool = AsyncMock(return_value="sub-agent result")

        result = await agent._execute_tool_call(
            "agent",
            {"description": "inspect", "prompt": "inspect the repo"},
            "call-1",
        )

        self.assertEqual(result, "sub-agent result")
        agent._execute_agent_tool.assert_awaited_once()

    async def test_tool_search_updates_model_schema_from_registry(self):
        agent = self._agent()
        before = {schema["name"] for schema in agent._active_anthropic_tools()}

        result = await agent._execute_tool_call(
            "tool_search",
            {"query": "enter plan"},
        )
        after = {schema["name"] for schema in agent._active_anthropic_tools()}

        self.assertNotIn("enter_plan_mode", before)
        self.assertIn("enter_plan_mode", after)
        self.assertIn('"name": "enter_plan_mode"', result)

    async def test_dynamic_loop_registers_schedule_wakeup_temporarily(self):
        agent = self._agent()
        visible_during_chat = []

        async def chat(_prompt):
            visible_during_chat.append(
                agent.tool_registry.is_active("schedule_wakeup")
            )

        agent.chat = chat

        with patch("lion_code.agent.print_info"):
            await agent._run_loop_dynamic({"prompt": "check"})

        self.assertEqual(visible_during_chat, [True])
        with self.assertRaises(LookupError):
            agent.tool_registry.resolve("schedule_wakeup")

    def test_agent_router_contains_no_tool_name_branches(self):
        source = inspect.getsource(Agent._execute_tool_call)

        for forbidden in (
            'name == "agent"',
            'name == "skill"',
            'name == "schedule_wakeup"',
            'name in ("enter_plan_mode", "exit_plan_mode")',
            "is_mcp_tool(name)",
            "run_pre_tool_use_hooks",
            "check_permission",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
