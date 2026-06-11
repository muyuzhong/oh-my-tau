"""append-only 会话状态与恢复测试。"""

from runtime.blocks import Message, TextBlock, ToolUseBlock, Usage
from runtime.state import SessionState


def test_append_writes_jsonl(tmp_path):
    state = SessionState(transcript_dir=str(tmp_path))
    state.append(Message.user("第一条"))
    state.append(Message.assistant([TextBlock(text="回复")], usage=Usage(3, 4)))
    lines = state.transcript_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_resume_roundtrip(tmp_path):
    state = SessionState(session_id="sess_test01", transcript_dir=str(tmp_path))
    state.append(Message.user("问题"))
    state.append(
        Message.assistant(
            [ToolUseBlock(name="bash", input={"cmd": "ls"}, id="t1")],
            usage=Usage(7, 8),
        )
    )

    restored = SessionState.resume("sess_test01", transcript_dir=str(tmp_path))
    assert len(restored.messages) == 2
    assert restored.messages[1].get_tool_calls()[0].input == {"cmd": "ls"}
    assert restored.messages[1].usage.output_tokens == 8
    assert [message.to_dict() for message in restored.messages] == [
        message.to_dict() for message in state.messages
    ]
