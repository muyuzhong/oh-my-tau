from runtime.blocks import Message, ToolUseBlock
from runtime.state import SessionState
from runtime.supervisor import ConstraintValidator, ReflectionStep, RepetitionDetector, Supervisor


def repeated(tmp_path, n):
    state = SessionState(transcript_dir=str(tmp_path))
    state.append(Message.user("开始"))
    for _ in range(n): state.append(Message.assistant([ToolUseBlock("bash", {"cmd": "ls"})]))
    return state


def test_repetition_detector_injects(tmp_path):
    assert RepetitionDetector(threshold=3).check(repeated(tmp_path, 3)).action == "inject"


def test_constraint_validator_terminates(tmp_path):
    assert ConstraintValidator(max_total_tool_calls=2).check(repeated(tmp_path, 3)).action == "terminate"


def test_reflection_step_every_n(tmp_path):
    step = ReflectionStep(2)
    assert step.check(repeated(tmp_path, 1)).action == "continue"
    assert step.check(repeated(tmp_path, 1)).action == "inject"


def test_supervisor_first_non_continue_wins(tmp_path):
    verdict = Supervisor([RepetitionDetector(threshold=3), ConstraintValidator(1)]).review(repeated(tmp_path, 3))
    assert verdict.action == "inject"
