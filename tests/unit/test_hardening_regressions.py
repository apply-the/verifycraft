"""Regression tests for release-blocking hardening:
1. Fingerprint integrity: repo changes invalidate evidence even without target_files.
2. Fingerprint integrity: fail closed when reliable fingerprinting is impossible.
3. Cached evidence validation: mismatched check_id must be rejected.
4. Cached evidence validation: untrusted_code=True policy cannot be bypassed.
5. Cached evidence validation: disallowed check_type policy cannot be bypassed.
6. Cached evidence validation: incompatible evidence_type cannot be accepted.
7. Cached evidence validation: invalid provenance must be rejected.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from verifycraft import (
    CheckSpec,
    CheckType,
    Evidence,
    ExecutionMode,
    ExecutionPolicy,
    ExpectedObservation,
    FingerprintError,
    Hypothesis,
    Verdict,
    VerificationEngine,
    VerificationInput,
)
from verifycraft.core.fingerprint import (
    compute_fingerprint,
    is_evidence_stale,
)


def _setup_test_repo(tmp_path: Path) -> tuple[Path, Path]:
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    src_file = src_dir / "auth.py"
    src_file.write_text("def authenticate(): return True\n", encoding="utf-8")

    test_file = tests_dir / "test_auth.py"
    test_file.write_text(
        "from src.auth import authenticate\ndef test_auth(): assert authenticate() is True\n",
        encoding="utf-8",
    )
    return src_file, test_file


# ---------------------------------------------------------------------------
# Defect 1: Fingerprint integrity
# ---------------------------------------------------------------------------


def test_fingerprint_invalidated_when_relevant_repo_file_changes_without_target_files(
    tmp_path: Path,
) -> None:
    """When a relevant repository file changes, previous evidence must be invalidated,
    even if the caller omitted that file from target_files."""
    src_file, _ = _setup_test_repo(tmp_path)

    spec = CheckSpec(
        check_id="chk-auth",
        check_type=CheckType.PYTEST,
        target_path="tests/test_auth.py",
    )

    # Caller omits target_files (defaults to empty)
    fp1 = compute_fingerprint(
        repo_path=str(tmp_path),
        revision="rev1",
        diff=None,
        target_files=[],
        check_spec=spec,
    )

    # Modify the source file in the repository without caller updating target_files
    src_file.write_text("def authenticate(): raise ValueError('broken')\n", encoding="utf-8")

    fp2 = compute_fingerprint(
        repo_path=str(tmp_path),
        revision="rev1",
        diff=None,
        target_files=[],
        check_spec=spec,
    )

    assert fp1 != fp2, "Fingerprint failed to change when relevant repo file src/auth.py changed"

    old_evidence = Evidence(
        evidence_id="evi-1",
        check_id="chk-auth",
        evidence_type="pytest_execution",
        exit_code=0,
        fingerprint=fp1,
    )
    assert is_evidence_stale(old_evidence, fp2) is True


def test_fingerprint_fails_closed_when_repo_path_does_not_exist() -> None:
    """Fingerprinting must fail closed (raise FingerprintError) when repo_path is missing."""
    spec = CheckSpec(
        check_id="chk-1",
        check_type=CheckType.PYTEST,
        target_path="tests/test_something.py",
    )
    with pytest.raises(FingerprintError) as exc_info:
        compute_fingerprint(
            repo_path="/nonexistent/directory/path/that/cannot/exist",
            revision="HEAD",
            diff=None,
            target_files=[],
            check_spec=spec,
        )
    assert "does not exist" in str(exc_info.value).lower()


def test_fingerprint_fails_closed_when_target_file_missing_on_disk(tmp_path: Path) -> None:
    """Fingerprinting must fail closed when check target_path does not exist on disk."""
    spec = CheckSpec(
        check_id="chk-1",
        check_type=CheckType.PYTEST,
        target_path="tests/test_does_not_exist.py",
    )
    with pytest.raises(FingerprintError) as exc_info:
        compute_fingerprint(
            repo_path=str(tmp_path),
            revision="HEAD",
            diff=None,
            target_files=[],
            check_spec=spec,
        )
    assert "does not exist" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Defect 2: Cached evidence validation & policy bypass
# ---------------------------------------------------------------------------


def test_cached_evidence_rejected_on_check_id_mismatch(tmp_path: Path) -> None:
    """Cached evidence must be rejected if check_id does not match the check spec."""
    _setup_test_repo(tmp_path)
    spec = CheckSpec(
        check_id="chk-auth-pytest",
        check_type=CheckType.PYTEST,
        target_path="tests/test_auth.py",
    )
    hyp = Hypothesis(
        id="hyp-1",
        claim="Auth failure",
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            expected_exit_codes=[1],
            match_patterns=["ValueError"],
        ),
    )
    v_input = VerificationInput(
        repo_path=str(tmp_path),
        revision="rev1",
        hypothesis=hyp,
        check_spec=spec,
    )
    fp = compute_fingerprint(
        repo_path=str(tmp_path),
        revision="rev1",
        diff=None,
        target_files=[],
        check_spec=spec,
    )

    # Evidence with a mismatched check_id
    mismatched_evidence = Evidence(
        evidence_id="evi-1",
        check_id="chk-DIFFERENT-check",
        evidence_type="pytest_execution",
        exit_code=1,
        stdout="ValueError: broken",
        fingerprint=fp,
    )

    engine = VerificationEngine()
    policy = ExecutionPolicy(mode=ExecutionMode.TRUSTED_LOCAL)
    result = engine.verify(v_input, policy=policy, cached_evidence=[mismatched_evidence])

    assert result.verdict == Verdict.BLOCKED
    assert "check_id" in (result.blocked_reason or "").lower()


def test_cached_evidence_cannot_bypass_untrusted_code_policy(tmp_path: Path) -> None:
    """Cached evidence must not bypass untrusted_code=True policy restriction."""
    _setup_test_repo(tmp_path)
    spec = CheckSpec(
        check_id="chk-auth-pytest",
        check_type=CheckType.PYTEST,
        target_path="tests/test_auth.py",
    )
    hyp = Hypothesis(
        id="hyp-1",
        claim="Auth failure",
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            expected_exit_codes=[1],
            match_patterns=["ValueError"],
        ),
    )
    v_input = VerificationInput(
        repo_path=str(tmp_path),
        revision="rev1",
        hypothesis=hyp,
        check_spec=spec,
    )
    fp = compute_fingerprint(
        repo_path=str(tmp_path),
        revision="rev1",
        diff=None,
        target_files=[],
        check_spec=spec,
    )

    manufactured_evidence = Evidence(
        evidence_id="evi-manufactured",
        check_id="chk-auth-pytest",
        evidence_type="pytest_execution",
        exit_code=1,
        stdout="ValueError: broken",
        fingerprint=fp,
    )

    engine = VerificationEngine()
    # untrusted_code is True: policy must BLOCK execution and NOT trust cached evidence
    # to manufacture a supported verdict
    policy = ExecutionPolicy(mode=ExecutionMode.TRUSTED_LOCAL, untrusted_code=True)
    result = engine.verify(v_input, policy=policy, cached_evidence=[manufactured_evidence])

    assert result.verdict == Verdict.BLOCKED
    assert "untrusted" in (result.blocked_reason or "").lower()


def test_cached_evidence_cannot_bypass_disallowed_check_type(tmp_path: Path) -> None:
    """Cached evidence cannot bypass check_type allowlist in execution policy."""
    _setup_test_repo(tmp_path)
    spec = CheckSpec(
        check_id="chk-auth-pytest",
        check_type=CheckType.PYTEST,
        target_path="tests/test_auth.py",
    )
    hyp = Hypothesis(
        id="hyp-1",
        claim="Auth failure",
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            expected_exit_codes=[1],
            match_patterns=["ValueError"],
        ),
    )
    v_input = VerificationInput(
        repo_path=str(tmp_path),
        revision="rev1",
        hypothesis=hyp,
        check_spec=spec,
    )
    fp = compute_fingerprint(
        repo_path=str(tmp_path),
        revision="rev1",
        diff=None,
        target_files=[],
        check_spec=spec,
    )

    evidence = Evidence(
        evidence_id="evi-1",
        check_id="chk-auth-pytest",
        evidence_type="pytest_execution",
        exit_code=1,
        stdout="ValueError: broken",
        fingerprint=fp,
    )

    engine = VerificationEngine()
    # Policy only allows STATIC, disallowing PYTEST
    policy = ExecutionPolicy(
        mode=ExecutionMode.TRUSTED_LOCAL,
        allowed_check_types=[CheckType.STATIC],
    )
    result = engine.verify(v_input, policy=policy, cached_evidence=[evidence])

    assert result.verdict == Verdict.BLOCKED
    assert "not permitted by policy" in (result.blocked_reason or "").lower()


def test_cached_evidence_rejected_on_incompatible_evidence_type(tmp_path: Path) -> None:
    """Cached evidence with evidence_type incompatible with check_spec must be rejected."""
    _setup_test_repo(tmp_path)
    spec = CheckSpec(
        check_id="chk-auth-pytest",
        check_type=CheckType.PYTEST,
        target_path="tests/test_auth.py",
    )
    hyp = Hypothesis(
        id="hyp-1",
        claim="Auth failure",
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            expected_exit_codes=[1],
            match_patterns=["ValueError"],
        ),
    )
    v_input = VerificationInput(
        repo_path=str(tmp_path),
        revision="rev1",
        hypothesis=hyp,
        check_spec=spec,
    )
    fp = compute_fingerprint(
        repo_path=str(tmp_path),
        revision="rev1",
        diff=None,
        target_files=[],
        check_spec=spec,
    )

    # Incompatible evidence_type (e.g. fake_execution for a pytest check under trusted_local)
    evidence = Evidence(
        evidence_id="evi-fake-1",
        check_id="chk-auth-pytest",
        evidence_type="fake_execution",
        exit_code=1,
        stdout="ValueError: broken",
        fingerprint=fp,
    )

    engine = VerificationEngine()
    policy = ExecutionPolicy(mode=ExecutionMode.TRUSTED_LOCAL)
    result = engine.verify(v_input, policy=policy, cached_evidence=[evidence])

    assert result.verdict == Verdict.BLOCKED
    assert "fake" in (result.blocked_reason or "").lower()


def test_cached_evidence_rejected_on_invalid_provenance(tmp_path: Path) -> None:
    """Cached evidence with invalid provenance (e.g. empty id) must be rejected."""
    _setup_test_repo(tmp_path)
    spec = CheckSpec(
        check_id="chk-auth-pytest",
        check_type=CheckType.PYTEST,
        target_path="tests/test_auth.py",
    )
    hyp = Hypothesis(
        id="hyp-1",
        claim="Auth failure",
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            expected_exit_codes=[1],
            match_patterns=["ValueError"],
        ),
    )
    v_input = VerificationInput(
        repo_path=str(tmp_path),
        revision="rev1",
        hypothesis=hyp,
        check_spec=spec,
    )
    fp = compute_fingerprint(
        repo_path=str(tmp_path),
        revision="rev1",
        diff=None,
        target_files=[],
        check_spec=spec,
    )

    # Evidence with empty evidence_id
    evidence_bad_id = Evidence(
        evidence_id="   ",
        check_id="chk-auth-pytest",
        evidence_type="pytest_execution",
        exit_code=1,
        stdout="ValueError: broken",
        fingerprint=fp,
    )

    engine = VerificationEngine()
    policy = ExecutionPolicy(mode=ExecutionMode.TRUSTED_LOCAL)
    result = engine.verify(v_input, policy=policy, cached_evidence=[evidence_bad_id])

    assert result.verdict == Verdict.BLOCKED
    assert "provenance" in (result.blocked_reason or "").lower()

    # Evidence with future timestamp
    evidence_future = Evidence(
        evidence_id="evi-future",
        check_id="chk-auth-pytest",
        evidence_type="pytest_execution",
        exit_code=1,
        stdout="ValueError: broken",
        fingerprint=fp,
        collected_at=datetime.now(UTC) + timedelta(days=365),
    )
    result_future = engine.verify(v_input, policy=policy, cached_evidence=[evidence_future])
    assert result_future.verdict == Verdict.BLOCKED
    assert "provenance" in (result_future.blocked_reason or "").lower()


def test_fingerprint_scope_fails_closed_when_over_500_relevant_files(tmp_path: Path) -> None:
    """Verification scope must fail closed (raise FingerprintError) when more than 500
    relevant files are discovered in the repository."""
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    # Create test file
    (tests_dir / "test_main.py").write_text("def test_ok(): pass\n", encoding="utf-8")

    # Create 501 python files in src/
    for i in range(501):
        (src_dir / f"mod_{i}.py").write_text(f"x = {i}\n", encoding="utf-8")

    spec = CheckSpec(
        check_id="chk-large",
        check_type=CheckType.PYTEST,
        target_path="tests/test_main.py",
    )

    with pytest.raises(FingerprintError) as exc_info:
        compute_fingerprint(
            repo_path=str(tmp_path),
            revision="rev1",
            diff=None,
            target_files=[],
            check_spec=spec,
        )

    assert "exceeded maximum limit of 500" in str(exc_info.value)


def test_fingerprint_invalidated_when_root_level_python_module_changes(tmp_path: Path) -> None:
    """Changes to root-level Python modules must invalidate previous evidence,
    even when src/ and tests/ directories exist and callers omit target_files."""
    _setup_test_repo(tmp_path)

    # Add a root-level Python module alongside src/ and tests/
    root_module = tmp_path / "config.py"
    root_module.write_text("DEBUG = False\n", encoding="utf-8")

    spec = CheckSpec(
        check_id="chk-auth",
        check_type=CheckType.PYTEST,
        target_path="tests/test_auth.py",
    )

    fp1 = compute_fingerprint(
        repo_path=str(tmp_path),
        revision="rev1",
        diff=None,
        target_files=[],
        check_spec=spec,
    )

    # Modify root-level Python module without caller declaring target_files
    root_module.write_text("DEBUG = True\n", encoding="utf-8")

    fp2 = compute_fingerprint(
        repo_path=str(tmp_path),
        revision="rev1",
        diff=None,
        target_files=[],
        check_spec=spec,
    )

    assert fp1 != fp2, "Fingerprint failed to change when root-level config.py was modified"

    old_evidence = Evidence(
        evidence_id="evi-1",
        check_id="chk-auth",
        evidence_type="pytest_execution",
        exit_code=0,
        fingerprint=fp1,
    )
    assert is_evidence_stale(old_evidence, fp2) is True


def test_caller_provided_cached_evidence_disabled_without_independent_auth(
    tmp_path: Path,
) -> None:
    """Caller-provided cached evidence must NOT be authoritative without independent
    provenance authentication, even if metadata and fingerprint match perfectly."""
    _setup_test_repo(tmp_path)
    spec = CheckSpec(
        check_id="chk-auth-pytest",
        check_type=CheckType.PYTEST,
        target_path="tests/test_auth.py",
    )
    hyp = Hypothesis(
        id="hyp-1",
        claim="Auth failure",
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            expected_exit_codes=[1],
            match_patterns=["ValueError"],
        ),
    )
    v_input = VerificationInput(
        repo_path=str(tmp_path),
        revision="rev1",
        hypothesis=hyp,
        check_spec=spec,
    )
    fp = compute_fingerprint(
        repo_path=str(tmp_path),
        revision="rev1",
        diff=None,
        target_files=[],
        check_spec=spec,
    )

    matching_evidence = Evidence(
        evidence_id="evi-caller-1",
        check_id="chk-auth-pytest",
        evidence_type="pytest_execution",
        exit_code=1,
        stdout="ValueError: broken",
        fingerprint=fp,
    )

    # Default engine without independent provenance authenticator
    engine = VerificationEngine()
    policy = ExecutionPolicy(mode=ExecutionMode.TRUSTED_LOCAL)
    result = engine.verify(v_input, policy=policy, cached_evidence=[matching_evidence])

    assert result.verdict == Verdict.BLOCKED
    assert (
        "independent provenance authentication is required" in (result.blocked_reason or "").lower()
    )


def test_caller_provided_cached_evidence_accepted_with_independent_auth(
    tmp_path: Path,
) -> None:
    """Caller-provided cached evidence can only be evaluated when an independent
    provenance authenticator validates the evidence."""
    _setup_test_repo(tmp_path)
    spec = CheckSpec(
        check_id="chk-auth-pytest",
        check_type=CheckType.PYTEST,
        target_path="tests/test_auth.py",
    )
    hyp = Hypothesis(
        id="hyp-1",
        claim="Auth failure",
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            expected_exit_codes=[1],
            match_patterns=["ValueError"],
        ),
    )
    v_input = VerificationInput(
        repo_path=str(tmp_path),
        revision="rev1",
        hypothesis=hyp,
        check_spec=spec,
    )
    fp = compute_fingerprint(
        repo_path=str(tmp_path),
        revision="rev1",
        diff=None,
        target_files=[],
        check_spec=spec,
    )

    matching_evidence = Evidence(
        evidence_id="evi-signed-123",
        check_id="chk-auth-pytest",
        evidence_type="pytest_execution",
        exit_code=1,
        stdout="ValueError: broken",
        fingerprint=fp,
    )

    # Engine configured with independent provenance authenticator
    def my_authenticator(evi: Evidence) -> bool:
        return evi.evidence_id == "evi-signed-123"

    engine = VerificationEngine(provenance_authenticator=my_authenticator)
    policy = ExecutionPolicy(mode=ExecutionMode.TRUSTED_LOCAL)
    result = engine.verify(v_input, policy=policy, cached_evidence=[matching_evidence])

    assert result.verdict == Verdict.SUPPORTED
    assert len(result.evidence) == 1
