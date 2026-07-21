"""自包含的 /goal、/loop 和 Auto Mode 纯逻辑测试。"""

import unittest

from lion_code import autonomy as a


class TestAutonomy(unittest.TestCase):
    def test_parse_duration_to_seconds(self):
        self.assertEqual(a.parse_duration_to_seconds("5m"), 300)
        self.assertEqual(a.parse_duration_to_seconds("2h"), 7200)
        self.assertIsNone(a.parse_duration_to_seconds("later"))

    def test_clamp_wakeup_delay(self):
        self.assertEqual(a.clamp_wakeup_delay(1), 60)
        self.assertEqual(a.clamp_wakeup_delay(60.5), 61)
        self.assertEqual(a.clamp_wakeup_delay(4000), 3600)

    def test_daily_wording(self):
        self.assertTrue(a.is_daily_wording("every morning"))
        self.assertFalse(a.is_daily_wording("every 5 minutes"))

    def test_parse_goal_verdict(self):
        self.assertEqual(
            a.parse_goal_verdict('{"ok": true, "reason": "done"}'),
            {"ok": True, "reason": "done", "impossible": False},
        )
        self.assertFalse(a.parse_goal_verdict("not json")["ok"])

    def test_parse_block_verdict(self):
        self.assertEqual(a.parse_block_verdict("<block>no</block>")["block"], False)
        self.assertEqual(
            a.parse_block_verdict("<block>yes</block><reason>unsafe</reason>"),
            {"block": True, "reason": "unsafe"},
        )
        self.assertTrue(a.parse_block_verdict("malformed")["block"])

    def test_parse_loop_input(self):
        self.assertEqual(
            a.parse_loop_input("5m run tests"),
            {
                "mode": "interval",
                "prompt": "run tests",
                "interval_seconds": 300,
                "interval_label": "5m",
            },
        )
        self.assertEqual(
            a.parse_loop_input("run tests every 2 hours"),
            {
                "mode": "interval",
                "prompt": "run tests",
                "interval_seconds": 7200,
                "interval_label": "2h",
            },
        )
        self.assertEqual(
            a.parse_loop_input("inspect the repository"),
            {"mode": "dynamic", "prompt": "inspect the repository"},
        )
        self.assertEqual(
            a.parse_loop_input("")["error"],
            "usage: /loop [interval] <prompt>",
        )

    def test_build_classifier_transcript(self):
        history = [
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "read_file",
                        "input": {"file_path": "a.txt"},
                    }
                ],
            },
        ]
        result = a.build_classifier_transcript(
            history,
            {"tool_name": "run_shell", "input": {"command": "echo hi"}},
        )
        self.assertEqual(result.splitlines()[0], '{"user":"hello"}')
        self.assertIn('"read_file":"', result)
        self.assertIn('"run_shell":"echo hi"', result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
