from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "benchmarks" / "context_management" / "formal_benchmark.py"


def load_formal_benchmark():
    spec = importlib.util.spec_from_file_location("lion_formal_benchmark", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_formal_dataset_is_balanced_and_executable():
    benchmark = load_formal_benchmark()
    dataset = benchmark.read_dataset()

    validation = benchmark.validate_dataset(dataset)

    assert validation["passed"], validation["errors"]
    assert validation["task_count"] == 9
    assert validation["matrix_cells"] == 9


def test_formal_order_contains_54_balanced_sessions():
    benchmark = load_formal_benchmark()
    dataset = benchmark.read_dataset()

    order = benchmark.build_run_order(
        dataset["tasks"], dataset["policies"], dataset["repeat_count"], 20260723
    )

    assert len(order) == 54
    keys = {(item["task_id"], item["policy"], item["repeat"]) for item in order}
    assert len(keys) == 54
    assert {item["repeat"] for item in order} == {1, 2}
