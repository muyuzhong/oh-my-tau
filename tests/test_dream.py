"""显式 `/dream` Memory 整合闭环测试。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from lion_code import dream
from lion_code.agent import Agent
from lion_code.frontmatter import format_frontmatter, parse_frontmatter


def _write_memory(path: Path, name: str, memory_type: str, body: str) -> None:
    path.write_text(
        format_frontmatter(
            {"name": name, "description": f"{name} description", "type": memory_type},
            body,
        ),
        encoding="utf-8",
    )


class TestDreamSessions(unittest.TestCase):
    def test_uses_latest_five_project_sessions_and_projects_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            sessions = root / "sessions"
            project.mkdir()
            sessions.mkdir()

            for index in range(7):
                data = {
                    "metadata": {
                        "id": f"session-{index}",
                        "cwd": str(project),
                        "startTime": f"2026-07-2{index}T00:00:00Z",
                    },
                    "openaiMessages": [
                        {"role": "system", "content": "ordinary system prompt"},
                        {
                            "role": "user",
                            "content": (
                                "<system-reminder>project instructions</system-reminder>\n\n"
                                f"remember preference {index}"
                            ),
                        },
                        {
                            "role": "assistant",
                            "content": "intermediate text",
                            "tool_calls": [{"id": "call"}],
                        },
                        {"role": "tool", "content": "x" * 2_000},
                        {"role": "assistant", "content": f"final answer {index}"},
                    ],
                }
                path = sessions / f"session-{index}.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                os.utime(path, (index + 1, index + 1))

            (sessions / "other.json").write_text(json.dumps({
                "metadata": {"id": "other", "cwd": str(root / "other")},
                "openaiMessages": [{"role": "user", "content": "ignore"}],
            }), encoding="utf-8")
            (sessions / "broken.json").write_text("not json", encoding="utf-8")

            with patch.object(dream, "SESSION_DIR", sessions):
                result = dream._recent_project_sessions(project)

        self.assertEqual(
            [session["id"] for session in result],
            ["session-6", "session-5", "session-4", "session-3", "session-2"],
        )
        messages = result[0]["messages"]
        combined = "\n".join(message["content"] for message in messages)
        self.assertIn("remember preference 6", combined)
        self.assertIn("final answer 6", combined)
        self.assertNotIn("ordinary system prompt", combined)
        self.assertNotIn("project instructions", combined)
        self.assertNotIn("intermediate text", combined)
        tool = next(message for message in messages if message["role"] == "tool")
        self.assertLessEqual(len(tool["content"]), dream.MAX_TOOL_RESULT_CHARS)


class TestDreamPlan(unittest.TestCase):
    def test_applies_create_update_delete_and_rebuilds_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            _write_memory(memory_dir / "project_old.md", "Old", "project", "obsolete")
            _write_memory(memory_dir / "user_preferences.md", "Preferences", "user", "old body")
            dream._update_memory_index(memory_dir)
            context = dream.DreamContext(
                project_root=memory_dir,
                memory_dir=memory_dir,
                memory_index="",
                memory_manifest=[],
                sessions=[],
                memory_snapshot=dream._memory_snapshot(memory_dir),
            )
            raw = json.dumps({
                "reason": "merged durable facts",
                "upsert": [
                    {
                        "filename": "project_architecture.md",
                        "name": "Architecture",
                        "description": "current architecture decision",
                        "type": "project",
                        "content": "Use the verified architecture.",
                    },
                    {
                        "filename": "user_preferences.md",
                        "name": "Preferences",
                        "description": "latest explicit preferences",
                        "type": "user",
                        "content": "Use concise Chinese commits.",
                    },
                ],
                "delete": ["project_old.md"],
            })

            result = dream.apply_dream_plan(context, dream.parse_dream_plan(raw))

            self.assertEqual(result.created, ["project_architecture.md"])
            self.assertEqual(result.updated, ["user_preferences.md"])
            self.assertEqual(result.deleted, ["project_old.md"])
            self.assertFalse((memory_dir / "project_old.md").exists())
            preferences = parse_frontmatter(
                (memory_dir / "user_preferences.md").read_text(encoding="utf-8")
            )
            self.assertEqual(preferences.body, "Use concise Chinese commits.")
            index = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
            self.assertIn("project_architecture.md", index)
            self.assertNotIn("project_old.md", index)

    def test_rejects_invalid_or_stale_plan_before_writing(self):
        invalid = json.dumps({
            "upsert": [{
                "filename": "../outside.md",
                "name": "Outside",
                "description": "invalid path",
                "type": "project",
                "content": "bad",
            }],
            "delete": [],
        })
        with self.assertRaisesRegex(ValueError, "非法 Memory 文件名"):
            dream.parse_dream_plan(invalid)

        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            target = memory_dir / "project_state.md"
            _write_memory(target, "State", "project", "before")
            context = dream.DreamContext(
                project_root=memory_dir,
                memory_dir=memory_dir,
                memory_index="",
                memory_manifest=[],
                sessions=[],
                memory_snapshot=dream._memory_snapshot(memory_dir),
            )
            target.write_text("changed concurrently", encoding="utf-8")
            plan = dream.DreamPlan("delete", [], ["project_state.md"])

            with self.assertRaisesRegex(RuntimeError, "其他进程修改"):
                dream.apply_dream_plan(context, plan)
            self.assertEqual(target.read_text(encoding="utf-8"), "changed concurrently")

    def test_rolls_back_all_files_when_index_rebuild_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            old = memory_dir / "project_old.md"
            keep = memory_dir / "user_keep.md"
            _write_memory(old, "Old", "project", "old body")
            _write_memory(keep, "Keep", "user", "keep body")
            context = dream.DreamContext(
                project_root=memory_dir,
                memory_dir=memory_dir,
                memory_index="",
                memory_manifest=[],
                sessions=[],
                memory_snapshot=dream._memory_snapshot(memory_dir),
            )
            plan = dream.DreamPlan(
                "change",
                [dream.MemoryDraft(
                    "user_keep.md", "Keep", "updated", "user", "changed body"
                ), dream.MemoryDraft(
                    "project_new.md", "New", "new", "project", "new body"
                )],
                ["project_old.md"],
            )

            with (
                patch.object(dream, "_update_memory_index", side_effect=[OSError("boom"), None]),
                self.assertRaisesRegex(OSError, "boom"),
            ):
                dream.apply_dream_plan(context, plan)

            self.assertTrue(old.exists())
            self.assertEqual(parse_frontmatter(keep.read_text(encoding="utf-8")).body, "keep body")
            self.assertFalse((memory_dir / "project_new.md").exists())


class TestDreamIsolation(unittest.IsolatedAsyncioTestCase):
    def test_read_tools_are_restricted_to_dream_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = (root / "project").resolve()
            memory = (root / "memory").resolve()
            outside = (root / "outside.txt").resolve()
            project.mkdir()
            memory.mkdir()
            outside.write_text("secret", encoding="utf-8")
            agent = dream._DreamAgent.__new__(dream._DreamAgent)
            agent._dream_read_roots = (project, memory)

            safe = agent._safe_read_input("read_file", {"file_path": str(project / "a.py")})
            escaped = agent._safe_read_input("read_file", {"file_path": str(outside)})
            traversal = agent._safe_read_input(
                "list_files", {"path": str(project), "pattern": "../*"}
            )

        self.assertEqual(safe["file_path"], str(project / "a.py"))
        self.assertIsNone(escaped)
        self.assertIsNone(traversal)
        self.assertIsNone(agent._safe_read_input("write_file", {"file_path": "x"}))

    def test_coordinator_exposes_only_read_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = dream.DreamContext(root, root, "", [], [], {})
            parent = SimpleNamespace(
                model="test-model",
                use_openai=True,
                _openai_client=SimpleNamespace(
                    base_url="https://example.test/v1", api_key="test-key"
                ),
            )
            coordinator = dream.DreamCoordinator(parent)

            with patch.object(dream, "_DreamAgent") as factory:
                coordinator._create_agent(context)

        kwargs = factory.call_args.kwargs
        self.assertEqual(
            {tool["name"] for tool in kwargs["custom_tools"]},
            {"read_file", "list_files", "grep_search"},
        )
        self.assertTrue(kwargs["is_sub_agent"])
        self.assertEqual(kwargs["max_turns"], dream.DREAM_MAX_TURNS)

    async def test_coordinator_runs_isolated_agent_once_and_applies_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = dream.DreamContext(
                project_root=root,
                memory_dir=root,
                memory_index="# Memory Index",
                memory_manifest=[{"filename": "project_a.md"}],
                sessions=[{"id": "s1", "messages": []}],
                memory_snapshot={},
            )
            parent = SimpleNamespace(
                total_input_tokens=10,
                total_output_tokens=20,
            )
            child = SimpleNamespace(
                run_once=AsyncMock(return_value={
                    "text": '{"reason":"done","upsert":[],"delete":[]}',
                    "tokens": {"input": 3, "output": 4},
                }),
                close=AsyncMock(),
            )
            coordinator = dream.DreamCoordinator(parent)
            coordinator._create_agent = lambda _: child

            with (
                patch.object(dream, "build_dream_context", return_value=context),
                patch.object(dream, "apply_dream_plan", return_value=dream.DreamResult([], [], [], "done")) as apply,
            ):
                result = await coordinator.run()

        self.assertEqual(result.reason, "done")
        child.run_once.assert_awaited_once()
        child.close.assert_awaited_once()
        self.assertEqual(parent.total_input_tokens, 13)
        self.assertEqual(parent.total_output_tokens, 24)
        apply.assert_called_once()


class TestAgentDreamRefresh(unittest.TestCase):
    def test_refreshes_index_and_invalidates_changed_memory_prefetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            changed_path = str(memory_dir / "project_changed.md")
            pending = SimpleNamespace(settled=False, task=Mock())
            agent = Agent.__new__(Agent)
            agent._memory_prefetch = pending
            agent._already_surfaced_memories = {changed_path, "other.md"}
            agent._dynamic_system_context = "old dynamic"
            agent._static_system_prompt = "static"
            agent._base_system_prompt = "old base"
            agent._system_prompt = "old system"
            agent.use_openai = True
            agent._openai_messages = [{"role": "system", "content": "old system"}]

            with (
                patch("lion_code.agent.get_memory_dir", return_value=memory_dir),
                patch("lion_code.agent.build_dynamic_system_context", return_value="new dynamic"),
            ):
                agent._refresh_memory_context_after_dream(["project_changed.md"])

        pending.task.cancel.assert_called_once()
        self.assertIsNone(agent._memory_prefetch)
        self.assertNotIn(changed_path, agent._already_surfaced_memories)
        self.assertIn("other.md", agent._already_surfaced_memories)
        self.assertEqual(agent._base_system_prompt, "static\n\nnew dynamic")
        self.assertEqual(agent._openai_messages[0]["content"], "static\n\nnew dynamic")


if __name__ == "__main__":
    unittest.main(verbosity=2)
