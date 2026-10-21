"""Example: Programmatic regression verification using VerifyCraft.

Demonstrates verifying an explicitly supplied regression hypothesis against
an approved pytest check using the VerificationEngine.
"""

from __future__ import annotations

from pathlib import Path

from verifycraft import (
    CheckSpec,
    CheckType,
    ExecutionMode,
    ExecutionPolicy,
    ExpectedObservation,
    Hypothesis,
    VerificationEngine,
    VerificationInput,
)


def main() -> None:
    # 1. Define an explicit, testable regression hypothesis
    hypothesis = Hypothesis(
        id="hyp-auth-null-01",
        claim="Passing None to get_user_role triggers TypeError subscript error",
        failure_mechanism="TypeError: 'NoneType' object is not subscriptable",
        affected_locations=["src/auth.py:3"],
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            expected_exit_codes=[1],
            match_patterns=["TypeError: 'NoneType' object is not subscriptable"],
        ),
    )

    # 2. Specify the approved verification check
    # Point to the benchmark fixture repo for a reproducible demonstration
    repo_path = (
        Path(__file__).parent.parent / "benchmarks" / "fixtures" / "repos" / "real_regression"
    )

    check_spec = CheckSpec(
        check_id="chk-auth-pytest",
        check_type=CheckType.PYTEST,
        target_path="tests/test_auth.py",
        args=["-q"],
    )

    # 3. Create the typed verification input
    diff_content = (
        "--- a/src/auth.py\n+++ b/src/auth.py\n@@ -2,2 +2,1 @@\n"
        "-    return user.get('role', 'guest')\n+    return user['role']"
    )
    verification_input = VerificationInput(
        repo_path=str(repo_path.resolve()),
        revision="git-sha-example",
        diff=diff_content,
        hypothesis=hypothesis,
        check_spec=check_spec,
    )

    # 4. Configure the execution policy (opt-in to trusted local execution)
    policy = ExecutionPolicy(
        mode=ExecutionMode.TRUSTED_LOCAL,
        timeout_seconds=15.0,
        untrusted_code=False,
    )

    # 5. Execute verification through the engine
    engine = VerificationEngine()
    result = engine.verify(verification_input, policy=policy)

    # 6. Inspect the structured, verifiable result
    print("=" * 60)
    print(" VerifyCraft Programmatic Verification Example")
    print("=" * 60)
    print(f"Verdict:      {result.verdict.value.upper()}")
    print(f"Fingerprint:  {result.fingerprint}")
    print(f"Wall Time:    {result.resource_usage.wall_time_seconds:.4f}s")
    print("-" * 60)

    for finding in result.findings:
        print(f"Finding:      [{finding.verdict.value}] {finding.hypothesis_id}")
        print(f"Reason:       {finding.reason}")

    for evi in result.evidence:
        print("-" * 60)
        print(f"Evidence ID:  {evi.evidence_id} (exit code {evi.exit_code})")
        print(f"Output Excerpt:\n{evi.stdout.strip()}")

    print("=" * 60)
    print("JSON Output Serialization:")
    print(result.to_json(indent=2))


if __name__ == "__main__":
    main()
