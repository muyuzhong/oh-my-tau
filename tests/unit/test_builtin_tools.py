from tools.builtin import ReadFileTool, RunCommandTool


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
