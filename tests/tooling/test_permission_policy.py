from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lion_code.tooling.permission import PermissionPolicy, reset_permission_cache
from lion_code.tooling.types import LionTool, ToolCapabilities, ToolResult


async def _execute(_context, _tool_call_id, _arguments, _on_update):
    return ToolResult(content="ok")


def _tool(name: str, **capabilities) -> LionTool:
    return LionTool(
        name=name,
        label=name,
        description=name,
        parameters={"type": "object", "properties": {}},
        execute_fn=_execute,
        capabilities=ToolCapabilities(**capabilities),
    )


class TestPermissionPolicy(unittest.TestCase):
    def tearDown(self):
        reset_permission_cache()

    def test_explicit_deny_beats_bypass(self):
        with tempfile.TemporaryDirectory() as home_dir, tempfile.TemporaryDirectory() as cwd_dir:
            settings = Path(cwd_dir) / ".claude" / "settings.json"
            settings.parent.mkdir()
            settings.write_text(
                json.dumps({"permissions": {"deny": ["run_shell"]}}),
                encoding="utf-8",
            )
            decision = PermissionPolicy(
                home=Path(home_dir),
                cwd=Path(cwd_dir),
            ).check(
                tool=_tool("run_shell", executes_process=True),
                arguments={"command": "echo ok"},
                mode="bypassPermissions",
                plan_file_path=None,
            )

        self.assertEqual(decision.action, "deny")

    def test_plan_mode_blocks_mutating_tool(self):
        decision = PermissionPolicy().check(
            tool=_tool("write_file", mutates_workspace=True),
            arguments={"file_path": "other.md"},
            mode="plan",
            plan_file_path="plan.md",
        )

        self.assertEqual(decision.action, "deny")

    def test_plan_mode_allows_read_only_tool(self):
        decision = PermissionPolicy().check(
            tool=_tool("read_file", read_only=True, allowed_in_plan=True),
            arguments={"file_path": "README.md"},
            mode="plan",
            plan_file_path="plan.md",
        )

        self.assertEqual(decision.action, "allow")

    def test_plan_file_is_only_mutating_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = str(Path(directory) / "plan.md")
            decision = PermissionPolicy().check(
                tool=_tool("write_file", mutates_workspace=True),
                arguments={"file_path": plan},
                mode="plan",
                plan_file_path=plan,
            )

        self.assertEqual(decision.action, "allow")


if __name__ == "__main__":
    unittest.main()
