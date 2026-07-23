from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lion_code.tooling.builtin import BUILTIN_TOOL_NAMES, create_builtin_tools
from lion_code.tooling.registry import ToolRegistry
from lion_code.tooling.runtime import ToolRuntime
from lion_code.tools import tool_definitions


class TestBuiltinTools(unittest.IsolatedAsyncioTestCase):
    def test_builtin_schema_has_single_object_source(self):
        tools = create_builtin_tools()

        self.assertEqual({tool.name for tool in tools}, BUILTIN_TOOL_NAMES)
        compatible = {
            tool["name"]: tool
            for tool in tool_definitions
            if tool["name"] in BUILTIN_TOOL_NAMES
        }
        self.assertEqual(
            compatible,
            {tool.name: tool.to_anthropic_schema() for tool in tools},
        )

    async def test_read_file_runs_through_runtime(self):
        registry = ToolRegistry()
        for tool in create_builtin_tools():
            registry.register(tool)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hello.txt"
            path.write_text("第一行\nsecond", encoding="utf-8")

            result = await ToolRuntime(registry).execute(
                tool_call_id="call-1",
                name="read_file",
                arguments={"file_path": str(path)},
            )

        self.assertFalse(result.is_error)
        self.assertIn("1 | 第一行", result.content)
        self.assertIn("2 | second", result.content)

    def test_capabilities_drive_execution_mode(self):
        tools = {tool.name: tool for tool in create_builtin_tools()}

        self.assertEqual(tools["read_file"].execution_mode, "parallel")
        self.assertTrue(tools["read_file"].capabilities.allowed_in_plan)
        self.assertEqual(tools["write_file"].execution_mode, "sequential")
        self.assertTrue(
            tools["write_file"].capabilities.requires_read_before_write
        )


if __name__ == "__main__":
    unittest.main()
