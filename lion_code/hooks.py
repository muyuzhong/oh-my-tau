"""PreToolUse Command Hook 的配置、信任与隔离执行。"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import os
import shlex
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable


DEFAULT_HOOK_TIMEOUT_MS = 5000
MAX_STDOUT_BYTES = 64 * 1024
MAX_STDERR_BYTES = 16 * 1024
MAX_HOOK_INPUT_BYTES = 256 * 1024
MAX_HOOK_ERROR_BYTES = 4096
SAFE_ENV_NAMES = {
    "PATH",
    "HOME",
    "USERPROFILE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
}
BLOCKED_ENV_NAMES = {
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN",
}
BLOCKED_ENV_PREFIXES = ("AWS_", "AZURE_", "GOOGLE_")

HookConfig = dict[str, Any]
TrustConfirm = Callable[[str], Awaitable[bool]]


class HookSource(str, Enum):
    USER = "user"
    PROJECT = "project"


class HookOutputLimitExceeded(Exception):
    """Hook 输出流超过允许的内存边界。"""

    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(f"Hook output exceeded {limit} bytes")


@dataclass(frozen=True)
class HookTrustDescriptor:
    project_root: str
    hook_id: str
    command: tuple[str, ...]
    config_hash: str
    executable_hash: str | None


def _read_json_object(path: Path, description: str) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid {description} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{description.capitalize()} must contain a JSON object: {path}")
    return value


def _config_hash(entry: dict) -> str:
    encoded = json.dumps(
        entry,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_blocked_env_name(name: str) -> bool:
    normalized = name.upper()
    return normalized in BLOCKED_ENV_NAMES or normalized.startswith(
        BLOCKED_ENV_PREFIXES
    )


def _parse_pass_env(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"Hook pass_env must be a JSON array: {label}")

    names: list[str] = []
    for name in value:
        if (
            not isinstance(name, str)
            or not name
            or "=" in name
            or "\x00" in name
        ):
            raise ValueError(f"Hook pass_env names must be non-empty strings: {label}")
        if _is_blocked_env_name(name):
            raise ValueError(f"Hook pass_env cannot expose secret variable {name!r}: {label}")
        if name not in names:
            names.append(name)
    return tuple(names)


def _parse_command(entry: dict, label: str) -> tuple[tuple[str, ...] | str, bool]:
    shell = entry.get("shell", False)
    command = entry.get("command")
    if not isinstance(shell, bool):
        raise ValueError(f"Hook shell must be a boolean: {label}")

    if shell:
        if not isinstance(command, str) or not command.strip():
            raise ValueError(
                f"Shell Hook command must be a non-empty string: {label}"
            )
        return command, True

    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(arg, str) or not arg for arg in command)
    ):
        raise ValueError(
            f"Hook command must be a non-empty string array unless shell is true: {label}"
        )
    return tuple(command), False


def _command_tokens(command: tuple[str, ...] | str, shell: bool) -> tuple[str, ...]:
    if not shell:
        return command if isinstance(command, tuple) else (command,)
    try:
        return tuple(shlex.split(command, posix=os.name != "nt"))
    except ValueError:
        return ()


def _project_file_hash(
    project_root: Path,
    command: tuple[str, ...] | str,
    shell: bool,
) -> str | None:
    """聚合命令中可解析的项目文件，脚本内容变化会改变信任指纹。"""
    root = project_root.resolve()
    files: list[Path] = []
    for index, raw_token in enumerate(_command_tokens(command, shell)):
        token = raw_token.strip("\"'")
        if not token or token.startswith("-"):
            continue
        if index == 0 and not (
            Path(token).is_absolute()
            or token.startswith(".")
            or "/" in token
            or "\\" in token
        ):
            continue

        candidate = Path(token)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
            if resolved.is_file() and resolved not in files:
                files.append(resolved)
        except (OSError, ValueError):
            continue

    if not files:
        return None

    digest = hashlib.sha256()
    for path in files:
        try:
            content = path.read_bytes()
        except OSError:
            return None
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\x00")
        digest.update(content)
        digest.update(b"\x00")
    return digest.hexdigest()


def describe_project_hook(hook: HookConfig) -> HookTrustDescriptor:
    """根据当前项目文件内容生成可持久化比较的 Hook 信任描述。"""
    project_root = Path(hook["project_root"]).resolve()
    command = hook["command"]
    command_tuple = command if isinstance(command, tuple) else (command,)
    return HookTrustDescriptor(
        project_root=str(project_root),
        hook_id=hook["id"],
        command=command_tuple,
        config_hash=hook["config_hash"],
        executable_hash=_project_file_hash(project_root, command, hook["shell"]),
    )


def load_pre_tool_use_hooks() -> list[HookConfig]:
    """按用户级、项目级顺序加载并严格校验 PreToolUse Hook。"""
    loaded: list[HookConfig] = []
    project_root = Path.cwd().resolve()
    locations = [
        (Path.home() / ".claude" / "settings.json", HookSource.USER),
        (project_root / ".claude" / "settings.json", HookSource.PROJECT),
    ]

    for path, source in locations:
        settings = _read_json_object(path, "settings file")
        hooks = settings.get("hooks", {})
        if not isinstance(hooks, dict):
            raise ValueError(f"hooks must be a JSON object: {path}")
        entries = hooks.get("PreToolUse", [])
        if not isinstance(entries, list):
            raise ValueError(f"hooks.PreToolUse must be a JSON array: {path}")

        seen_ids: set[str] = set()
        for index, entry in enumerate(entries):
            label = f"{path}:hooks.PreToolUse[{index}]"
            if not isinstance(entry, dict):
                raise ValueError(f"Hook must be a JSON object: {label}")

            hook_id = entry.get("id")
            matcher = entry.get("matcher", "*")
            timeout_ms = entry.get("timeout_ms", DEFAULT_HOOK_TIMEOUT_MS)
            if not isinstance(hook_id, str) or not hook_id.strip():
                raise ValueError(f"Hook id must be a non-empty string: {label}")
            if hook_id in seen_ids:
                raise ValueError(f"Hook id must be unique in its settings file: {label}")
            seen_ids.add(hook_id)
            if not isinstance(matcher, str) or not matcher.strip():
                raise ValueError(f"Hook matcher must be a non-empty string: {label}")
            if (
                isinstance(timeout_ms, bool)
                or not isinstance(timeout_ms, (int, float))
                or timeout_ms <= 0
            ):
                raise ValueError(f"Hook timeout_ms must be a positive number: {label}")

            command, shell = _parse_command(entry, label)
            loaded.append(
                {
                    "id": hook_id,
                    "source": source,
                    "matcher": matcher,
                    "command": command,
                    "shell": shell,
                    "timeout_ms": float(timeout_ms),
                    "pass_env": _parse_pass_env(entry.get("pass_env"), label),
                    "project_root": str(project_root),
                    "config_hash": _config_hash(entry),
                    "label": f"{label} ({hook_id})",
                }
            )

    return loaded


def _trust_store_path() -> Path:
    return Path.home() / ".lion-code" / "trusted-hooks.json"


def _read_trust_store() -> dict:
    return _read_json_object(_trust_store_path(), "Hook trust store")


def is_project_hook_trusted(descriptor: HookTrustDescriptor) -> bool:
    project_records = _read_trust_store().get(descriptor.project_root, {})
    if not isinstance(project_records, dict):
        return False
    record = project_records.get(descriptor.hook_id, {})
    return isinstance(record, dict) and (
        record.get("config_hash") == descriptor.config_hash
        and record.get("executable_hash") == descriptor.executable_hash
    )


def trust_project_hook(descriptor: HookTrustDescriptor) -> None:
    """原子写入一条项目 Hook 信任记录。"""
    path = _trust_store_path()
    store = _read_trust_store()
    project_records = store.setdefault(descriptor.project_root, {})
    if not isinstance(project_records, dict):
        raise ValueError(
            f"Hook trust store project record must be a JSON object: {path}"
        )
    project_records[descriptor.hook_id] = {
        "config_hash": descriptor.config_hash,
        "executable_hash": descriptor.executable_hash,
        "trusted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            json.dump(store, temp_file, ensure_ascii=False, indent=2, sort_keys=True)
            temp_file.write("\n")
            temp_path = Path(temp_file.name)
        os.replace(temp_path, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def _format_trust_prompt(hook: HookConfig, descriptor: HookTrustDescriptor) -> str:
    command = hook["command"] if hook["shell"] else list(descriptor.command)
    lines = [
        "Project Hook requires explicit trust.",
        f"Hook: {descriptor.hook_id}",
        f"Project: {descriptor.project_root}",
        f"Command: {json.dumps(command, ensure_ascii=False)}",
        "The Hook will execute repository-provided code before matched tools.",
    ]
    if hook["shell"]:
        lines.append(
            "WARNING: shell=true enables shell parsing, pipelines, redirection, and expansion."
        )
    return "\n".join(lines)


async def _authorize_project_hook(
    hook: HookConfig,
    confirm_trust: TrustConfirm | None,
) -> HookTrustDescriptor | str:
    descriptor = describe_project_hook(hook)
    try:
        if is_project_hook_trusted(descriptor):
            return descriptor
    except ValueError as exc:
        return f"{hook['label']} trust check failed: {exc}"

    if confirm_trust is None:
        return f"{hook['label']} is not trusted; project Hooks require explicit approval"
    try:
        approved = await confirm_trust(_format_trust_prompt(hook, descriptor))
    except Exception as exc:
        return f"{hook['label']} trust prompt failed: {exc}"
    if not approved:
        return f"{hook['label']} was not trusted by the user"

    current_descriptor = describe_project_hook(hook)
    if current_descriptor != descriptor:
        return f"{hook['label']} changed while trust approval was pending"
    try:
        trust_project_hook(descriptor)
    except (OSError, ValueError) as exc:
        return f"{hook['label']} could not save trust: {exc}"
    return descriptor


def _build_hook_env(hook: HookConfig, project_root: Path) -> dict[str, str]:
    env = {name: os.environ[name] for name in SAFE_ENV_NAMES if name in os.environ}
    for name in hook["pass_env"]:
        if name in os.environ:
            env[name] = os.environ[name]
    env.update(
        {
            "LION_HOOK_EVENT": "PreToolUse",
            "LION_PROJECT_ROOT": str(project_root),
            "LION_HOOK_ID": hook["id"],
        }
    )
    return env


async def _kill_and_reap(process: asyncio.subprocess.Process) -> None:
    # Hook 可再启动子进程；超时或取消时必须回收整棵进程树，避免后台残留。
    if os.name == "nt":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        except OSError:
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await process.wait()


async def read_limited(stream: asyncio.StreamReader, limit: int) -> bytes:
    """读取单条 Hook 输出流，超过字节上限时立即失败。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise HookOutputLimitExceeded(limit)
        chunks.append(chunk)


async def _write_payload(stream: asyncio.StreamWriter, payload: bytes) -> None:
    try:
        stream.write(payload)
        await stream.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        stream.close()


async def _abort_hook_tasks(
    process: asyncio.subprocess.Process,
    tasks: tuple[asyncio.Task[Any], ...],
) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await _kill_and_reap(process)
    await asyncio.gather(*tasks, return_exceptions=True)


def _decode_diagnostic(data: bytes) -> str:
    return data[:MAX_HOOK_ERROR_BYTES].decode("utf-8", errors="replace").strip()


async def _run_command_hook(hook: HookConfig, payload: bytes, cwd: Path) -> str | None:
    label = hook["label"]
    if len(payload) > MAX_HOOK_INPUT_BYTES:
        return f"{label} exceeded Hook input limit of {MAX_HOOK_INPUT_BYTES} bytes"

    spawn_options = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    process_options = {
        "stdin": asyncio.subprocess.PIPE,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
        "cwd": str(cwd),
        "env": _build_hook_env(hook, cwd),
        **spawn_options,
    }
    try:
        if hook["shell"]:
            process = await asyncio.create_subprocess_shell(
                hook["command"],
                **process_options,
            )
        else:
            process = await asyncio.create_subprocess_exec(
                *hook["command"],
                **process_options,
            )
    except Exception as exc:
        return f"{label} failed to start: {exc}"

    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_task = asyncio.create_task(read_limited(process.stdout, MAX_STDOUT_BYTES))
    stderr_task = asyncio.create_task(read_limited(process.stderr, MAX_STDERR_BYTES))
    stdin_task = asyncio.create_task(_write_payload(process.stdin, payload))
    wait_task = asyncio.create_task(process.wait())
    tasks = (stdout_task, stderr_task, stdin_task, wait_task)

    try:
        async with asyncio.timeout(hook["timeout_ms"] / 1000):
            stdout, stderr, _, _ = await asyncio.gather(*tasks)
    except TimeoutError:
        await _abort_hook_tasks(process, tasks)
        return f"{label} timed out after {hook['timeout_ms']:g}ms"
    except HookOutputLimitExceeded as exc:
        await _abort_hook_tasks(process, tasks)
        return f"{label} exceeded Hook output limit of {exc.limit} bytes"
    except asyncio.CancelledError:
        await _abort_hook_tasks(process, tasks)
        raise
    except Exception as exc:
        await _abort_hook_tasks(process, tasks)
        return f"{label} failed during I/O: {exc}"

    if process.returncode != 0:
        detail = _decode_diagnostic(stderr)
        suffix = f": {detail}" if detail else ""
        return f"{label} exited with code {process.returncode}{suffix}"
    try:
        result = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return f"{label} returned invalid JSON: {exc}"
    if not isinstance(result, dict):
        return f"{label} must return a JSON object"

    action = result.get("action")
    if action == "allow":
        return None
    if action == "deny":
        reason = result.get("reason")
        return (
            reason
            if isinstance(reason, str) and reason.strip()
            else f"{label} denied the tool call"
        )
    return f"{label} returned unsupported action: {action!r}"


async def run_pre_tool_use_hooks(
    hooks: list[HookConfig],
    tool_name: str,
    tool_input: dict,
    *,
    confirm_trust: TrustConfirm | None = None,
) -> str | None:
    """顺序执行匹配 Hook；返回拒绝原因，全部放行时返回 None。"""
    cwd = Path.cwd().resolve()
    payload = json.dumps(
        {
            "event": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": tool_input,
            "cwd": str(cwd),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    for hook in hooks:
        if not fnmatch.fnmatchcase(tool_name, hook["matcher"]):
            continue
        if hook["source"] is HookSource.PROJECT:
            if hook["project_root"] != str(cwd):
                return f"{hook['label']} belongs to a different project root"
            authorization = await _authorize_project_hook(hook, confirm_trust)
            if isinstance(authorization, str):
                return authorization
            if describe_project_hook(hook) != authorization:
                return f"{hook['label']} changed after trust approval"

        denial = await _run_command_hook(hook, payload, cwd)
        if denial is not None:
            return denial
    return None
