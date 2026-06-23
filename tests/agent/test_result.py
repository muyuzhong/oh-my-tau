from nanoagent.agent.events import AgentEnd
from nanoagent.agent.result import RunResult, StopReason


def test_run_result_fields():
    r = RunResult(reason=StopReason.COMPLETED, final_message_id="msg_1")
    assert r.reason is StopReason.COMPLETED and r.error is None


def test_run_result_succeeded_only_for_completed():
    assert RunResult(reason=StopReason.COMPLETED).succeeded is True
    assert RunResult(reason=StopReason.ERROR).succeeded is False


def test_agent_end_carries_result():
    r = RunResult(reason=StopReason.MAX_TURNS)
    ev = AgentEnd(messages=[], result=r)
    assert ev.type == "agent_end" and ev.result.reason is StopReason.MAX_TURNS


def test_run_stop_reason_distinct_from_wire():
    import nanoagent.ai as ai

    assert StopReason.COMPLETED.value == "completed"
    assert ai.StopReason.STOP.value == "stop"
