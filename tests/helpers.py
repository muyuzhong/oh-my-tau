"""跨测试文件共享的最小工具与可注入假时钟。"""
from core.tool import Tool, ToolResult
from runtime.execution_env import ReadResult, ShellResult


class EchoTool(Tool):
    timeout_seconds = 5
    def name(self): return "echo"
    def description(self): return "回显输入文本"
    def input_schema(self):
        return {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    async def call(self, params):
        return ToolResult(True, f"echo:{params['text']}", 0.0)


class DangerTool(EchoTool):
    requires_approval = True
    def name(self): return "danger"


class FakeSleep:
    def __init__(self): self.calls = []
    async def __call__(self, delay): self.calls.append(delay)


async def collect(aiter):
    return [event async for event in aiter]


class FakeExecutionEnv:
    """伪执行端口：记录调用并返回预置结果，无需真实文件系统或子进程。"""
    def __init__(self, read_result=None, shell_result=None):
        self.read_result = read_result or ReadResult(True, "假内容")
        self.shell_result = shell_result or ShellResult(True, 0, "假输出")
        self.reads, self.shells = [], []

    async def read_text(self, path):
        self.reads.append(path)
        return self.read_result

    async def run_shell(self, command, *, timeout):
        self.shells.append((command, timeout))
        return self.shell_result
