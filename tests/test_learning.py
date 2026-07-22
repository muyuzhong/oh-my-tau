"""显式 `/learn` 会话经验沉淀闭环测试。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from lion_code import skills
from lion_code.agent import Agent, LEARN_META_SKILL_PROMPT


class TestCreateSkill(unittest.TestCase):
    def test_create_project_skill_and_reject_overwrite(self):
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                skills._cached_skills = []
                content = "---\nname: learned-flow\ndescription: reusable flow\n---\n\nFollow it."

                result = skills.create_skill("learned-flow", content)

                skill_path = Path(tmp) / ".claude" / "skills" / "learned-flow" / "SKILL.md"
                self.assertEqual(result, f"Skill created: {skill_path}")
                self.assertEqual(skill_path.read_text(encoding="utf-8"), content)
                self.assertIsNone(skills._cached_skills)
                self.assertEqual(
                    skills.get_skill_by_name("learned-flow").name,
                    "learned-flow",
                )
                self.assertEqual(
                    skills.create_skill("learned-flow", content),
                    "Skill already exists",
                )
                self.assertEqual(
                    skills.create_skill("Invalid_Name", content),
                    "Invalid skill name",
                )
            finally:
                os.chdir(original_cwd)
                skills.reset_skill_cache()


class TestLearnFromSession(unittest.IsolatedAsyncioTestCase):
    async def test_create_decision_uses_one_meta_skill_call(self):
        agent = Agent.__new__(Agent)
        agent.use_openai = True
        agent._openai_messages = [
            {"role": "system", "content": "ordinary prompt"},
            {"role": "user", "content": "fix the build"},
            {"role": "assistant", "content": "fixed and verified"},
        ]
        agent._anthropic_messages = []
        decision = {
            "create": True,
            "reason": "reusable",
            "scope": "project",
            "name": "fix-build",
            "content": "---\nname: fix-build\ndescription: fix build\n---\n\nRun checks.",
        }
        agent._run_evaluator_query = AsyncMock(
            return_value=f"```json\n{json.dumps(decision)}\n```"
        )

        with patch("lion_code.agent.create_skill", return_value="Skill created") as create:
            result = await agent.learn_from_current_session()

        self.assertEqual(result, "Skill created")
        agent._run_evaluator_query.assert_awaited_once()
        system, messages = agent._run_evaluator_query.await_args.args
        self.assertEqual(system, LEARN_META_SKILL_PROMPT)
        self.assertNotIn("ordinary prompt", messages[0]["content"])
        self.assertIn("fix the build", messages[0]["content"])
        self.assertEqual(agent._run_evaluator_query.await_args.kwargs["max_tokens"], 4096)
        create.assert_called_once_with(
            name="fix-build",
            content=decision["content"],
            scope="project",
        )

    async def test_rejected_decision_does_not_write(self):
        agent = Agent.__new__(Agent)
        agent.use_openai = False
        agent._openai_messages = []
        agent._anthropic_messages = [{"role": "user", "content": "hello"}]
        agent._run_evaluator_query = AsyncMock(
            return_value='{"create": false, "reason": "only small talk"}'
        )

        with patch("lion_code.agent.create_skill") as create:
            result = await agent.learn_from_current_session()

        self.assertEqual(result, "不建议沉淀：only small talk")
        create.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
