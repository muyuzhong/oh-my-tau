import asyncio
import sys
import time

from runtime.execution_env import LocalExecutionEnv, ReadResult, ShellResult


async def test_read_text_returns_content(tmp_path):
    path = tmp_path / "a.txt"; path.write_text("你好world", encoding="utf-8")
    result = await LocalExecutionEnv().read_text(str(path))
    assert result.ok and result.content == "你好world"


async def test_read_text_missing_is_error_not_raise(tmp_path):
    result = await LocalExecutionEnv().read_text(str(tmp_path / "nope.txt"))
    assert result.ok is False and result.content == "" and result.error


async def test_read_text_rejects_out_of_root(tmp_path):
    root = tmp_path / "ws"; root.mkdir()
    outside = tmp_path / "secret.txt"; outside.write_text("x", encoding="utf-8")
    result = await LocalExecutionEnv(root=root).read_text(str(outside))
    assert result.ok is False and "越界" in result.error


async def test_read_text_allows_in_root(tmp_path):
    root = tmp_path / "ws"; root.mkdir()
    inside = root / "ok.txt"; inside.write_text("内部", encoding="utf-8")
    result = await LocalExecutionEnv(root=root).read_text(str(inside))
    assert result.ok and result.content == "内部"


async def test_run_shell_success():
    result = await LocalExecutionEnv().run_shell("echo mono-shell", timeout=30)
    assert result.ok and result.exit_code == 0 and "mono-shell" in result.output


async def test_run_shell_timeout_does_not_hang():
    cmd = f'"{sys.executable}" -c "import time; time.sleep(5)"'
    start = time.perf_counter()
    result = await LocalExecutionEnv().run_shell(cmd, timeout=0.3)
    elapsed = time.perf_counter() - start
    assert result.ok is False and "超时" in result.error
    assert elapsed < 3.0


async def test_terminate_kills_running_process():
    process = await asyncio.create_subprocess_shell(
        f'"{sys.executable}" -c "import time; time.sleep(30)"',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    assert process.returncode is None
    await LocalExecutionEnv._terminate(process)
    assert process.returncode is not None
