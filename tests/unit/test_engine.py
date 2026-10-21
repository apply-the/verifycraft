"""Unit tests for VerificationEngine."""

from verifycraft import (
    CheckSpec,
    CheckType,
    Evidence,
    ExecutionMode,
    ExecutionPolicy,
    ExpectedObservation,
    FakeRunner,
    Hypothesis,
    ResourceUsage,
    Verdict,
    VerificationEngine,
    VerificationInput,
)
from verifycraft.core.fingerprint import compute_fingerprint


def _make_input(
    claim: str, expected_code: int = 1, pattern: str = "RegressionError"
) -> VerificationInput:
    hyp = Hypothesis(
        id="hyp-1",
        claim=claim,
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            expected_exit_codes=[expected_code],
            match_patterns=[pattern],
        ),
    )
    spec = CheckSpec(
        check_id="chk-1",
        check_type=CheckType.FAKE,
        target_path="tests/test_reg.py",
    )
    return VerificationInput(
        repo_path=".",
        revision="rev-test",
        diff="--- a\n+++ b",
        hypothesis=hyp,
        check_spec=spec,
    )


def test_engine_supported_verdict() -> None:
    fake_runner = FakeRunner()
    fake_runner.set_response(
        "chk-1", exit_code=1, stdout="FAILED - RegressionError: bug introduced"
    )

    engine = VerificationEngine(runner=fake_runner)
    policy = ExecutionPolicy(mode=ExecutionMode.FAKE)
    v_input = _make_input("RegressionError introduced")

    result = engine.verify(v_input, policy=policy)

    assert result.verdict == Verdict.SUPPORTED
    assert len(result.findings) == 1
    assert result.findings[0].verdict == Verdict.SUPPORTED
    assert len(result.evidence) == 1
    assert result.evidence[0].exit_code == 1
    assert result.fingerprint.startswith("sha256:")


def test_engine_refuted_verdict() -> None:
    fake_runner = FakeRunner()
    fake_runner.set_response("chk-1", exit_code=0, stdout="1 passed in 0.01s")

    engine = VerificationEngine(runner=fake_runner)
    policy = ExecutionPolicy(mode=ExecutionMode.FAKE)
    v_input = _make_input("RegressionError introduced")

    result = engine.verify(v_input, policy=policy)

    assert result.verdict == Verdict.REFUTED
    assert len(result.findings) == 1
    assert result.findings[0].verdict == Verdict.REFUTED
    assert "exit code 0" in result.findings[0].reason


def test_engine_inconclusive_verdict_on_unexpected_error() -> None:
    fake_runner = FakeRunner()
    fake_runner.set_response("chk-1", exit_code=1, stdout="SyntaxError: invalid syntax")

    engine = VerificationEngine(runner=fake_runner)
    policy = ExecutionPolicy(mode=ExecutionMode.FAKE)
    v_input = _make_input("RegressionError introduced")

    result = engine.verify(v_input, policy=policy)

    assert result.verdict == Verdict.INCONCLUSIVE
    assert result.inconclusive_reason is not None
    assert "diagnostic signature" in result.inconclusive_reason


def test_engine_blocked_by_default_dry_run() -> None:
    engine = VerificationEngine()
    v_input = _make_input("Dry run test")

    # Default policy is dry_run
    result = engine.verify(v_input)

    assert result.verdict == Verdict.BLOCKED
    assert result.blocked_reason is not None
    assert "dry_run" in result.blocked_reason
    assert len(result.evidence) == 0


def test_engine_blocked_on_untrusted_code() -> None:
    engine = VerificationEngine()
    policy = ExecutionPolicy(mode=ExecutionMode.TRUSTED_LOCAL, untrusted_code=True)
    v_input = _make_input("Untrusted code test")

    result = engine.verify(v_input, policy=policy)

    assert result.verdict == Verdict.BLOCKED
    assert result.blocked_reason is not None
    assert "isolated container" in result.blocked_reason


def test_engine_rejects_stale_cached_evidence() -> None:
    engine = VerificationEngine()
    v_input = _make_input("Stale evidence test")

    stale_evi = Evidence(
        evidence_id="evi-old",
        check_id="chk-1",
        evidence_type="pytest_execution",
        exit_code=1,
        stdout="RegressionError",
        fingerprint="sha256:different_old_fingerprint",
        resource_usage=ResourceUsage(),
    )

    result = engine.verify(v_input, cached_evidence=[stale_evi])

    assert result.verdict == Verdict.BLOCKED
    assert result.blocked_reason is not None
    assert "Stale evidence rejected" in result.blocked_reason


def test_engine_evaluates_fresh_cached_evidence() -> None:
    # When independently authenticated, fresh cached evidence is accepted
    engine = VerificationEngine(provenance_authenticator=lambda evi: True)
    v_input = _make_input("Fresh cached evidence test")

    current_fp = compute_fingerprint(
        repo_path=v_input.repo_path,
        revision=v_input.revision,
        diff=v_input.diff,
        target_files=v_input.target_files,
        check_spec=v_input.check_spec,
    )
    fresh_evi = Evidence(
        evidence_id="evi-fresh",
        check_id="chk-1",
        evidence_type="pytest_execution",
        exit_code=1,
        stdout="RegressionError",
        fingerprint=current_fp,
        resource_usage=ResourceUsage(),
    )

    result = engine.verify(v_input, cached_evidence=[fresh_evi])
    assert result.verdict == Verdict.SUPPORTED
    assert len(result.evidence) == 1


def test_engine_rejects_unauthenticated_caller_cached_evidence() -> None:
    # Without independent authentication, metadata consistency alone cannot authorize evidence
    engine = VerificationEngine()  # No authenticator provided
    v_input = _make_input("Unauthenticated cached evidence test")

    current_fp = compute_fingerprint(
        repo_path=v_input.repo_path,
        revision=v_input.revision,
        diff=v_input.diff,
        target_files=v_input.target_files,
        check_spec=v_input.check_spec,
    )
    fresh_evi = Evidence(
        evidence_id="evi-fresh",
        check_id="chk-1",
        evidence_type="pytest_execution",
        exit_code=1,
        stdout="RegressionError",
        fingerprint=current_fp,
        resource_usage=ResourceUsage(),
    )

    result = engine.verify(v_input, cached_evidence=[fresh_evi])
    assert result.verdict == Verdict.BLOCKED
    assert "provenance authentication is required" in (result.blocked_reason or "").lower()


def test_engine_unsupported_mode_resolution_error() -> None:
    engine = VerificationEngine()
    v_input = _make_input("Test unsupported mode")
    policy = ExecutionPolicy(
        mode=ExecutionMode.ISOLATED_CONTAINER,
        untrusted_code=False,
    )
    result = engine.verify(v_input, policy=policy)
    assert result.verdict == Verdict.BLOCKED
    assert "Failed to resolve execution runner" in (result.blocked_reason or "")


def test_engine_rejects_fake_runner_under_trusted_local_policy() -> None:
    fake_runner = FakeRunner()
    engine = VerificationEngine(runner=fake_runner)
    v_input = _make_input("Test fake runner under trusted_local")
    policy = ExecutionPolicy(mode=ExecutionMode.TRUSTED_LOCAL, untrusted_code=False)

    result = engine.verify(v_input, policy=policy)
    assert result.verdict == Verdict.BLOCKED
    assert "Incompatible runner" in (result.blocked_reason or "")
    assert "Simulation cannot be used" in (result.blocked_reason or "")


def test_engine_rejects_fake_cached_evidence_under_trusted_local_policy() -> None:
    engine = VerificationEngine()
    v_input = _make_input("Test fake cached evidence under trusted_local")
    current_fp = compute_fingerprint(
        repo_path=v_input.repo_path,
        revision=v_input.revision,
        diff=v_input.diff,
        target_files=v_input.target_files,
        check_spec=v_input.check_spec,
    )
    fake_cached_evi = Evidence(
        evidence_id="evi-fake-cached",
        check_id="chk-1",
        evidence_type="fake_execution",
        exit_code=1,
        stdout="RegressionError",
        fingerprint=current_fp,
    )
    policy = ExecutionPolicy(mode=ExecutionMode.TRUSTED_LOCAL, untrusted_code=False)

    result = engine.verify(v_input, policy=policy, cached_evidence=[fake_cached_evi])
    assert result.verdict == Verdict.BLOCKED
    assert "cached fake evidence cannot be accepted" in (result.blocked_reason or "")
