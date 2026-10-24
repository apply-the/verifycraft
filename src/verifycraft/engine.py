"""Verification engine for VerifyCraft.

Coordinates policy enforcement, fingerprint calculation, check runner delegation,
evidence capture, and verdict evaluation into typed results.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from verifycraft.contracts.models import (
    CheckType,
    Evidence,
    ExecutionMode,
    ExecutionPolicy,
    Finding,
    ResourceUsage,
    Verdict,
    VerificationInput,
    VerificationResult,
)
from verifycraft.core.evaluator import evaluate_hypothesis
from verifycraft.core.fingerprint import (
    FingerprintError,
    compute_fingerprint,
    is_evidence_stale,
)
from verifycraft.core.policy import validate_execution_authorization
from verifycraft.core.runner import CheckRunner, FakeRunner, TrustedPytestRunner


def _make_blocked_result(
    reason: str,
    hypothesis_id: str,
    fingerprint: str,
    start_time: float,
    evidence: list[Evidence],
    empty_findings: bool = False,
) -> VerificationResult:
    elapsed = time.perf_counter() - start_time
    findings = (
        []
        if empty_findings
        else [
            Finding(
                hypothesis_id=hypothesis_id,
                verdict=Verdict.BLOCKED,
                reason=reason,
            )
        ]
    )
    return VerificationResult(
        verdict=Verdict.BLOCKED,
        findings=findings,
        evidence=evidence,
        blocked_reason=reason,
        fingerprint=fingerprint,
        resource_usage=ResourceUsage(wall_time_seconds=round(elapsed, 4)),
    )


def _check_evidence_mode_compatibility(
    evi: Evidence,
    policy: ExecutionPolicy,
    check_type: CheckType,
) -> str | None:
    if policy.mode == ExecutionMode.TRUSTED_LOCAL:
        if evi.evidence_type == "fake_execution":
            return (
                "Safety violation: cached fake evidence cannot be accepted under "
                "'trusted_local' policy."
            )
        if check_type == CheckType.PYTEST and evi.evidence_type != "pytest_execution":
            return (
                f"Incompatible evidence: evidence type '{evi.evidence_type}' is "
                "incompatible with pytest check specification."
            )

    if policy.mode == ExecutionMode.FAKE and evi.evidence_type != "fake_execution":
        return (
            f"Incompatible evidence: evidence type '{evi.evidence_type}' is "
            "incompatible with fake policy mode."
        )

    return None


def _compute_fingerprint_or_blocked(
    verification_input: VerificationInput,
    cached_evidence: Sequence[Evidence] | None,
    start_time: float,
) -> tuple[str, VerificationResult | None]:
    try:
        fp = compute_fingerprint(
            repo_path=verification_input.repo_path,
            revision=verification_input.revision,
            diff=verification_input.diff,
            target_files=verification_input.target_files,
            check_spec=verification_input.check_spec,
            hypothesis=verification_input.hypothesis,
        )
        return fp, None
    except FingerprintError as exc:
        blocked = _make_blocked_result(
            f"Fingerprint computation failed: {exc}",
            verification_input.hypothesis.id,
            "sha256:uncomputable",
            start_time,
            list(cached_evidence) if cached_evidence else [],
        )
        return "", blocked


class VerificationEngine:
    """The central verification engine for VerifyCraft."""

    def __init__(
        self,
        runner: CheckRunner | None = None,
        provenance_authenticator: Callable[[Evidence], bool] | None = None,
    ) -> None:
        """Initialize the verification engine.

        Args:
            runner: Optional custom CheckRunner implementation. If None, the engine
                    selects an appropriate runner based on the execution policy mode.
            provenance_authenticator: Optional independent authenticator verifying evidence
                    cryptographic provenance. Caller-provided cached evidence is rejected
                    as authoritative in v0.1.0 unless independently authenticated.
        """
        self._runner = runner
        self._authenticator = provenance_authenticator

    def _resolve_runner(self, policy: ExecutionPolicy) -> CheckRunner:
        if self._runner is not None:
            if policy.mode == ExecutionMode.TRUSTED_LOCAL and isinstance(self._runner, FakeRunner):
                raise ValueError(
                    "Incompatible runner: FakeRunner cannot be used when policy mode is "
                    "'trusted_local'. Simulation cannot be used for trusted local verification."
                )
            if policy.mode == ExecutionMode.FAKE and not isinstance(self._runner, FakeRunner):
                raise ValueError(
                    "Incompatible runner: non-fake runner cannot be used "
                    "when policy mode is 'fake'."
                )
            return self._runner

        if policy.mode == ExecutionMode.FAKE:
            return FakeRunner()
        if policy.mode == ExecutionMode.TRUSTED_LOCAL:
            return TrustedPytestRunner()

        raise RuntimeError(f"No runner configured for execution mode '{policy.mode.value}'.")

    def _validate_cached_evidence_item(
        self,
        evi: Evidence,
        verification_input: VerificationInput,
        active_policy: ExecutionPolicy,
        current_fingerprint: str,
        now: datetime,
    ) -> tuple[str | None, bool]:
        if evi.check_id != verification_input.check_spec.check_id:
            return (
                f"Evidence check_id '{evi.check_id}' does not match "
                f"expected check_id '{verification_input.check_spec.check_id}'.",
                False,
            )

        if is_evidence_stale(evi, current_fingerprint):
            return (
                f"Stale evidence rejected: cached fingerprint ({evi.fingerprint}) "
                f"does not match current revision fingerprint ({current_fingerprint}).",
                True,
            )

        mode_err = _check_evidence_mode_compatibility(
            evi, active_policy, verification_input.check_spec.check_type
        )
        if mode_err is not None:
            return mode_err, False

        if not evi.evidence_id or not evi.evidence_id.strip():
            return "Invalid evidence provenance: evidence_id is missing or empty.", False

        if evi.collected_at > now + timedelta(minutes=5):
            return (
                f"Invalid evidence provenance: collected_at timestamp "
                f"({evi.collected_at}) is in the future.",
                False,
            )

        if self._authenticator is None or not self._authenticator(evi):
            return (
                "Caller-provided cached evidence rejected: independent provenance "
                "authentication is required to treat cached evidence as authoritative. "
                "Metadata consistency alone is not authentication.",
                False,
            )

        return None, False

    def _handle_cached_evidence(
        self,
        cached_evidence: Sequence[Evidence],
        verification_input: VerificationInput,
        active_policy: ExecutionPolicy,
        current_fingerprint: str,
        start_time: float,
    ) -> VerificationResult:
        evidence_list = list(cached_evidence)

        if active_policy.untrusted_code:
            return _make_blocked_result(
                "Untrusted code execution is blocked: local verification of untrusted "
                "code is prohibited. Cached evidence cannot bypass untrusted origin policy.",
                verification_input.hypothesis.id,
                current_fingerprint,
                start_time,
                evidence_list,
            )

        if verification_input.check_spec.check_type not in active_policy.allowed_check_types:
            return _make_blocked_result(
                f"Check type '{verification_input.check_spec.check_type.value}' is not "
                f"permitted by policy. Allowed types: "
                f"{[t.value for t in active_policy.allowed_check_types]}.",
                verification_input.hypothesis.id,
                current_fingerprint,
                start_time,
                evidence_list,
            )

        now = datetime.now(UTC)
        for evi in cached_evidence:
            blocked_msg, empty_findings = self._validate_cached_evidence_item(
                evi, verification_input, active_policy, current_fingerprint, now
            )
            if blocked_msg is not None:
                return _make_blocked_result(
                    blocked_msg,
                    verification_input.hypothesis.id,
                    current_fingerprint,
                    start_time,
                    evidence_list,
                    empty_findings=empty_findings,
                )

        findings: list[Finding] = [
            evaluate_hypothesis(verification_input.hypothesis, evi) for evi in cached_evidence
        ]
        elapsed = time.perf_counter() - start_time
        overall_verdict = findings[0].verdict if findings else Verdict.INCONCLUSIVE
        return VerificationResult(
            verdict=overall_verdict,
            findings=findings,
            evidence=evidence_list,
            fingerprint=current_fingerprint,
            resource_usage=ResourceUsage(wall_time_seconds=round(elapsed, 4)),
        )

    def _execute_and_verify(
        self,
        verification_input: VerificationInput,
        active_policy: ExecutionPolicy,
        current_fingerprint: str,
        start_time: float,
    ) -> VerificationResult:
        blocked_reason = validate_execution_authorization(
            active_policy,
            verification_input.check_spec,
            repo_path=verification_input.repo_path,
        )
        if blocked_reason is not None:
            return _make_blocked_result(
                blocked_reason,
                verification_input.hypothesis.id,
                current_fingerprint,
                start_time,
                [],
            )

        try:
            runner = self._resolve_runner(active_policy)
        except Exception as exc:
            return _make_blocked_result(
                f"Failed to resolve execution runner: {exc}",
                verification_input.hypothesis.id,
                current_fingerprint,
                start_time,
                [],
            )

        evidence = runner.run(
            spec=verification_input.check_spec,
            repo_path=verification_input.repo_path,
            policy=active_policy,
            fingerprint=current_fingerprint,
        )

        if (
            active_policy.mode == ExecutionMode.TRUSTED_LOCAL
            and evidence.evidence_type == "fake_execution"
        ):
            return _make_blocked_result(
                "Safety violation: fake evidence cannot be accepted under 'trusted_local' policy.",
                verification_input.hypothesis.id,
                current_fingerprint,
                start_time,
                [evidence],
            )

        finding = evaluate_hypothesis(verification_input.hypothesis, evidence)
        elapsed = time.perf_counter() - start_time
        inconclusive_reason = finding.reason if finding.verdict == Verdict.INCONCLUSIVE else None

        return VerificationResult(
            verdict=finding.verdict,
            findings=[finding],
            evidence=[evidence],
            inconclusive_reason=inconclusive_reason,
            fingerprint=current_fingerprint,
            resource_usage=ResourceUsage(
                wall_time_seconds=round(elapsed, 4),
                output_bytes=evidence.resource_usage.output_bytes,
                attempts=1,
            ),
        )

    def verify(
        self,
        verification_input: VerificationInput,
        policy: ExecutionPolicy | None = None,
        cached_evidence: Sequence[Evidence] | None = None,
    ) -> VerificationResult:
        """Execute or evaluate verification for the given input and policy.

        Args:
            verification_input: Input with hypothesis, check spec, and revision.
            policy: Execution policy governing execution permissions and boundaries.
            cached_evidence: Optional previously gathered evidence to evaluate.

        Returns:
            A typed, versioned VerificationResult.
        """
        active_policy = policy if policy is not None else ExecutionPolicy()
        start_time = time.perf_counter()

        current_fingerprint, blocked_result = _compute_fingerprint_or_blocked(
            verification_input, cached_evidence, start_time
        )
        if blocked_result is not None:
            return blocked_result

        if cached_evidence:
            return self._handle_cached_evidence(
                cached_evidence,
                verification_input,
                active_policy,
                current_fingerprint,
                start_time,
            )

        return self._execute_and_verify(
            verification_input,
            active_policy,
            current_fingerprint,
            start_time,
        )
