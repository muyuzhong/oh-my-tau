import pytest

from nanoagent.ai import ToolCall
from nanoagent.agent.control import AbortSignal, AllowAll


def test_abort_signal_flips():
    sig = AbortSignal()
    assert sig.aborted is False
    sig.abort("user")
    assert sig.aborted is True and sig.reason == "user"


def test_abort_signal_keeps_first_reason():
    sig = AbortSignal()
    sig.abort("first")
    sig.abort("second")
    assert sig.reason == "first"


@pytest.mark.asyncio
async def test_allow_all_approves():
    src = AllowAll()
    assert await src.request_approval(ToolCall(id="t", name="x", arguments={}), "exec") is True
