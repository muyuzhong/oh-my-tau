from providers.mock import MockProvider
from runtime import events as ev
from runtime.context import RetryPolicy
from runtime.control import Abort, Approve, ControlPlane, Deny, Pause, Resume, Steer
from runtime.engine import AgentLoop
from runtime.executor import ToolRegistry
from runtime.state import SessionState
from tests.helpers import DangerTool, EchoTool, FakeSleep


def make_loop(tmp_path, script, tools=()):
    registry = ToolRegistry()
    for tool in tools: registry.register(tool)
    control = ControlPlane()
    return AgentLoop(MockProvider(script), registry, control=control, state=SessionState(transcript_dir=str(tmp_path)),
                     retry_policy=RetryPolicy(sleep=FakeSleep())), control


async def test_abort_mid_stream_discards_partial(tmp_path):
    loop, control = make_loop(tmp_path, [MockProvider.text_turn("很长")])
    events = []
    async for event in loop.run("hi"):
        events.append(event)
        if isinstance(event, ev.TextDeltaEvent): control.submit(Abort())
    assert events[-1].reason == "user_abort" and len(loop.state.messages) == 1


async def test_steer_appends_user_message(tmp_path):
    loop, control = make_loop(tmp_path, [MockProvider.text_turn("收到")])
    control.submit(Steer("注意"))
    _ = [event async for event in loop.run("帮助")]
    assert [m.get_text() for m in loop.state.messages if m.role == "user"] == ["帮助", "注意"]


async def test_approval_approve_executes_tool(tmp_path):
    loop, control = make_loop(tmp_path, [MockProvider.tool_turn("danger", {"text": "rm"}, "d1"), MockProvider.text_turn("完成")], [DangerTool()])
    events = []
    async for event in loop.run("执行"):
        events.append(event)
        if isinstance(event, ev.ApprovalRequested): control.submit(Approve())
    assert events[-1].reason == "completed"


async def test_approval_deny_feeds_error_to_model(tmp_path):
    loop, control = make_loop(tmp_path, [MockProvider.tool_turn("danger", {"text": "rm"}, "d1"), MockProvider.text_turn("不执行")], [DangerTool()])
    async for event in loop.run("执行"):
        if isinstance(event, ev.ApprovalRequested): control.submit(Deny())
    assert any(getattr(block, "error_type", None) == "PermissionDenied" for m in loop.provider.requests[1].messages for block in m.content)


async def test_pause_then_resume(tmp_path):
    loop, control = make_loop(tmp_path, [MockProvider.text_turn("继续")])
    control.submit(Pause()); control.submit(Resume())
    names = [type(event).__name__ async for event in loop.run("hi")]
    assert "Paused" in names and "Resumed" in names


async def test_unattended_tools_do_not_need_approval(tmp_path):
    loop, _ = make_loop(tmp_path, [MockProvider.tool_turn("echo", {"text": "safe"}), MockProvider.text_turn("done")], [EchoTool()])
    events = [event async for event in loop.run("hi")]
    assert not any(isinstance(event, ev.ApprovalRequested) for event in events)
