"""正式上下文评测使用的可执行编码任务。"""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TaskFixture:
    initial_files: dict[str, str]
    reference_files: dict[str, str]
    test_code: str
    failure_log: str

    @property
    def allowed_files(self) -> set[str]:
        return set(self.initial_files)


TEST_PREFIX = """from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
"""


FIXTURES: dict[str, TaskFixture] = {
    "pricing_split": TaskFixture(
        initial_files={
            "pricing.py": '''def calculate_cost(prompt_tokens, cache_hit_tokens, completion_tokens):
    """按每百万 token 的人民币价格返回费用。"""
    return prompt_tokens / 1_000_000 + completion_tokens / 1_000_000 * 2
''',
        },
        reference_files={
            "pricing.py": '''def calculate_cost(prompt_tokens, cache_hit_tokens, completion_tokens):
    """按每百万 token 的人民币价格返回费用。"""
    hit = min(max(int(cache_hit_tokens), 0), int(prompt_tokens))
    miss = max(int(prompt_tokens) - hit, 0)
    return hit / 1_000_000 * 0.02 + miss / 1_000_000 * 1.0 + int(completion_tokens) / 1_000_000 * 2.0
''',
        },
        test_code=TEST_PREFIX
        + '''from pricing import calculate_cost

assert abs(calculate_cost(1_000_000, 1_000_000, 0) - 0.02) < 1e-12
assert abs(calculate_cost(1_000_000, 250_000, 100_000) - 0.955) < 1e-12
assert abs(calculate_cost(100, 999, 0) - 0.000002) < 1e-12
assert abs(calculate_cost(100, -5, 10) - 0.00012) < 1e-12
print("PASS")
''',
        failure_log="test_cached_input_price: 期望 0.02，实际 1.0；缓存命中 token 被按未命中价格计费。",
    ),
    "normalize_fact": TaskFixture(
        initial_files={
            "retention.py": '''def normalize_fact(value):
    return "".join(value.split())
''',
        },
        reference_files={
            "retention.py": '''import re


def normalize_fact(value):
    return re.sub(r"\\s*([=:])\\s*", r"\\1", value.strip())
''',
        },
        test_code=TEST_PREFIX
        + '''from retention import normalize_fact

assert normalize_fact("  cmd = python -m pytest -q  ") == "cmd=python -m pytest -q"
assert normalize_fact("说明: 保留 两个 空格") == "说明:保留 两个 空格"
assert normalize_fact(r"path = D:\\harness agent\\Lion") == r"path=D:\\harness agent\\Lion"
assert normalize_fact("plain   text") == "plain   text"
print("PASS")
''',
        failure_log="test_command_spacing: 命令中的有效空格被吞掉，python-mpytest-q 无法执行。",
    ),
    "truncate_utf8": TaskFixture(
        initial_files={
            "truncation.py": '''def truncate_utf8(text, max_bytes):
    if len(text) <= max_bytes:
        return text
    return text[:max_bytes]
''',
        },
        reference_files={
            "truncation.py": '''def truncate_utf8(text, max_bytes):
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    marker = "\\n...[truncated]...\\n"
    marker_size = len(marker.encode("utf-8"))
    if max_bytes <= marker_size:
        return raw[:max_bytes].decode("utf-8", errors="ignore")
    remaining = max_bytes - marker_size
    head_size = remaining // 2
    tail_size = remaining - head_size
    head = raw[:head_size].decode("utf-8", errors="ignore")
    tail = raw[-tail_size:].decode("utf-8", errors="ignore")
    result = head + marker + tail
    while len(result.encode("utf-8")) > max_bytes and tail:
        tail = tail[1:]
        result = head + marker + tail
    return result
''',
        },
        test_code=TEST_PREFIX
        + '''from truncation import truncate_utf8

source = "开头-" + "甲乙丙丁" * 40 + "-结尾"
result = truncate_utf8(source, 80)
assert len(result.encode("utf-8")) <= 80
assert result.startswith("开头-")
assert result.endswith("-结尾")
assert "...[truncated]..." in result
assert truncate_utf8("短文本", 100) == "短文本"
tiny = truncate_utf8("中文abc", 4)
assert len(tiny.encode("utf-8")) <= 4
tiny.encode("utf-8")
print("PASS")
''',
        failure_log="test_utf8_budget: 字符数未超过 80，但 UTF-8 已占 224 bytes；下游请求超过字节预算。",
    ),
    "static_dynamic_prompt": TaskFixture(
        initial_files={
            "prompt_parts.py": '''def build_static_system_prompt(cwd, today):
    return f"You are Lion Code.\\nWorking directory: {cwd}\\nToday: {today}"


def build_dynamic_reminder(cwd, today):
    return ""
''',
            "request_builder.py": '''from prompt_parts import build_static_system_prompt


def build_request(user_text, cwd, today):
    return [
        {"role": "system", "content": build_static_system_prompt(cwd, today)},
        {"role": "user", "content": user_text},
    ]
''',
        },
        reference_files={
            "prompt_parts.py": '''def build_static_system_prompt(cwd=None, today=None):
    return "You are Lion Code."


def build_dynamic_reminder(cwd, today):
    return f"<system-reminder>\\nWorking directory: {cwd}\\nToday: {today}\\n</system-reminder>"
''',
            "request_builder.py": '''from prompt_parts import build_dynamic_reminder, build_static_system_prompt


def build_request(user_text, cwd, today):
    reminder = build_dynamic_reminder(cwd, today)
    return [
        {"role": "system", "content": build_static_system_prompt()},
        {"role": "user", "content": f"{reminder}\\n\\n{user_text}"},
    ]
''',
        },
        test_code=TEST_PREFIX
        + '''from request_builder import build_request

first = build_request("修复问题", "D:/repo-a", "2026-07-23")
second = build_request("修复问题", "D:/repo-b", "2026-07-24")
assert first[0] == second[0] == {"role": "system", "content": "You are Lion Code."}
assert "D:/repo-a" in first[1]["content"] and "2026-07-23" in first[1]["content"]
assert "D:/repo-b" in second[1]["content"] and "2026-07-24" in second[1]["content"]
assert first[1]["content"].endswith("修复问题")
assert first[0]["content"] not in first[1]["content"]
print("PASS")
''',
        failure_log="test_cross_workspace_cache: 切换 cwd 后 system 前缀发生变化，公共缓存无法复用。",
    ),
    "hot_prefix_policy": TaskFixture(
        initial_files={
            "policy.py": '''def should_snip(utilization, seconds_since_call):
    return utilization >= 0.60
''',
            "session.py": '''from policy import should_snip


def compact_results(results, utilization, seconds_since_call):
    if not should_snip(utilization, seconds_since_call):
        return list(results)
    return ["[snipped]"] * max(0, len(results) - 3) + list(results[-3:])
''',
        },
        reference_files={
            "policy.py": '''SNIP_THRESHOLD = 0.60
SNIP_HOT_OVERRIDE = 0.75
CACHE_HOT_SECONDS = 300


def should_snip(utilization, seconds_since_call):
    if utilization < SNIP_THRESHOLD:
        return False
    cache_hot = 0 <= seconds_since_call < CACHE_HOT_SECONDS
    if cache_hot and utilization < SNIP_HOT_OVERRIDE:
        return False
    return True
''',
            "session.py": '''from policy import should_snip


def compact_results(results, utilization, seconds_since_call):
    items = list(results)
    if not should_snip(utilization, seconds_since_call):
        return items
    cutoff = max(0, len(items) - 3)
    return ["[snipped]"] * cutoff + items[cutoff:]
''',
        },
        test_code=TEST_PREFIX
        + '''from policy import should_snip
from session import compact_results

assert not should_snip(0.59, 999)
assert not should_snip(0.65, 1)
assert should_snip(0.65, 301)
assert should_snip(0.75, 1)
assert should_snip(0.90, 1)
source = ["r1", "r2", "r3", "r4", "r5"]
assert compact_results(source, 0.65, 1) == source
assert compact_results(source, 0.65, 301) == ["[snipped]", "[snipped]", "r3", "r4", "r5"]
print("PASS")
''',
        failure_log="test_hot_cache_guard: 利用率 65% 且前缀仍热时发生原地裁剪，下一请求缓存命中骤降。",
    ),
    "compact_pairing": TaskFixture(
        initial_files={
            "messages.py": '''def is_plain_user(message):
    return message.get("role") == "user"
''',
            "compactor.py": '''def compact(messages, summary):
    return [
        messages[0],
        {"role": "user", "content": "[summary] " + summary},
        messages[-1],
    ]
''',
        },
        reference_files={
            "messages.py": '''def is_plain_user(message):
    return message.get("role") == "user" and isinstance(message.get("content"), str)


def last_plain_user(messages):
    for message in reversed(messages):
        if is_plain_user(message):
            return message
    return None
''',
            "compactor.py": '''from messages import last_plain_user


def compact(messages, summary):
    if not messages:
        return []
    system = messages[0]
    latest_user = last_plain_user(messages)
    result = [
        system,
        {"role": "user", "content": "[Previous conversation summary]\\n" + summary},
        {"role": "assistant", "content": "已理解此前上下文，可以继续。"},
    ]
    if latest_user is not None:
        result.append(latest_user)
    return result
''',
        },
        test_code=TEST_PREFIX
        + '''from compactor import compact
from messages import is_plain_user, last_plain_user

history = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "旧问题"},
    {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
    {"role": "tool", "tool_call_id": "c1", "content": "结果"},
    {"role": "user", "content": "当前问题"},
]
result = compact(history, "关键决策")
assert [item["role"] for item in result] == ["system", "user", "assistant", "user"]
assert result[-1]["content"] == "当前问题"
assert "关键决策" in result[1]["content"]
assert is_plain_user({"role": "user", "content": "x"})
assert not is_plain_user({"role": "user", "content": [{"type": "tool_result"}]})
assert last_plain_user(history)["content"] == "当前问题"
assert compact([], "x") == []
print("PASS")
''',
        failure_log="invalid_request_error: assistant.tool_calls 后的 tool 结果在压缩时被拆开，消息配对不合法。",
    ),
    "keep_recent_offbyone": TaskFixture(
        initial_files={
            "stale.py": '''SNIPPED = "[snipped]"


def snip_stale(results, keep_recent=3):
    cutoff = len(results) - keep_recent
    return [SNIPPED if index <= cutoff else value for index, value in enumerate(results)]
''',
        },
        reference_files={
            "stale.py": '''SNIPPED = "[snipped]"


def snip_stale(results, keep_recent=3):
    items = list(results)
    cutoff = max(0, len(items) - max(0, keep_recent))
    return [SNIPPED if index < cutoff else value for index, value in enumerate(items)]
''',
        },
        test_code=TEST_PREFIX
        + '''from stale import SNIPPED, snip_stale

assert snip_stale(["a", "b", "c", "d", "e"], 3) == [SNIPPED, SNIPPED, "c", "d", "e"]
assert snip_stale(["a", "b"], 3) == ["a", "b"]
assert snip_stale([], 3) == []
once = snip_stale(["a", "b", "c", "d"], 3)
assert snip_stale(once, 3) == once
assert snip_stale(["a", "b"], 0) == [SNIPPED, SNIPPED]
print("PASS")
''',
        failure_log="test_keep_recent_three: 期望保留 c/d/e，实际只保留 d/e；边界多裁剪了一条。",
    ),
    "usage_nested": TaskFixture(
        initial_files={
            "usage.py": '''def parse_usage(payload):
    prompt = int(payload.get("prompt_tokens", 0))
    details = payload.get("prompt_tokens_details", {})
    hit = int(details.get("cached_tokens", 0))
    return {"prompt": prompt, "hit": hit, "miss": prompt - hit}
''',
        },
        reference_files={
            "usage.py": '''def parse_usage(payload):
    prompt = max(int(payload.get("prompt_tokens") or 0), 0)
    details = payload.get("prompt_tokens_details") or {}
    hit = payload.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = details.get("cached_tokens") or 0
    hit = min(max(int(hit or 0), 0), prompt)
    miss = payload.get("prompt_cache_miss_tokens")
    if miss is None:
        miss = prompt - hit
    miss = max(int(miss or 0), 0)
    if hit + miss != prompt:
        miss = max(prompt - hit, 0)
    return {"prompt": prompt, "hit": hit, "miss": miss}
''',
        },
        test_code=TEST_PREFIX
        + '''from usage import parse_usage

assert parse_usage({"prompt_tokens": 100, "prompt_cache_hit_tokens": 60, "prompt_cache_miss_tokens": 40}) == {"prompt": 100, "hit": 60, "miss": 40}
assert parse_usage({"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 25}}) == {"prompt": 100, "hit": 25, "miss": 75}
assert parse_usage({"prompt_tokens": 100, "prompt_cache_hit_tokens": 120, "prompt_cache_miss_tokens": 7}) == {"prompt": 100, "hit": 100, "miss": 0}
assert parse_usage({"prompt_tokens": 100, "prompt_cache_hit_tokens": 40, "prompt_cache_miss_tokens": 99}) == {"prompt": 100, "hit": 40, "miss": 60}
assert parse_usage({}) == {"prompt": 0, "hit": 0, "miss": 0}
print("PASS")
''',
        failure_log="test_deepseek_usage: 顶层 prompt_cache_hit_tokens=60 被忽略，报告错误显示命中率为 0%。",
    ),
    "same_file_dedupe": TaskFixture(
        initial_files={
            "dedupe.py": '''SNIPPED = "[snipped]"


def dedupe_results(results, keep_recent=3):
    cutoff = max(0, len(results) - keep_recent)
    return [item if index >= cutoff else {**item, "content": SNIPPED} for index, item in enumerate(results)]
''',
        },
        reference_files={
            "dedupe.py": '''SNIPPED = "[snipped]"


def dedupe_results(results, keep_recent=3):
    items = [dict(item) for item in results]
    latest_by_path = {}
    for index, item in enumerate(items):
        path = item.get("path")
        if path:
            latest_by_path[path] = index
    recent_start = max(0, len(items) - max(0, keep_recent))
    keep = set(range(recent_start, len(items))) | set(latest_by_path.values())
    for index, item in enumerate(items):
        if index not in keep:
            item["content"] = SNIPPED
    return items
''',
        },
        test_code=TEST_PREFIX
        + '''from dedupe import SNIPPED, dedupe_results

source = [
    {"path": "a.py", "content": "a1"},
    {"path": "b.py", "content": "b1"},
    {"path": "a.py", "content": "a2"},
    {"path": "c.py", "content": "c1"},
    {"path": "d.py", "content": "d1"},
    {"path": "e.py", "content": "e1"},
]
result = dedupe_results(source, 3)
assert result[0]["content"] == SNIPPED
assert result[1]["content"] == "b1"
assert result[2]["content"] == "a2"
assert [item["content"] for item in result[-3:]] == ["c1", "d1", "e1"]
assert source[0]["content"] == "a1"
assert dedupe_results([], 3) == []
print("PASS")
''',
        failure_log="test_latest_per_file: b.py 只有一次读取但因不在最近三条中被清理，后续诊断缺少该文件内容。",
    ),
}


FORBIDDEN_CALLS = {"__import__", "compile", "eval", "exec", "input", "open"}
FORBIDDEN_MODULES = {"ctypes", "os", "pathlib", "shutil", "socket", "subprocess"}


def render_files(files: dict[str, str]) -> str:
    return "\n\n".join(f"===== {name} =====\n{content}" for name, content in files.items())


def _validate_candidate(files: dict[str, Any], fixture: TaskFixture) -> str | None:
    if not files:
        return "模型没有返回任何文件"
    unknown = set(files) - fixture.allowed_files
    if unknown:
        return f"模型尝试修改未授权文件：{', '.join(sorted(unknown))}"
    for name, content in files.items():
        if not isinstance(content, str):
            return f"{name} 的内容不是字符串"
        if len(content.encode("utf-8")) > 50_000:
            return f"{name} 超过 50 KB 输出上限"
        try:
            tree = ast.parse(content, filename=name)
        except SyntaxError as exc:
            return f"{name} 语法错误：{exc.msg}"
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [alias.name.split(".", 1)[0] for alias in node.names] if isinstance(node, ast.Import) else [(node.module or "").split(".", 1)[0]]
                if any(module in FORBIDDEN_MODULES for module in modules):
                    return f"{name} 导入了评测禁用模块"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                return f"{name} 调用了评测禁用函数 {node.func.id}"
    return None


def evaluate_files(fixture_id: str, candidate_files: dict[str, Any], timeout_seconds: float = 10.0) -> dict[str, Any]:
    fixture = FIXTURES[fixture_id]
    validation_error = _validate_candidate(candidate_files, fixture)
    if validation_error:
        return {"passed": False, "stage": "validation", "detail": validation_error}

    with tempfile.TemporaryDirectory(prefix="lion-formal-task-") as temp_dir:
        workspace = Path(temp_dir)
        for name, content in fixture.initial_files.items():
            (workspace / name).write_text(content, encoding="utf-8")
        for name, content in candidate_files.items():
            (workspace / name).write_text(content, encoding="utf-8")
        test_path = workspace / "_acceptance.py"
        test_path.write_text(fixture.test_code, encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "-I", str(test_path)],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    return {
        "passed": completed.returncode == 0,
        "stage": "acceptance",
        "returncode": completed.returncode,
        "stdout": completed.stdout[-2000:],
        "stderr": completed.stderr[-4000:],
    }


def validate_fixture_catalog() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for fixture_id, fixture in FIXTURES.items():
        initial = evaluate_files(fixture_id, fixture.initial_files)
        reference = evaluate_files(fixture_id, fixture.reference_files)
        checks.append(
            {
                "fixture_id": fixture_id,
                "initial_fails": not initial["passed"],
                "reference_passes": reference["passed"],
                "initial_detail": initial,
                "reference_detail": reference,
            }
        )
    return checks
