"""Unit tests for the benchmark suite."""

from benchmarks.baseline import BaselineReviewer
from benchmarks.evaluate import run_benchmark
from benchmarks.fixtures import load_benchmark_cases


def test_load_benchmark_cases() -> None:
    cases = load_benchmark_cases()
    assert len(cases) == 5
    case_ids = [c.case_id for c in cases]
    assert "case_01_real_regression" in case_ids
    assert "case_02_contradicted_hypothesis" in case_ids
    assert "case_03_inconclusive_signature" in case_ids
    assert "case_04_blocked_untrusted_code" in case_ids
    assert "case_05_stale_evidence" in case_ids


def test_baseline_reviewer_heuristic() -> None:
    cases = load_benchmark_cases()
    baseline = BaselineReviewer()
    # Baseline predicts supported for regression case
    v1 = baseline.predict(cases[0].verification_input)
    assert v1.value == "supported"


def test_canonical_fixtures_contract_invariants() -> None:
    """Ensure each canonical Stage 0/1 fixture satisfies its documented contract invariant."""
    summary = run_benchmark(verbose=False)
    assert summary["total_cases"] == 5
    for case_result in summary["cases"]:
        assert case_result["verifycraft_correct"] is True, (
            f"Fixture {case_result['case_id']} expected {case_result['ground_truth']}, "
            f"got {case_result['verifycraft_verdict']}"
        )
