"""PreToolUse Hook 的配置、信任、环境与执行边界测试。"""

import asyncio
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from lion_code import hooks as hook_module
from lion_code.agent import Agent
from lion_code.hooks import (
    HookChainResult,
    HookErrorKind,
    HookOutcome,
    HookResult,
    HookSource,
    describe_project_hook,
    is_project_hook_trusted,
    load_pre_tool_use_hooks,
    run_pre_tool_use_hooks,
    trust_project_hook,
)


def _python_shell_command(script: Path) -> str:
    args = [sys.executable, str(script)]
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


def _write_settings(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"hooks": {"PreToolUse": entries}}, ensure_ascii=False),
        encoding="utf-8",
    )


def _hook(
    command: list[str] | tuple[str, ...] | str,
    *,
    matcher: str = "*",
    timeout_ms: float = 1000,
    hook_id: str = "test-hook",
    source: HookSource = HookSource.USER,
    shell: bool = False,
    pass_env: tuple[str, ...] = (),
    project_root: Path | None = None,
) -> dict:
    normalized_command = command if shell else tuple(command)
    root = (project_root or hook_module.Path.cwd()).resolve()
    return {
        "id": hook_id,
        "source": source,
        "matcher": matcher,
        "command": normalized_command,
        "shell": shell,
        "timeout_ms": timeout_ms,
        "pass_env": pass_env,
        "project_root": str(root),
        "config_hash": "test-config",
        "label": hook_id,
    }


class TestHookConfig(unittest.TestCase):
    def test_loads_user_hooks_before_project_hooks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            project = root / "project"
            _write_settings(
                home / ".claude" / "settings.json",
                [
                    {
                        "id": "user-policy",
                        "matcher": "run_*",
                        "command": ["user-hook", "--json"],
                    }
                ],
            )
            _write_settings(
                project / ".claude" / "settings.json",
                [
                    {
                        "id": "project-policy",
                        "matcher": "write_file",
                        "command": ["project-hook"],
                        "timeout_ms": 250,
                        "pass_env": ["POLICY_CONFIG_PATH"],
                    }
                ],
            )

            with (
                patch.object(hook_module.Path, "home", return_value=home),
                patch.object(hook_module.Path, "cwd", return_value=project),
            ):
                loaded = load_pre_tool_use_hooks()

        self.assertEqual(
            [hook["command"] for hook in loaded],
            [("user-hook", "--json"), ("project-hook",)],
        )
        self.assertEqual(
            [hook["source"] for hook in loaded],
            [HookSource.USER, HookSource.PROJECT],
        )
        self.assertEqual(loaded[0]["timeout_ms"], 5000.0)
        self.assertEqual(loaded[1]["timeout_ms"], 250.0)
        self.assertEqual(loaded[1]["pass_env"], ("POLICY_CONFIG_PATH",))

    def test_requires_array_command_unless_shell_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = root / ".claude" / "settings.json"
            _write_settings(
                settings,
                [{"id": "legacy", "matcher": "run_shell", "command": "echo ok"}],
            )

            with (
                patch.object(hook_module.Path, "home", return_value=root / "home"),
                patch.object(hook_module.Path, "cwd", return_value=root),
            ):
                with self.assertRaisesRegex(ValueError, "string array"):
                    load_pre_tool_use_hooks()

                _write_settings(
                    settings,
                    [
                        {
                            "id": "legacy",
                            "matcher": "run_shell",
                            "shell": True,
                            "command": "echo ok",
                        }
                    ],
                )
                loaded = load_pre_tool_use_hooks()

        self.assertTrue(loaded[0]["shell"])
        self.assertEqual(loaded[0]["command"], "echo ok")

    def test_rejects_secret_environment_passthrough(self):
        blocked_names = [
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GITHUB_TOKEN",
            "AWS_SECRET_ACCESS_KEY",
            "AZURE_OPENAI_API_KEY",
            "GOOGLE_APPLICATION_CREDENTIALS",
        ]
        for name in blocked_names:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                _write_settings(
                    root / ".claude" / "settings.json",
                    [
                        {
                            "id": "policy",
                            "command": ["policy-hook"],
                            "pass_env": [name],
                        }
                    ],
                )
                with (
                    patch.object(hook_module.Path, "home", return_value=root / "home"),
                    patch.object(hook_module.Path, "cwd", return_value=root),
                ):
                    with self.assertRaisesRegex(ValueError, "cannot expose secret"):
                        load_pre_tool_use_hooks()


class TestCommandHooks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name).resolve()
        self.home = self.root / "home"
        self._cwd_patch = patch.object(hook_module.Path, "cwd", return_value=self.root)
        self._home_patch = patch.object(
            hook_module.Path, "home", return_value=self.home
        )
        self._cwd_patch.start()
        self._home_patch.start()

    def tearDown(self):
        self._home_patch.stop()
        self._cwd_patch.stop()
        self._temp_dir.cleanup()

    def _write_script(self, name: str, source: str) -> Path:
        script = self.root / name
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(source, encoding="utf-8")
        return script

    def _user_python_hook(self, script: Path, **kwargs) -> dict:
        return _hook([sys.executable, str(script)], **kwargs)

    def _write_project_hook(
        self,
        script_source: str = 'print("{\\"action\\":\\"allow\\"}")',
        **entry_overrides,
    ) -> tuple[Path, dict]:
        script = self._write_script(".claude/hooks/policy.py", script_source)
        entry = {
            "id": "project-policy",
            "matcher": "run_shell",
            "command": [sys.executable, ".claude/hooks/policy.py"],
            **entry_overrides,
        }
        _write_settings(self.root / ".claude" / "settings.json", [entry])
        project_hook = next(
            hook
            for hook in load_pre_tool_use_hooks()
            if hook["source"] is HookSource.PROJECT
        )
        return script, project_hook

    async def test_allow_hook_receives_utf8_payload_without_shell(self):
        script = self._write_script(
            "allow.py",
            """import json
from pathlib import Path
import sys

payload = json.load(sys.stdin.buffer)
Path("payload.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
print(json.dumps({"action": "allow"}))
""",
        )

        chain = await run_pre_tool_use_hooks(
            [self._user_python_hook(script, matcher="run_*")],
            "run_shell",
            {"command": "echo 你好"},
        )

        self.assertEqual(chain.outcome, HookOutcome.ALLOW)
        self.assertIsNone(chain.terminal_result)
        self.assertEqual(len(chain.executed), 1)
        self.assertEqual(chain.executed[0].hook_id, "test-hook")
        self.assertEqual(chain.executed[0].outcome, HookOutcome.ALLOW)
        self.assertGreaterEqual(chain.executed[0].duration_ms, 0)
        payload = json.loads((self.root / "payload.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["event"], "PreToolUse")
        self.assertEqual(payload["tool_name"], "run_shell")
        self.assertEqual(payload["tool_input"], {"command": "echo 你好"})
        self.assertEqual(payload["cwd"], str(self.root))

    async def test_exec_preserves_shell_metacharacters_as_arguments(self):
        script = self._write_script(
            "argv.py",
            """import json
from pathlib import Path
import sys

Path("argv.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
print(json.dumps({"action": "allow"}))
""",
        )
        hook = _hook([sys.executable, str(script), "value | not-a-pipe"])

        chain = await run_pre_tool_use_hooks([hook], "run_shell", {})

        self.assertEqual(chain.outcome, HookOutcome.ALLOW)
        argv = json.loads((self.root / "argv.json").read_text(encoding="utf-8"))
        self.assertEqual(argv, ["value | not-a-pipe"])

    async def test_explicit_shell_hook_is_supported(self):
        script = self._write_script(
            "shell.py", 'print("{\\"action\\":\\"allow\\"}")'
        )
        hook = _hook(_python_shell_command(script), shell=True)

        chain = await run_pre_tool_use_hooks([hook], "run_shell", {})

        self.assertEqual(chain.outcome, HookOutcome.ALLOW)

    async def test_hook_receives_only_safe_and_explicit_environment(self):
        script = self._write_script(
            "env.py",
            """import json
import os
from pathlib import Path

Path("env.json").write_text(json.dumps(dict(os.environ)), encoding="utf-8")
print(json.dumps({"action": "allow"}))
""",
        )
        hook = self._user_python_hook(script, pass_env=("POLICY_CONFIG_PATH",))
        injected = {
            "OPENAI_API_KEY": "openai-secret",
            "ANTHROPIC_API_KEY": "anthropic-secret",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            "UNLISTED_VALUE": "hidden",
            "POLICY_CONFIG_PATH": "policy.json",
        }

        with patch.dict(os.environ, injected):
            chain = await run_pre_tool_use_hooks([hook], "run_shell", {})

        self.assertEqual(chain.outcome, HookOutcome.ALLOW)
        child_env = json.loads((self.root / "env.json").read_text(encoding="utf-8"))
        self.assertNotIn("OPENAI_API_KEY", child_env)
        self.assertNotIn("ANTHROPIC_API_KEY", child_env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", child_env)
        self.assertNotIn("UNLISTED_VALUE", child_env)
        self.assertEqual(child_env["POLICY_CONFIG_PATH"], "policy.json")
        self.assertEqual(child_env["LION_HOOK_EVENT"], "PreToolUse")
        self.assertEqual(child_env["LION_PROJECT_ROOT"], str(self.root))
        self.assertEqual(child_env["LION_HOOK_ID"], "test-hook")

    async def test_non_matching_hook_is_not_started(self):
        hook = _hook(["command-that-does-not-exist"], matcher="write_file")
        chain = await run_pre_tool_use_hooks(
            [hook], "run_shell", {"command": "echo hi"}
        )
        self.assertEqual(chain.outcome, HookOutcome.ALLOW)
        self.assertEqual(chain.executed, ())

    async def test_deny_stops_later_hooks(self):
        allow = self._write_script(
            "allow_first.py",
            'print("{\\"action\\":\\"allow\\"}")',
        )
        deny = self._write_script(
            "deny.py",
            'print("{\\"action\\":\\"deny\\",\\"reason\\":\\"blocked\\"}")',
        )
        later = self._write_script(
            "later.py",
            'from pathlib import Path\nPath("later-ran").touch()\nprint("{\\"action\\":\\"allow\\"}")',
        )

        chain = await run_pre_tool_use_hooks(
            [
                self._user_python_hook(allow, hook_id="allow"),
                self._user_python_hook(deny, hook_id="deny"),
                self._user_python_hook(later, hook_id="later"),
            ],
            "run_shell",
            {"command": "echo hi"},
        )

        self.assertEqual(chain.outcome, HookOutcome.DENY)
        self.assertEqual(chain.terminal_result.reason, "blocked")
        self.assertIsNone(chain.terminal_result.error_kind)
        self.assertEqual([item.hook_id for item in chain.executed], ["allow", "deny"])
        self.assertIs(chain.terminal_result, chain.executed[-1])
        self.assertFalse((self.root / "later-ran").exists())

    async def test_process_failures_are_structured_errors(self):
        invalid_json = self._write_script("invalid.py", 'print("not json")')
        nonzero = self._write_script(
            "nonzero.py",
            'import sys\nsys.stderr.write("boom")\nsys.exit(7)',
        )

        invalid_chain = await run_pre_tool_use_hooks(
            [
                self._user_python_hook(invalid_json, hook_id="invalid"),
                _hook(["command-that-must-not-run"], hook_id="later"),
            ],
            "run_shell",
            {},
        )
        nonzero_chain = await run_pre_tool_use_hooks(
            [self._user_python_hook(nonzero)], "run_shell", {}
        )

        self.assertEqual(invalid_chain.outcome, HookOutcome.ERROR)
        self.assertEqual(
            invalid_chain.terminal_result.error_kind,
            HookErrorKind.INVALID_OUTPUT,
        )
        self.assertIn("invalid JSON", invalid_chain.terminal_result.reason)
        self.assertEqual([item.hook_id for item in invalid_chain.executed], ["invalid"])

        self.assertEqual(nonzero_chain.outcome, HookOutcome.ERROR)
        self.assertEqual(
            nonzero_chain.terminal_result.error_kind,
            HookErrorKind.NONZERO_EXIT,
        )
        self.assertEqual(nonzero_chain.terminal_result.exit_code, 7)
        self.assertIn("exited with code 7: boom", nonzero_chain.terminal_result.reason)

    async def test_spawn_failure_is_a_structured_error(self):
        chain = await run_pre_tool_use_hooks(
            [_hook(["command-that-does-not-exist"])], "run_shell", {}
        )

        self.assertEqual(chain.outcome, HookOutcome.ERROR)
        self.assertEqual(chain.terminal_result.error_kind, HookErrorKind.SPAWN_ERROR)
        self.assertIn("failed to start", chain.terminal_result.reason)
        self.assertEqual(chain.executed, (chain.terminal_result,))

    async def test_project_hook_requires_and_persists_explicit_trust(self):
        script, hook = self._write_project_hook(
            """import json
from pathlib import Path

Path("project-hook-ran").touch()
print(json.dumps({"action": "allow"}))
"""
        )

        untrusted_chain = await run_pre_tool_use_hooks([hook], "run_shell", {})
        self.assertEqual(untrusted_chain.outcome, HookOutcome.DENY)
        self.assertEqual(untrusted_chain.terminal_result.hook_id, "project-policy")
        self.assertIn("is not trusted", untrusted_chain.terminal_result.reason)
        self.assertIsNone(untrusted_chain.terminal_result.error_kind)
        self.assertEqual(untrusted_chain.executed, ())
        self.assertFalse((self.root / "project-hook-ran").exists())

        confirm = AsyncMock(return_value=True)
        approved_chain = await run_pre_tool_use_hooks(
            [hook], "run_shell", {}, confirm_trust=confirm
        )
        self.assertEqual(approved_chain.outcome, HookOutcome.ALLOW)
        self.assertEqual(len(approved_chain.executed), 1)
        confirm.assert_awaited_once()
        self.assertTrue((self.root / "project-hook-ran").exists())

        trust_store = json.loads(
            (self.home / ".lion-code" / "trusted-hooks.json").read_text(
                encoding="utf-8"
            )
        )
        record = trust_store[str(self.root)]["project-policy"]
        self.assertIn("config_hash", record)
        self.assertIn("executable_hash", record)
        self.assertIn("trusted_at", record)

        already_trusted = AsyncMock(return_value=False)
        trusted_chain = await run_pre_tool_use_hooks(
            [hook], "run_shell", {}, confirm_trust=already_trusted
        )
        self.assertEqual(trusted_chain.outcome, HookOutcome.ALLOW)
        already_trusted.assert_not_awaited()
        self.assertTrue(script.is_file())

    async def test_shell_project_hook_shows_extra_risk_warning(self):
        _, hook = self._write_project_hook(
            shell=True,
            command="command-that-must-not-run | another-command",
        )
        confirm = AsyncMock(return_value=False)

        chain = await run_pre_tool_use_hooks(
            [hook], "run_shell", {}, confirm_trust=confirm
        )

        self.assertEqual(chain.outcome, HookOutcome.DENY)
        self.assertIn("was not trusted", chain.terminal_result.reason)
        prompt = confirm.await_args.args[0]
        self.assertIn("WARNING: shell=true", prompt)

    async def test_trust_prompt_failure_is_an_infrastructure_error(self):
        _, hook = self._write_project_hook()
        confirm = AsyncMock(side_effect=RuntimeError("prompt unavailable"))

        chain = await run_pre_tool_use_hooks(
            [hook], "run_shell", {}, confirm_trust=confirm
        )

        self.assertEqual(chain.outcome, HookOutcome.ERROR)
        self.assertEqual(chain.terminal_result.error_kind, HookErrorKind.TRUST_ERROR)
        self.assertIn("prompt unavailable", chain.terminal_result.reason)
        self.assertEqual(chain.executed, ())

    def test_trust_invalidates_on_command_config_script_or_root_change(self):
        script, original_hook = self._write_project_hook()
        original = describe_project_hook(original_hook)
        trust_project_hook(original)
        self.assertTrue(is_project_hook_trusted(original))

        _, command_changed_hook = self._write_project_hook(
            command=[sys.executable, "-u", ".claude/hooks/policy.py"]
        )
        self.assertFalse(
            is_project_hook_trusted(describe_project_hook(command_changed_hook))
        )

        _, config_changed_hook = self._write_project_hook(timeout_ms=250)
        self.assertFalse(
            is_project_hook_trusted(describe_project_hook(config_changed_hook))
        )

        _, restored_hook = self._write_project_hook()
        script.write_text(
            'print("{\\"action\\":\\"deny\\",\\"reason\\":\\"changed\\"}")',
            encoding="utf-8",
        )
        self.assertFalse(is_project_hook_trusted(describe_project_hook(restored_hook)))

        second_root = self.root / "second-project"
        second_script = second_root / ".claude" / "hooks" / "policy.py"
        second_script.parent.mkdir(parents=True)
        second_script.write_text(
            'print("{\\"action\\":\\"allow\\"}")', encoding="utf-8"
        )
        _write_settings(
            second_root / ".claude" / "settings.json",
            [
                {
                    "id": "project-policy",
                    "matcher": "run_shell",
                    "command": [sys.executable, ".claude/hooks/policy.py"],
                }
            ],
        )
        with patch.object(hook_module.Path, "cwd", return_value=second_root):
            second_hook = next(
                hook
                for hook in load_pre_tool_use_hooks()
                if hook["source"] is HookSource.PROJECT
            )
        second_descriptor = describe_project_hook(second_hook)
        self.assertNotEqual(original.project_root, second_descriptor.project_root)
        self.assertFalse(is_project_hook_trusted(second_descriptor))

    async def test_input_limit_rejects_hook_before_process_start(self):
        script = self._write_script(
            "input_limit.py",
            """from pathlib import Path

Path("input-limit-hook-ran").touch()
print('{"action":"allow"}')
""",
        )

        chain = await run_pre_tool_use_hooks(
            [self._user_python_hook(script)],
            "run_shell",
            {"command": "x" * hook_module.MAX_HOOK_INPUT_BYTES},
        )

        self.assertEqual(chain.outcome, HookOutcome.ERROR)
        self.assertEqual(chain.terminal_result.error_kind, HookErrorKind.INPUT_LIMIT)
        self.assertIn("input exceeded 262144 bytes", chain.terminal_result.reason)
        self.assertFalse((self.root / "input-limit-hook-ran").exists())

    async def test_stdout_limit_aborts_hook_promptly(self):
        script = self._write_script(
            "large_stdout.py",
            f"""import sys
import time

sys.stdout.buffer.write(b"x" * {hook_module.MAX_STDOUT_BYTES + 8192})
sys.stdout.buffer.flush()
time.sleep(10)
""",
        )
        started = time.monotonic()

        with patch.object(
            hook_module,
            "_kill_and_reap",
            wraps=hook_module._kill_and_reap,
        ) as kill_and_reap:
            chain = await run_pre_tool_use_hooks(
                [self._user_python_hook(script, timeout_ms=5000)], "run_shell", {}
            )

        self.assertEqual(chain.outcome, HookOutcome.ERROR)
        self.assertEqual(chain.terminal_result.error_kind, HookErrorKind.OUTPUT_LIMIT)
        self.assertIn("output exceeded 65536 bytes", chain.terminal_result.reason)
        kill_and_reap.assert_awaited_once()
        self.assertLess(time.monotonic() - started, 2)

    async def test_stderr_has_independent_output_limit(self):
        script = self._write_script(
            "large_stderr.py",
            f"""import sys
import time

sys.stderr.buffer.write(b"x" * {hook_module.MAX_STDERR_BYTES + 8192})
sys.stderr.buffer.flush()
time.sleep(10)
""",
        )

        chain = await run_pre_tool_use_hooks(
            [self._user_python_hook(script, timeout_ms=5000)], "run_shell", {}
        )

        self.assertEqual(chain.outcome, HookOutcome.ERROR)
        self.assertEqual(chain.terminal_result.error_kind, HookErrorKind.OUTPUT_LIMIT)
        self.assertIn("output exceeded 16384 bytes", chain.terminal_result.reason)

    async def test_stream_read_failure_is_structured_without_escaping(self):
        script = self._write_script("read_failure.py", "import time\ntime.sleep(10)\n")

        with patch.object(
            hook_module,
            "read_limited",
            AsyncMock(side_effect=OSError("read failed")),
        ):
            chain = await run_pre_tool_use_hooks(
                [self._user_python_hook(script, timeout_ms=5000)], "run_shell", {}
            )

        self.assertEqual(chain.outcome, HookOutcome.ERROR)
        self.assertEqual(chain.terminal_result.error_kind, HookErrorKind.IO_ERROR)
        self.assertIn("I/O failed: read failed", chain.terminal_result.reason)

    async def test_timeout_kills_process_tree_promptly(self):
        script = self._write_script("sleep.py", "import time\ntime.sleep(10)\n")
        started = time.monotonic()

        chain = await run_pre_tool_use_hooks(
            [self._user_python_hook(script, timeout_ms=50)], "run_shell", {}
        )

        self.assertEqual(chain.outcome, HookOutcome.ERROR)
        self.assertEqual(chain.terminal_result.error_kind, HookErrorKind.TIMEOUT)
        self.assertEqual(chain.terminal_result.reason, "Hook timed out after 50ms")
        self.assertLess(time.monotonic() - started, 2)

    async def test_cancellation_reaps_process_tree(self):
        script = self._write_script("cancel.py", "import time\ntime.sleep(10)\n")
        task = asyncio.create_task(
            run_pre_tool_use_hooks(
                [self._user_python_hook(script, timeout_ms=5000)],
                "run_shell",
                {},
            )
        )
        await asyncio.sleep(0.1)
        started = time.monotonic()

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertLess(time.monotonic() - started, 2)


class TestAgentHookIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_hook_denial_stops_tool_router_and_passes_trust_callback(self):
        with patch("lion_code.agent.load_pre_tool_use_hooks", return_value=[]):
            agent = Agent(api_key="test-key")

        terminal = HookResult(
            hook_id="protect-shell",
            outcome=HookOutcome.DENY,
            reason="blocked by policy",
        )
        hook_runner = AsyncMock(
            return_value=HookChainResult(
                outcome=HookOutcome.DENY,
                terminal_result=terminal,
                executed=(terminal,),
            )
        )
        tool_runner = AsyncMock(return_value="executed")
        with (
            patch("lion_code.agent.run_pre_tool_use_hooks", hook_runner),
            patch("lion_code.agent.execute_tool", tool_runner),
        ):
            result = await agent._execute_tool_call("run_shell", {"command": "echo hi"})

        self.assertEqual(
            result,
            'Action denied by hook "protect-shell":\n'
            "blocked by policy\n\n"
            "A configured policy rejected this action. Adjust the action.",
        )
        hook_runner.assert_awaited_once()
        self.assertIs(hook_runner.await_args.kwargs["confirm_trust"].__self__, agent)
        tool_runner.assert_not_awaited()

    async def test_hook_error_is_not_presented_as_user_policy(self):
        with patch("lion_code.agent.load_pre_tool_use_hooks", return_value=[]):
            agent = Agent(api_key="test-key")

        terminal = HookResult(
            hook_id="protect-shell",
            outcome=HookOutcome.ERROR,
            reason="Hook timed out after 5000ms",
            error_kind=HookErrorKind.TIMEOUT,
        )
        hook_runner = AsyncMock(
            return_value=HookChainResult(
                outcome=HookOutcome.ERROR,
                terminal_result=terminal,
                executed=(terminal,),
            )
        )
        tool_runner = AsyncMock(return_value="executed")
        with (
            patch("lion_code.agent.run_pre_tool_use_hooks", hook_runner),
            patch("lion_code.agent.execute_tool", tool_runner),
        ):
            result = await agent._execute_tool_call("run_shell", {"command": "echo hi"})

        self.assertEqual(
            result,
            'Tool call blocked because hook "protect-shell" failed:\n'
            "Hook timed out after 5000ms\n\n"
            "The hook system failed. Do not interpret this as user intent.",
        )
        tool_runner.assert_not_awaited()

    async def test_dont_ask_mode_rejects_hook_trust_without_prompt(self):
        with patch("lion_code.agent.load_pre_tool_use_hooks", return_value=[]):
            agent = Agent(api_key="test-key", permission_mode="dontAsk")
        dangerous_prompt = AsyncMock(return_value=True)

        with patch.object(agent, "_confirm_dangerous", dangerous_prompt):
            approved = await agent._confirm_hook_trust("trust this Hook")

        self.assertFalse(approved)
        dangerous_prompt.assert_not_awaited()


if __name__ == "__main__":
    unittest.main(verbosity=2)
