"""Unit tests for the benchmark suite."""

import pytest
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


def test_evaluate_review_simulated_mode_runs_cleanly() -> None:
    """Verifies that simulated evaluation executes across 3 variants and candidate replay."""
    from benchmarks.evaluate_review import run_evaluation

    summary = run_evaluation(split="dev", mode="simulated", verbose=False)
    assert summary["execution_mode"] == "simulated"
    assert summary["cases_count"] == 10
    assert summary["release_recommendation"] == (
        "NO-GO (Simulated Benchmark Only — Real-Provider Live Validation Required)"
    )
    assert summary["release_gate"]["status"] == "SIMULATED_TEST_ONLY"
    assert summary["release_gate"]["maintainer_approval_granted"] is False

    # Variant A (Diff-Only) vs Variant B (Context-Rich)
    var_a = summary["variants"]["variant_a"]
    var_b = summary["variants"]["variant_b"]
    var_c = summary["variants"]["variant_c"]
    var_replay = summary["variants"]["variant_replay"]

    # Context enrichment: B discovers surrounding and multi-file defects that A misses
    assert var_b["defect_recall"] >= var_a["defect_recall"]

    # Downstream validation: C eliminates false positives via AST checks
    assert var_c["actionable_precision"] >= var_b["actionable_precision"]
    assert var_c["invalid_locations"] == 0

    # Candidate replay fidelity: Replay of B matches C downstream pipeline
    assert var_replay["actionable_precision"] == var_c["actionable_precision"]
    assert var_replay["defect_recall"] == var_c["defect_recall"]


def test_evaluate_review_live_mode_fails_closed_without_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies that live mode fails closed with exit code 1 if credentials are missing."""
    from benchmarks.evaluate_review import run_evaluation

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        run_evaluation(split="dev", mode="live", verbose=False)
    assert exc_info.value.code == 1


def test_evaluate_review_scoring_helpers() -> None:
    """Verifies matching, invalid location detection, and false positive checks."""
    from benchmarks.evaluate_review import (
        _is_finding_false_positive,
        _is_finding_match,
        _is_invalid_location,
    )
    from benchmarks.review_dataset import ExpectedIssue, GroundTruth, ReviewEvalCase

    from verifycraft.review.providers.base import RawCandidateFinding

    gt = GroundTruth(
        expected_verdict="has_defects",
        expected_issues=[
            ExpectedIssue(
                file_path="src/auth.py",
                line_range=[10, 15],
                subject_contains="expiration",
                label="issue",
            )
        ],
        forbidden_false_positives=["token valid"],
    )
    case = ReviewEvalCase(
        case_id="test_case",
        name="Test Case",
        split="dev",
        category="auth",
        version="1.0.0",
        provenance="test",
        base_commit="1" * 40,
        head_commit="2" * 40,
        changed_files={"src/auth.py": "line1\nline2\nline3\nline4\nline5\n"},
        raw_diff="diff",
        ground_truth=gt,
    )

    # Valid matching finding
    valid_f = RawCandidateFinding(
        file_path="src/auth.py",
        line=12,
        label="issue",
        subject="Missing token expiration check",
        description="Expires_at was not verified.",
    )
    assert _is_finding_match(valid_f, gt.expected_issues[0], case) is True
    assert _is_finding_false_positive(valid_f, gt, case) is False

    # Out of bounds line range in match
    out_of_range_f = RawCandidateFinding(
        file_path="src/auth.py",
        line=99,
        label="issue",
        subject="Missing token expiration check",
        description="Expires_at was not verified.",
    )
    assert _is_finding_match(out_of_range_f, gt.expected_issues[0], case) is False

    # Forbidden false positive text
    forbidden_f = RawCandidateFinding(
        file_path="src/auth.py",
        line=12,
        label="issue",
        subject="Token valid error with token expiration",
        description="Claims token valid erroneously.",
    )
    assert _is_finding_match(forbidden_f, gt.expected_issues[0], case) is False
    assert _is_finding_false_positive(forbidden_f, gt, case) is True

    # Invalid code locations (nonexistent file and line out of bounds)
    assert _is_invalid_location("src/nonexistent.py", 1, case) is True
    assert _is_invalid_location("src/auth.py", 50, case) is True
    assert _is_invalid_location("src/auth.py", 3, case) is False


def test_evaluate_release_gate_decision_policy() -> None:
    """Verifies that assess_release_gate enforces the 5-stage defensible release policy."""
    from benchmarks.evaluate_review import AggregateVariantSummary, assess_release_gate

    base_agg = AggregateVariantSummary(
        variant_name="variant_b",
        display_title="Variant B",
        total_cases=10,
        expected_defects=10,
        correct_findings=10,
        defect_recall=1.0,
        false_positives=2,
        false_positives_clean=1,
        actionable_precision=0.833,
        invalid_locations=0,
        requirements_correct=5,
        total_requirements=7,
        requirements_accuracy=0.714,
        total_comments=12,
        total_tokens=5000,
        total_prompt_tokens=3500,
        total_output_tokens=1500,
        total_latency_seconds=10.0,
        estimated_cost_usd=0.001,
    )
    vc_agg_pass = AggregateVariantSummary(
        variant_name="variant_c",
        display_title="Variant C",
        total_cases=10,
        expected_defects=10,
        correct_findings=10,
        defect_recall=1.0,
        false_positives=1,
        false_positives_clean=0,
        actionable_precision=0.909,
        invalid_locations=0,
        requirements_correct=6,
        total_requirements=7,
        requirements_accuracy=0.857,
        total_comments=11,
        total_tokens=5000,
        total_prompt_tokens=3500,
        total_output_tokens=1500,
        total_latency_seconds=10.0,
        estimated_cost_usd=0.001,
    )

    # 1. Simulated mode -> SIMULATED_TEST_ONLY
    sim_gate = assess_release_gate(
        mode="simulated",
        live_active=False,
        credentials_available=False,
        live_provider_call_completed=False,
        agg_b=base_agg,
        agg_c=vc_agg_pass,
    )
    assert sim_gate["status"] == "SIMULATED_TEST_ONLY"
    assert "Simulated Benchmark Only" in sim_gate["recommendation"]
    assert sim_gate["maintainer_approval_granted"] is False

    # 2. Live mode without credentials -> BLOCKED
    blocked_gate = assess_release_gate(
        mode="live",
        live_active=False,
        credentials_available=False,
        live_provider_call_completed=False,
        agg_b=base_agg,
        agg_c=vc_agg_pass,
    )
    assert blocked_gate["status"] == "BLOCKED"
    assert "Missing Credentials" in blocked_gate["recommendation"]
    assert blocked_gate["maintainer_approval_granted"] is False

    # 3. Live mode with failed provider call -> PROVIDER_CALL_FAILED
    failed_call_gate = assess_release_gate(
        mode="live",
        live_active=False,
        credentials_available=True,
        live_provider_call_completed=False,
        agg_b=base_agg,
        agg_c=vc_agg_pass,
    )
    assert failed_call_gate["status"] == "PROVIDER_CALL_FAILED"
    assert "Live Provider Calls Failed" in failed_call_gate["recommendation"]

    # 4. Live mode with quality criteria failure (e.g. invalid locations and low precision)
    vc_agg_fail = AggregateVariantSummary(
        variant_name="variant_c",
        display_title="Variant C",
        total_cases=10,
        expected_defects=10,
        correct_findings=5,
        defect_recall=0.5,
        false_positives=5,
        false_positives_clean=2,
        actionable_precision=0.5,
        invalid_locations=3,
        requirements_correct=2,
        total_requirements=7,
        requirements_accuracy=0.28,
        total_comments=10,
        total_tokens=5000,
        total_prompt_tokens=3500,
        total_output_tokens=1500,
        total_latency_seconds=10.0,
        estimated_cost_usd=0.001,
    )
    fail_gate = assess_release_gate(
        mode="live",
        live_active=True,
        credentials_available=True,
        live_provider_call_completed=True,
        agg_b=base_agg,
        agg_c=vc_agg_fail,
    )
    assert fail_gate["status"] == "FAILED_QUALITY_GATE"
    assert fail_gate["quality_criteria_passed"] is False
    assert len(fail_gate["quality_audit"]["failures"]) >= 3
    assert "Quality Criteria Not Met" in fail_gate["recommendation"]

    # 5. Live mode with quality criteria PASS but on subset (e.g. held_out)
    # -> PARTIAL_EVALUATION_ONLY
    subset_gate = assess_release_gate(
        mode="live",
        live_active=True,
        credentials_available=True,
        live_provider_call_completed=True,
        agg_b=base_agg,
        agg_c=vc_agg_pass,
        split="held_out",
    )
    assert subset_gate["status"] == "PARTIAL_EVALUATION_ONLY"
    assert subset_gate["live_evaluation_completed"] is False
    assert subset_gate["is_complete_evaluation"] is False
    assert "Partial Evaluation Only" in subset_gate["recommendation"]
    assert "held_out" in subset_gate["recommendation"]

    # 6. Live mode with complete evaluation ('all') and quality criteria PASS
    # -> PENDING_MAINTAINER_APPROVAL (Must never emit an unconditional GO!)
    pass_gate = assess_release_gate(
        mode="live",
        live_active=True,
        credentials_available=True,
        live_provider_call_completed=True,
        agg_b=base_agg,
        agg_c=vc_agg_pass,
        split="all",
    )
    assert pass_gate["status"] == "PENDING_MAINTAINER_APPROVAL"
    assert pass_gate["live_evaluation_completed"] is True
    assert pass_gate["is_complete_evaluation"] is True
    assert pass_gate["quality_criteria_passed"] is True
    assert pass_gate["maintainer_approval_granted"] is False
    assert "PENDING_MAINTAINER_APPROVAL" in pass_gate["recommendation"]
    assert pass_gate["recommendation"] != "GO"
