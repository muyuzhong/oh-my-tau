from __future__ import annotations

import unittest

from lion_code.tooling.internal import create_schedule_wakeup_tool
from lion_code.tooling.registry import ToolRegistry


class TestTemporaryTools(unittest.TestCase):
    def test_schedule_wakeup_only_exists_inside_scope(self):
        registry = ToolRegistry()

        with self.assertRaises(LookupError):
            registry.resolve("schedule_wakeup")

        with registry.temporary_tool(create_schedule_wakeup_tool()):
            self.assertTrue(registry.is_active("schedule_wakeup"))

        with self.assertRaises(LookupError):
            registry.resolve("schedule_wakeup")


if __name__ == "__main__":
    unittest.main()
