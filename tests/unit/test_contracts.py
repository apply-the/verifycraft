"""Unit tests for VerifyCraft public contracts."""

import pytest
from pydantic import ValidationError

from verifycraft.contracts import (
    CheckSpec,
    CheckType,
    Evidence,
    ExecutionMode,
    ExecutionPolicy,
    ExpectedObservation,
    Finding,
    Hypothesis,
    ResourceUsage,
    Verdict,
    VerificationInput,
    VerificationResult,
)


def test_verdict_values() -> None:
    assert Verdict.SUPPORTED.value == "supported"
    assert Verdict.REFUTED.value == "refuted"
    assert Verdict.INCONCLUSIVE.value == "inconclusive"
    assert Verdict.BLOCKED.value == "blocked"


def test_check_spec_argument_security() -> None:
    # Valid arguments
    spec = CheckSpec(
        check_id="test_run",
        check_type=CheckType.PYTEST,
        target_path="tests/test_foo.py",
        args=["-k", "test_item", "-q"],
    )
    assert spec.args == ["-k", "test_item", "-q"]

    # Dangerous shell injection characters
    with pytest.raises(ValidationError, match="Prohibited pattern"):
        CheckSpec(
            check_id="bad",
            check_type=CheckType.PYTEST,
            target_path="tests/test_foo.py",
            args=["-k", "test_item; rm -rf /"],
        )

    # Forbidden pytest flag
    with pytest.raises(ValidationError, match="Prohibited pattern"):
        CheckSpec(
            check_id="bad",
            check_type=CheckType.PYTEST,
            target_path="tests/test_foo.py",
            args=["--override-ini=addopts="],
        )


def test_check_spec_target_path_validation() -> None:
    # Empty target path
    with pytest.raises(ValidationError, match="target_path must not be empty"):
        CheckSpec(
            check_id="chk-empty",
            check_type=CheckType.PYTEST,
            target_path="   ",
        )

    # Absolute target path (POSIX and Windows)
    with pytest.raises(ValidationError, match="target_path must be relative, not absolute"):
        CheckSpec(
            check_id="chk-abs",
            check_type=CheckType.PYTEST,
            target_path="/etc/passwd",
        )
    with pytest.raises(ValidationError, match="target_path must be relative, not absolute"):
        CheckSpec(
            check_id="chk-abs-win",
            check_type=CheckType.PYTEST,
            target_path="C:/test.py",
        )

    # Path traversal target path
    with pytest.raises(ValidationError, match="must not contain '\\.\\.' path traversal"):
        CheckSpec(
            check_id="chk-trav",
            check_type=CheckType.PYTEST,
            target_path="../tests/test_foo.py",
        )


def test_execution_policy_defaults_and_validation() -> None:
    policy = ExecutionPolicy()
    assert policy.mode == ExecutionMode.DRY_RUN
    assert policy.timeout_seconds == 30.0
    assert policy.untrusted_code is False
    assert policy.allow_network is False

    with pytest.raises(ValidationError):
        ExecutionPolicy(timeout_seconds=-5.0)

    with pytest.raises(ValidationError):
        ExecutionPolicy(max_output_bytes=10)


def test_verification_input_validation() -> None:
    hyp = Hypothesis(
        id="hyp-1",
        claim="Function returns None on empty input",
        failure_mechanism="AssertionError",
        affected_locations=["src/parser.py:10"],
    )
    spec = CheckSpec(
        check_id="chk-1",
        check_type=CheckType.PYTEST,
        target_path="tests/test_parser.py",
    )
    inp = VerificationInput(
        repo_path=".",
        revision="main",
        diff="--- a\n+++ b",
        hypothesis=hyp,
        check_spec=spec,
    )
    assert inp.repo_path == "."
    assert inp.revision == "main"

    with pytest.raises(ValidationError):
        VerificationInput(
            repo_path="",
            hypothesis=hyp,
            check_spec=spec,
        )


def test_json_serialization_roundtrip() -> None:
    hyp = Hypothesis(
        id="hyp-1",
        claim="ZeroDivisionError occurs",
        expected_observation=ExpectedObservation(
            expected_exit_codes=[1],
            match_patterns=["ZeroDivisionError"],
            expected_outcome="fail",
        ),
    )
    spec = CheckSpec(
        check_id="chk-1",
        check_type=CheckType.PYTEST,
        target_path="tests/test_math.py",
        args=["-k", "test_div"],
    )
    evidence = Evidence(
        evidence_id="evi-1",
        check_id=spec.check_id,
        evidence_type="pytest_execution",
        exit_code=1,
        stdout="E   ZeroDivisionError: division by zero",
        stderr="",
        output_truncated=False,
        fingerprint="sha256-test-fp",
        resource_usage=ResourceUsage(wall_time_seconds=0.42, output_bytes=38),
    )
    finding = Finding(
        hypothesis_id=hyp.id,
        verdict=Verdict.SUPPORTED,
        reason="Test failed with ZeroDivisionError as predicted.",
        evidence_ids=["evi-1"],
    )
    result = VerificationResult(
        schema_version="verifycraft.v0",
        verdict=Verdict.SUPPORTED,
        findings=[finding],
        evidence=[evidence],
        resource_usage=ResourceUsage(wall_time_seconds=0.45, output_bytes=38),
        fingerprint="sha256-test-fp",
    )

    json_str = result.to_json()
    assert '"schema_version": "verifycraft.v0"' in json_str
    assert '"verdict": "supported"' in json_str

    deserialized = VerificationResult.from_json(json_str)
    assert deserialized.verdict == Verdict.SUPPORTED
    assert len(deserialized.findings) == 1
    assert deserialized.findings[0].hypothesis_id == "hyp-1"
    assert len(deserialized.evidence) == 1
    assert deserialized.evidence[0].exit_code == 1
    assert deserialized.fingerprint == "sha256-test-fp"
