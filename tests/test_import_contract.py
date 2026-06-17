from pathlib import Path

from importlinter.api import use_cases


def test_import_linter_contract_holds():
    """agent -> ai -> utils layering must hold (no reverse/upward imports)."""
    config = Path(__file__).resolve().parents[1] / ".importlinter"
    assert use_cases.lint_imports(config_filename=str(config)) is True
