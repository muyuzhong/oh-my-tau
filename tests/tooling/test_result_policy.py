from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lion_code.tooling.context import ToolContext
from lion_code.tooling.middleware import ResultPolicyMiddleware
from lion_code.tooling.registry import ToolRegistry
from lion_code.tooling.result_store import ResultStore
from lion_code.tooling.runtime import ToolRuntime
from lion_code.tooling.types import LionTool, ToolCapabilities, ToolResult


async def _execute(_context, _tool_call_id, _arguments, _on_update):
    return ToolResult(content="unused")


def _tool(policy: str) -> LionTool:
    return LionTool(
        name="large_output",
        label="large_output",
        description="large output",
        parameters={"type": "object", "properties": {}},
        execute_fn=_execute,
        capabilities=ToolCapabilities(result_policy=policy),
    )


class TestResultStore(unittest.IsolatedAsyncioTestCase):
    def test_large_result_is_persisted_before_preview(self):
        original = "first\n" + "x" * 2_000 + "\nlast"
        with tempfile.TemporaryDirectory() as directory:
            store = ResultStore(Path(directory), threshold_bytes=100)

            result = store.process(
                _tool("persist_large"),
                ToolResult(content=original),
            )

            persisted = Path(str(result.details["persisted_path"]))
            self.assertEqual(persisted.read_text(encoding="utf-8"), original)
            self.assertEqual(
                result.details["original_bytes"],
                len(original.encode("utf-8")),
            )
            self.assertIn("Full output saved to", result.content)

    def test_snippable_large_result_is_also_recoverable(self):
        original = "z" * 200
        with tempfile.TemporaryDirectory() as directory:
            result = ResultStore(
                Path(directory),
                threshold_bytes=10,
            ).process(_tool("snippable"), ToolResult(content=original))

            self.assertTrue(Path(str(result.details["persisted_path"])).is_file())

    def test_normal_result_is_returned_unchanged(self):
        source = ToolResult(content="z" * 200)
        with tempfile.TemporaryDirectory() as directory:
            result = ResultStore(
                Path(directory),
                threshold_bytes=10,
            ).process(_tool("normal"), source)

            self.assertIs(result, source)
            self.assertEqual(list(Path(directory).iterdir()), [])

    async def test_runtime_applies_result_policy_before_returning(self):
        original = "runtime" * 100

        async def execute(_context, _tool_call_id, _arguments, _on_update):
            return ToolResult(content=original)

        tool = LionTool(
            name="runtime_output",
            label="runtime_output",
            description="runtime output",
            parameters={"type": "object", "properties": {}},
            execute_fn=execute,
            capabilities=ToolCapabilities(result_policy="persist_large"),
        )
        registry = ToolRegistry()
        registry.register(tool)
        context = ToolContext(
            session_id="session",
            cwd=Path.cwd(),
            controller=object(),
            registry=registry,
            permission_mode="default",
            plan_file_path=None,
            read_file_state={},
        )
        with tempfile.TemporaryDirectory() as directory:
            runtime = ToolRuntime(
                registry,
                context,
                [ResultPolicyMiddleware(ResultStore(
                    Path(directory),
                    threshold_bytes=10,
                ))],
            )

            result = await runtime.execute(
                tool_call_id="call-1",
                name="runtime_output",
                arguments={},
            )

            self.assertEqual(
                Path(str(result.details["persisted_path"])).read_text(encoding="utf-8"),
                original,
            )


if __name__ == "__main__":
    unittest.main()
