from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from lion_code.agent import Agent
from lion_code.tooling.types import ToolResult


class TestAgentBuiltinRuntime(unittest.IsolatedAsyncioTestCase):
    def _agent(self, **kwargs):
        with patch("lion_code.agent.load_pre_tool_use_hooks", return_value=[]):
            return Agent(api_key="test-key", **kwargs)

    async def test_builtin_call_uses_runtime(self):
        agent = self._agent()
        agent.tool_runtime.execute = AsyncMock(
            return_value=ToolResult(content="through runtime")
        )

        result = await agent._execute_tool_call(
            "read_file",
            {"file_path": "README.md"},
            "call-1",
        )

        self.assertEqual(result, "through runtime")
        agent.tool_runtime.execute.assert_awaited_once_with(
            tool_call_id="call-1",
            name="read_file",
            arguments={"file_path": "README.md"},
        )

    async def test_runtime_preserves_read_before_write_state(self):
        agent = self._agent(permission_mode="bypassPermissions")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.txt"
            path.write_text("before", encoding="utf-8")

            denied = await agent._execute_tool_call(
                "write_file",
                {"file_path": str(path), "content": "after"},
            )
            await agent._execute_tool_call(
                "read_file",
                {"file_path": str(path)},
            )
            allowed = await agent._execute_tool_call(
                "write_file",
                {"file_path": str(path), "content": "after"},
            )

        self.assertIn("must read this file", denied)
        self.assertIn("Successfully wrote", allowed)

    def test_model_schema_comes_from_agent_registry(self):
        agent = self._agent()
        read_tool = agent.tool_registry.resolve("read_file")

        schemas = {
            schema["name"]: schema
            for schema in agent._active_anthropic_tools()
        }

        self.assertEqual(schemas["read_file"], read_tool.to_anthropic_schema())

    def test_custom_tools_limit_registry(self):
        agent = self._agent(
            custom_tools=[
                {
                    "name": "read_file",
                    "description": "compat",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ]
        )

        self.assertEqual(
            [tool.name for tool in agent.tool_registry.active_tools()],
            ["read_file"],
        )
        self.assertEqual(
            [schema["name"] for schema in agent._active_anthropic_tools()],
            ["read_file"],
        )


if __name__ == "__main__":
    unittest.main()
