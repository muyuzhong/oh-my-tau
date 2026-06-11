"""每轮结束后的可插拔漂移检测与硬约束监督。"""
from __future__ import annotations
import json, time
from dataclasses import dataclass


@dataclass
class Verdict:
    action: str = "continue"
    message: str | None = None
    reason: str | None = None


class RepetitionDetector:
    def __init__(self, window=5, threshold=3): self.window, self.threshold = window, threshold
    def check(self, state):
        counts = {}
        for message in [m for m in state.messages[-self.window * 2:] if m.role == "assistant"]:
            for call in message.get_tool_calls():
                key = (call.name, json.dumps(call.input, sort_keys=True, ensure_ascii=False)); counts[key] = counts.get(key, 0) + 1
        for (name, _), count in counts.items():
            if count >= self.threshold:
                return Verdict("inject", f"你已经 {count} 次以相同参数调用工具 {name}。请分析失败原因并换一种方法。", f"repetition:{name}")
        return Verdict()


class ConstraintValidator:
    def __init__(self, max_total_tool_calls=50, max_wall_seconds=3600, clock=time.monotonic):
        self.max_total_tool_calls, self.max_wall_seconds, self.clock, self.started = max_total_tool_calls, max_wall_seconds, clock, clock()
    def check(self, state):
        total = sum(len(m.get_tool_calls()) for m in state.messages if m.role == "assistant")
        if total > self.max_total_tool_calls: return Verdict("terminate", reason=f"constraint:max_tool_calls({total})")
        if self.clock() - self.started > self.max_wall_seconds: return Verdict("terminate", reason="constraint:max_wall_time")
        return Verdict()


class ReflectionStep:
    def __init__(self, every_n_turns=5): self.every, self.reviews = every_n_turns, 0
    def check(self, state):
        self.reviews += 1
        if self.reviews % self.every: return Verdict()
        goal = next((m.get_text() for m in state.messages if m.role == "user"), "")
        return Verdict("inject", f"### 反思检查点\n原始目标：{goal}\n请判断是否需要调整方法。", "reflection")


class Supervisor:
    def __init__(self, checkers=None): self.checkers = list(checkers) if checkers is not None else [RepetitionDetector(), ConstraintValidator()]
    def review(self, state):
        for checker in self.checkers:
            verdict = checker.check(state)
            if verdict.action != "continue": return verdict
        return Verdict()
