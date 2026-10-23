"""Unit tests for verdict evaluator."""

from verifycraft.contracts import (
    Evidence,
    ExpectedObservation,
    Hypothesis,
    ResourceUsage,
    Verdict,
)
from verifycraft.core.evaluator import evaluate_hypothesis


def _make_evidence(
    exit_code: int, stdout: str = "", stderr: str = "", truncated: bool = False
) -> Evidence:
    return Evidence(
        evidence_id="evi-1",
        check_id="chk-1",
        evidence_type="pytest_execution",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        output_truncated=truncated,
        fingerprint="sha256:dummy",
        resource_usage=ResourceUsage(wall_time_seconds=0.1),
    )


def test_regression_supported_when_expected_failure_observed() -> None:
    hyp = Hypothesis(
        id="hyp-reg-1",
        claim="Function raises ZeroDivisionError on empty input",
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            expected_exit_codes=[1],
            match_patterns=["ZeroDivisionError"],
        ),
    )
    evi = _make_evidence(
        exit_code=1, stdout="FAILED test_div - ZeroDivisionError: division by zero"
    )
    finding = evaluate_hypothesis(hyp, evi)
    assert finding.verdict == Verdict.SUPPORTED
    assert "ZeroDivisionError" in finding.reason or "expected failure" in finding.reason


def test_regression_refuted_when_test_passes() -> None:
    # Key invariant: passing test refutes a regression hypothesis!
    hyp = Hypothesis(
        id="hyp-reg-1",
        claim="Function raises ZeroDivisionError on empty input",
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            expected_exit_codes=[1],
            match_patterns=["ZeroDivisionError"],
        ),
    )
    evi = _make_evidence(exit_code=0, stdout="1 passed in 0.01s")
    finding = evaluate_hypothesis(hyp, evi)
    assert finding.verdict == Verdict.REFUTED
    assert "exit code 0" in finding.reason


def test_contradict_pattern_refutes_hypothesis() -> None:
    hyp = Hypothesis(
        id="hyp-1",
        claim="Calculation is wrong",
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            contradict_patterns=["ALL_CORRECT"],
        ),
    )
    evi = _make_evidence(exit_code=1, stdout="Failure: ALL_CORRECT flag set unexpectedly")
    finding = evaluate_hypothesis(hyp, evi)
    assert finding.verdict == Verdict.REFUTED
    assert "contradict pattern" in finding.reason


def test_inconclusive_when_failure_signature_does_not_match() -> None:
    hyp = Hypothesis(
        id="hyp-reg-1",
        claim="Function raises ZeroDivisionError on empty input",
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            expected_exit_codes=[1],
            match_patterns=["ZeroDivisionError"],
        ),
    )
    # Failed, but with ImportError instead of predicted ZeroDivisionError
    evi = _make_evidence(exit_code=1, stdout="FAILED - ImportError: cannot import name 'foo'")
    finding = evaluate_hypothesis(hyp, evi)
    assert finding.verdict == Verdict.INCONCLUSIVE
    assert "did not produce expected diagnostic signature" in finding.reason


def test_inconclusive_when_output_truncated_missing_signature() -> None:
    hyp = Hypothesis(
        id="hyp-reg-1",
        claim="Function raises ZeroDivisionError",
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            expected_exit_codes=[1],
            match_patterns=["ZeroDivisionError"],
        ),
    )
    evi = _make_evidence(exit_code=1, stdout="Truncated output...", truncated=True)
    finding = evaluate_hypothesis(hyp, evi)
    assert finding.verdict == Verdict.INCONCLUSIVE
    assert "truncated" in finding.reason


def test_inconclusive_on_unexpected_exit_code() -> None:
    hyp = Hypothesis(
        id="hyp-reg-1",
        claim="Regression check",
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            expected_exit_codes=[1],
        ),
    )
    evi = _make_evidence(exit_code=2, stderr="Command line syntax error")
    finding = evaluate_hypothesis(hyp, evi)
    assert finding.verdict == Verdict.INCONCLUSIVE
    assert "unexpected code 2" in finding.reason


def test_inconclusive_on_timeout() -> None:
    hyp = Hypothesis(
        id="hyp-reg-1",
        claim="Regression check",
        expected_observation=ExpectedObservation(expected_outcome="fail"),
    )
    evi = _make_evidence(exit_code=124, stderr="Command timed out.")
    finding = evaluate_hypothesis(hyp, evi)
    assert finding.verdict == Verdict.INCONCLUSIVE
    assert "timed out" in finding.reason


def test_expected_pass_supported() -> None:
    hyp = Hypothesis(
        id="hyp-pass-1",
        claim="Bug fix ensures test passes",
        expected_observation=ExpectedObservation(
            expected_outcome="pass",
            expected_exit_codes=[0],
            match_patterns=["passed"],
        ),
    )
    evi = _make_evidence(exit_code=0, stdout="1 passed in 0.01s")
    finding = evaluate_hypothesis(hyp, evi)
    assert finding.verdict == Verdict.SUPPORTED


def test_expected_pass_refuted_on_failure() -> None:
    hyp = Hypothesis(
        id="hyp-pass-1",
        claim="Bug fix ensures test passes",
        expected_observation=ExpectedObservation(
            expected_outcome="pass",
            expected_exit_codes=[0],
        ),
    )
    evi = _make_evidence(exit_code=1, stdout="FAILED test")
    finding = evaluate_hypothesis(hyp, evi)
    assert finding.verdict == Verdict.REFUTED


def test_expected_pass_inconclusive_when_missing_pattern() -> None:
    hyp = Hypothesis(
        id="hyp-pass-1",
        claim="Bug fix ensures test passes with verified label",
        expected_observation=ExpectedObservation(
            expected_outcome="pass",
            expected_exit_codes=[0],
            match_patterns=["VERIFIED_LABEL"],
        ),
    )
    evi = _make_evidence(exit_code=0, stdout="ok")
    finding = evaluate_hypothesis(hyp, evi)
    assert finding.verdict == Verdict.INCONCLUSIVE
    assert "Missing expected output patterns" in finding.reason


def test_regression_failure_without_signature_is_inconclusive() -> None:
    # Bare exit code 1 without match_patterns or failure_mechanism must NOT produce supported
    hyp = Hypothesis(
        id="hyp-no-sig",
        claim="Function regresses",
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            expected_exit_codes=[1],
            match_patterns=[],
        ),
    )
    evi = _make_evidence(exit_code=1, stdout="FAILED test_any")
    finding = evaluate_hypothesis(hyp, evi)
    assert finding.verdict == Verdict.INCONCLUSIVE
    assert "no diagnostic signature" in finding.reason


def test_regression_uses_failure_mechanism_as_signature() -> None:
    hyp = Hypothesis(
        id="hyp-mech",
        claim="Function regresses with IndexError",
        failure_mechanism="IndexError: list index out of range",
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            expected_exit_codes=[1],
            match_patterns=[],  # empty, fallback to failure_mechanism
        ),
    )
    evi = _make_evidence(exit_code=1, stdout="FAILED - IndexError: list index out of range")
    finding = evaluate_hypothesis(hyp, evi)
    assert finding.verdict == Verdict.SUPPORTED


def test_expected_error_outcome() -> None:
    hyp = Hypothesis(
        id="hyp-err",
        claim="Invocation triggers collection error",
        expected_observation=ExpectedObservation(
            expected_outcome="error",
            expected_exit_codes=[2],
            match_patterns=["UsageError"],
        ),
    )
    evi_err = _make_evidence(exit_code=2, stderr="ERROR: UsageError invalid options")
    assert evaluate_hypothesis(hyp, evi_err).verdict == Verdict.SUPPORTED

    evi_clean = _make_evidence(exit_code=0, stdout="clean")
    assert evaluate_hypothesis(hyp, evi_clean).verdict == Verdict.REFUTED


def test_evaluator_invalid_regex_and_contradict_non_match() -> None:
    from verifycraft.core.evaluator import _pattern_matches

    # 1. Invalid regex fallback to literal substring
    assert _pattern_matches("[invalid(regex", "Error: [invalid(regex happened") is True
    assert _pattern_matches("[invalid(regex", "Different text") is False

    # 2. Contradict pattern does not match, allowing evaluation to proceed
    hyp = Hypothesis(
        id="hyp-cont",
        claim="Regression occurs",
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            expected_exit_codes=[1],
            match_patterns=["KnownFailure"],
            contradict_patterns=["UnexpectedSuccess", "ShouldNotHappen"],
        ),
    )
    evi = _make_evidence(exit_code=1, stdout="KnownFailure observed")
    finding = evaluate_hypothesis(hyp, evi)
    assert finding.verdict == Verdict.SUPPORTED

    # 3. Unhandled outcome fallback at line 168
    object.__setattr__(hyp.expected_observation, "expected_outcome", "unknown_outcome")
    finding_unhandled = evaluate_hypothesis(hyp, evi)
    assert finding_unhandled.verdict == Verdict.INCONCLUSIVE
    assert "decisively categorized" in finding_unhandled.reason
