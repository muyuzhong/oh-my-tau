"""基于 ToolCapabilities 的权限规则与危险操作判定。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .types import JSONValue, LionTool


DANGEROUS_PATTERNS = (
    re.compile(r"\brm\s"),
    re.compile(r"\bgit\s+(push|reset|clean|checkout\s+\.)"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s"),
    re.compile(r">\s*/dev/"),
    re.compile(r"\bkill\b"),
    re.compile(r"\bpkill\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bshutdown\b"),
    re.compile(r"\bdel\s", re.IGNORECASE),
    re.compile(r"\brmdir\s", re.IGNORECASE),
    re.compile(r"\bformat\s", re.IGNORECASE),
    re.compile(r"\btaskkill\s", re.IGNORECASE),
    re.compile(r"\bRemove-Item\s", re.IGNORECASE),
    re.compile(r"\bStop-Process\s", re.IGNORECASE),
)


def is_dangerous(command: str) -> bool:
    """返回命令是否命中现有危险命令规则。"""
    return any(pattern.search(command) for pattern in DANGEROUS_PATTERNS)


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    action: Literal["allow", "deny", "confirm"]
    message: str = ""


_cached_rules: dict[tuple[str, str], dict[str, list[dict[str, str | None]]]] = {}


def reset_permission_cache() -> None:
    """清空权限配置缓存，供配置刷新和测试使用。"""
    _cached_rules.clear()


def _parse_rule(rule: str) -> dict[str, str | None]:
    match = re.match(r"^([a-z_]+)\((.+)\)$", rule)
    if match:
        return {"tool": match.group(1), "pattern": match.group(2)}
    return {"tool": rule, "pattern": None}


def _load_settings(file_path: Path) -> dict | None:
    if not file_path.exists():
        return None
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def load_permission_rules(home: Path, cwd: Path) -> dict[str, list[dict]]:
    """按用户级后项目级顺序加载兼容 `.claude/settings.json` 规则。"""
    key = (str(home.resolve()), str(cwd.resolve()))
    cached = _cached_rules.get(key)
    if cached is not None:
        return cached

    allow: list[dict] = []
    deny: list[dict] = []
    for settings_path in (
        home / ".claude" / "settings.json",
        cwd / ".claude" / "settings.json",
    ):
        settings = _load_settings(settings_path)
        if not settings or not isinstance(settings.get("permissions"), dict):
            continue
        permissions = settings["permissions"]
        allow.extend(
            _parse_rule(rule)
            for rule in permissions.get("allow", [])
            if isinstance(rule, str)
        )
        deny.extend(
            _parse_rule(rule)
            for rule in permissions.get("deny", [])
            if isinstance(rule, str)
        )

    rules = {"allow": allow, "deny": deny}
    _cached_rules[key] = rules
    return rules


def _matches_rule(
    rule: Mapping[str, str | None],
    tool: LionTool,
    arguments: Mapping[str, JSONValue],
) -> bool:
    if rule["tool"] != tool.name:
        return False
    pattern = rule["pattern"]
    if pattern is None:
        return True

    if "command" in arguments:
        value = str(arguments.get("command", ""))
    elif "file_path" in arguments:
        value = str(arguments.get("file_path", ""))
    else:
        return True

    if pattern.endswith("*"):
        return value.startswith(pattern[:-1])
    return value == pattern


def _same_path(first: str, second: str) -> bool:
    try:
        return Path(first).resolve() == Path(second).resolve()
    except (OSError, ValueError):
        return first == second


class PermissionPolicy:
    """按硬边界、模式和 Capability 的固定优先级生成权限决定。"""

    def __init__(self, *, cwd: Path | None = None, home: Path | None = None):
        self.cwd = (cwd or Path.cwd()).resolve()
        self.home = (home or Path.home()).resolve()

    def _rule_action(
        self,
        tool: LionTool,
        arguments: Mapping[str, JSONValue],
    ) -> str | None:
        rules = load_permission_rules(self.home, self.cwd)
        if any(_matches_rule(rule, tool, arguments) for rule in rules["deny"]):
            return "deny"
        if any(_matches_rule(rule, tool, arguments) for rule in rules["allow"]):
            return "allow"
        return None

    def check_hard_boundaries(
        self,
        *,
        tool: LionTool,
        arguments: Mapping[str, JSONValue],
        mode: str,
        plan_file_path: str | None,
    ) -> PermissionDecision | None:
        """先执行显式 deny 与 Plan 硬约束，二者不能被任何模式绕过。"""
        if self._rule_action(tool, arguments) == "deny":
            return PermissionDecision(
                "deny",
                f"Denied by permission rule for {tool.name}",
            )

        capabilities = tool.capabilities
        if mode != "plan":
            return None
        if capabilities.mutates_workspace:
            target = str(arguments.get("file_path") or arguments.get("path") or "")
            if plan_file_path and target and _same_path(target, plan_file_path):
                return PermissionDecision("allow")
            return PermissionDecision(
                "deny",
                f"Blocked in plan mode: {tool.name}",
            )
        if capabilities.executes_process:
            return PermissionDecision("deny", "Shell commands blocked in plan mode")
        if not capabilities.allowed_in_plan:
            return PermissionDecision(
                "deny",
                f"Blocked in plan mode: {tool.name}",
            )
        return None

    def check(
        self,
        *,
        tool: LionTool,
        arguments: Mapping[str, JSONValue],
        mode: str,
        plan_file_path: str | None,
    ) -> PermissionDecision:
        hard = self.check_hard_boundaries(
            tool=tool,
            arguments=arguments,
            mode=mode,
            plan_file_path=plan_file_path,
        )
        if hard is not None:
            return hard

        if mode == "bypassPermissions":
            return PermissionDecision("allow")

        if self._rule_action(tool, arguments) == "allow":
            return PermissionDecision("allow")

        capabilities = tool.capabilities
        if capabilities.read_only:
            return PermissionDecision("allow")
        if mode == "acceptEdits" and capabilities.mutates_workspace:
            return PermissionDecision("allow")

        confirm_message = ""
        if capabilities.requires_confirmation:
            confirm_message = f"use tool: {tool.name}"
        elif capabilities.executes_process and is_dangerous(
            str(arguments.get("command", ""))
        ):
            confirm_message = str(arguments.get("command", ""))
        elif capabilities.mutates_workspace and capabilities.requires_read_before_write:
            target = str(arguments.get("file_path", ""))
            target_path = Path(target)
            if not target_path.is_absolute():
                target_path = self.cwd / target_path
            if target and not target_path.exists():
                confirm_message = f"write new file: {target}"

        if confirm_message:
            if mode == "dontAsk":
                return PermissionDecision(
                    "deny",
                    f"Auto-denied (dontAsk mode): {confirm_message}",
                )
            return PermissionDecision("confirm", confirm_message)

        return PermissionDecision("allow")
