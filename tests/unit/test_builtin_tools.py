from tools.builtin import ReadFileTool, RunCommandTool
from runtime.execution_env import ReadResult, ShellResult
from tests.helpers import FakeExecutionEnv


async def test_read_file(tmp_path):
    path = tmp_path / "demo.txt"; path.write_text("内容123", encoding="utf-8")
    assert "内容123" in (await ReadFileTool().call({"path": str(path)})).content


async def test_read_file_missing_is_error():
    assert not (await ReadFileTool().call({"path": "Z:/不存在/no.txt"})).success


async def test_read_file_truncates(tmp_path):
    path = tmp_path / "big.txt"; path.write_text("x" * 30000)
    assert "[已截断]" in (await ReadFileTool().call({"path": str(path)})).content


async def test_run_command_echo():
    assert "mono-test" in (await RunCommandTool().call({"command": "echo mono-test"})).content


def test_run_command_requires_approval():
    assert RunCommandTool.requires_approval is True


async def test_read_file_routes_through_env():
    env = FakeExecutionEnv(read_result=ReadResult(True, "经由端口"))
    result = await ReadFileTool(env).call({"path": "anything"})
    assert result.success and result.content == "经由端口" and env.reads == ["anything"]


async def test_read_file_env_error_becomes_tool_error():
    env = FakeExecutionEnv(read_result=ReadResult(False, "", "越界"))
    result = await ReadFileTool(env).call({"path": "x"})
    assert not result.success and "越界" in result.content


async def test_run_command_routes_through_env():
    env = FakeExecutionEnv(shell_result=ShellResult(True, 0, "假输出"))
    result = await RunCommandTool(env).call({"command": "ls"})
    assert result.success and "假输出" in result.content and env.shells == [("ls", 30)]
