from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from lion_code.tooling.environment import ToolEnvironment


class _Manager:
    def __init__(self):
        self.disconnect_all = AsyncMock()


class TestToolEnvironment(unittest.IsolatedAsyncioTestCase):
    def test_child_reuses_parent_mcp_manager(self):
        manager = _Manager()
        root = ToolEnvironment(mcp_manager=manager)

        child = root.child_view()

        self.assertIs(child.mcp_manager, manager)
        self.assertTrue(root.owns_mcp_manager)
        self.assertFalse(child.owns_mcp_manager)

    async def test_child_close_does_not_disconnect_mcp(self):
        manager = _Manager()
        child = ToolEnvironment(mcp_manager=manager).child_view()

        await child.close()

        manager.disconnect_all.assert_not_awaited()

    async def test_root_close_disconnects_mcp_once(self):
        manager = _Manager()
        root = ToolEnvironment(mcp_manager=manager)

        await root.close()
        await root.close()

        manager.disconnect_all.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
