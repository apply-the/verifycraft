"""Benchmark fixture loader."""

from pathlib import Path

from benchmarks.models import BenchmarkCase
from verifycraft.contracts import (
    CheckSpec,
    CheckType,
    Evidence,
    ExecutionMode,
    ExecutionPolicy,
    ExpectedObservation,
    Hypothesis,
    ResourceUsage,
    Verdict,
    VerificationInput,
)

FIXTURES_DIR = Path(__file__).parent.resolve()
REPOS_DIR = FIXTURES_DIR / "repos"


def load_benchmark_cases() -> list[BenchmarkCase]:
    """Load the canonical 5 benchmark cases representing the Stage 0/1 test suite."""

    # Case 1: Real regression with reproducible failing test -> SUPPORTED
    real_reg_repo = str(REPOS_DIR / "real_regression")
    diff_reg = (
        "--- a/src/auth.py\n+++ b/src/auth.py\n@@ -2,2 +2,1 @@\n"
        "-    return user.get('role', 'guest')\n+    return user['role']"
    )
    case_real_regression = BenchmarkCase(
        case_id="case_01_real_regression",
        name="Real Regression via Pytest",
        description="A pull request diff introduces a NoneType subscript regression in auth.py. "
        "The targeted test test_anonymous_user fails with TypeError as hypothesized.",
        ground_truth_verdict=Verdict.SUPPORTED,
        verification_input=VerificationInput(
            repo_path=real_reg_repo,
            revision="git-commit-bug-01",
            diff=diff_reg,
            hypothesis=Hypothesis(
                id="hyp-auth-null",
                claim="Passing None to get_user_role triggers TypeError subscript error",
                failure_mechanism="TypeError: 'NoneType' object is not subscriptable",
                affected_locations=["src/auth.py:3"],
                expected_observation=ExpectedObservation(
                    expected_outcome="fail",
                    expected_exit_codes=[1],
                    match_patterns=["TypeError: 'NoneType' object is not subscriptable"],
                ),
            ),
            check_spec=CheckSpec(
                check_id="chk-auth-pytest",
                check_type=CheckType.PYTEST,
                target_path="tests/test_auth.py",
                args=["-q"],
            ),
        ),
        policy=ExecutionPolicy(
            mode=ExecutionMode.TRUSTED_LOCAL,
            timeout_seconds=10.0,
        ),
    )

    # Case 2: Hypothesis contradicted by targeted check -> REFUTED
    contra_repo = str(REPOS_DIR / "contradicted_hypothesis")
    case_contradicted = BenchmarkCase(
        case_id="case_02_contradicted_hypothesis",
        name="Hypothesis Contradicted by Passing Test",
        description="A reviewer hypothesized that discount calculation fails on float rates. "
        "The targeted test passes with exit code 0, refuting the claim.",
        ground_truth_verdict=Verdict.REFUTED,
        verification_input=VerificationInput(
            repo_path=contra_repo,
            revision="git-commit-contra-02",
            diff="--- a/src/discount.py\n+++ b/src/discount.py",
            hypothesis=Hypothesis(
                id="hyp-discount-float",
                claim="Float discount rate causes TypeError during calculation",
                failure_mechanism="TypeError",
                affected_locations=["src/discount.py:4"],
                expected_observation=ExpectedObservation(
                    expected_outcome="fail",
                    expected_exit_codes=[1],
                    match_patterns=["TypeError"],
                ),
            ),
            check_spec=CheckSpec(
                check_id="chk-discount-pytest",
                check_type=CheckType.PYTEST,
                target_path="tests/test_discount.py",
                args=["-q"],
            ),
        ),
        policy=ExecutionPolicy(
            mode=ExecutionMode.TRUSTED_LOCAL,
            timeout_seconds=10.0,
        ),
    )

    # Case 3: Inconclusive verification -> INCONCLUSIVE
    inconclusive_repo = str(REPOS_DIR / "inconclusive_case")
    case_inconclusive = BenchmarkCase(
        case_id="case_03_inconclusive_signature",
        name="Inconclusive Due to Unmatched Failure Signature",
        description="Hypothesis asserts a TimeoutError occurs, but the check fails with an "
        "unrelated ModuleNotFoundError. The engine must report inconclusive.",
        ground_truth_verdict=Verdict.INCONCLUSIVE,
        verification_input=VerificationInput(
            repo_path=inconclusive_repo,
            revision="git-commit-inc-03",
            diff="--- a/src/service.py\n+++ b/src/service.py",
            hypothesis=Hypothesis(
                id="hyp-service-timeout",
                claim="Service call fails due to network TimeoutError",
                failure_mechanism="TimeoutError",
                affected_locations=["src/service.py:3"],
                expected_observation=ExpectedObservation(
                    expected_outcome="fail",
                    expected_exit_codes=[1],
                    match_patterns=["TimeoutError"],
                ),
            ),
            check_spec=CheckSpec(
                check_id="chk-service-pytest",
                check_type=CheckType.PYTEST,
                target_path="tests/test_service.py",
                args=["-q"],
            ),
        ),
        policy=ExecutionPolicy(
            mode=ExecutionMode.TRUSTED_LOCAL,
            timeout_seconds=10.0,
        ),
    )

    # Case 4: Blocked verification -> BLOCKED
    case_blocked = BenchmarkCase(
        case_id="case_04_blocked_untrusted_code",
        name="Blocked Untrusted Pull Request Code",
        description="A pull request from an untrusted public fork is analyzed. The policy "
        "marks untrusted_code=True without container isolation. Execution is blocked.",
        ground_truth_verdict=Verdict.BLOCKED,
        verification_input=VerificationInput(
            repo_path=real_reg_repo,
            revision="git-commit-pr-untrusted-04",
            diff="--- a/src/auth.py\n+++ b/src/auth.py",
            hypothesis=Hypothesis(
                id="hyp-untrusted-pr",
                claim="PR introduces regression",
                expected_observation=ExpectedObservation(expected_outcome="fail"),
            ),
            check_spec=CheckSpec(
                check_id="chk-untrusted",
                check_type=CheckType.PYTEST,
                target_path="tests/test_auth.py",
            ),
        ),
        policy=ExecutionPolicy(
            mode=ExecutionMode.TRUSTED_LOCAL,
            untrusted_code=True,  # Safety trigger!
        ),
    )

    # Case 5: Stale-evidence rejection -> BLOCKED
    stale_evidence = Evidence(
        evidence_id="evi-stale-05",
        check_id="chk-stale",
        evidence_type="pytest_execution",
        exit_code=1,
        stdout="TypeError: 'NoneType' object is not subscriptable",
        fingerprint="sha256:stale_outdated_revision_fingerprint",
        resource_usage=ResourceUsage(wall_time_seconds=0.1),
    )
    case_stale = BenchmarkCase(
        case_id="case_05_stale_evidence",
        name="Stale Evidence Mismatch Rejection",
        description="Cached evidence from an earlier revision is presented. The engine detects "
        "a fingerprint mismatch and rejects stale evidence.",
        ground_truth_verdict=Verdict.BLOCKED,
        verification_input=VerificationInput(
            repo_path=real_reg_repo,
            revision="git-commit-new-05",
            diff="--- a/src/auth.py\n+++ b/src/auth.py\n# new changes",
            hypothesis=Hypothesis(
                id="hyp-stale",
                claim="Hypothesis evaluated against current code",
                expected_observation=ExpectedObservation(
                    expected_outcome="fail",
                    expected_exit_codes=[1],
                    match_patterns=["TypeError"],
                ),
            ),
            check_spec=CheckSpec(
                check_id="chk-stale",
                check_type=CheckType.PYTEST,
                target_path="tests/test_auth.py",
            ),
        ),
        policy=ExecutionPolicy(
            mode=ExecutionMode.DRY_RUN,
        ),
        cached_evidence=[stale_evidence],
    )

    return [
        case_real_regression,
        case_contradicted,
        case_inconclusive,
        case_blocked,
        case_stale,
    ]
