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
                    as authoritative in v0.1.0a1 unless independently authenticated.
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

        # 1. Compute deterministic change fingerprint (fail-closed if impossible)
        try:
            current_fingerprint = compute_fingerprint(
                repo_path=verification_input.repo_path,
                revision=verification_input.revision,
                diff=verification_input.diff,
                target_files=verification_input.target_files,
                check_spec=verification_input.check_spec,
                hypothesis=verification_input.hypothesis,
            )
        except FingerprintError as exc:
            elapsed = time.perf_counter() - start_time
            blocked_msg = f"Fingerprint computation failed: {exc}"
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                findings=[
                    Finding(
                        hypothesis_id=verification_input.hypothesis.id,
                        verdict=Verdict.BLOCKED,
                        reason=blocked_msg,
                    )
                ],
                evidence=list(cached_evidence) if cached_evidence else [],
                blocked_reason=blocked_msg,
                fingerprint="sha256:uncomputable",
                resource_usage=ResourceUsage(wall_time_seconds=round(elapsed, 4)),
            )

        # 2. Validate cached evidence against execution policies, provenance, and check spec
        if cached_evidence:
            # Policy enforcement: cached evidence cannot bypass untrusted code or check type list
            if active_policy.untrusted_code:
                elapsed = time.perf_counter() - start_time
                blocked_msg = (
                    "Untrusted code execution is blocked: local verification of untrusted "
                    "code is prohibited. Cached evidence cannot bypass untrusted origin policy."
                )
                return VerificationResult(
                    verdict=Verdict.BLOCKED,
                    findings=[
                        Finding(
                            hypothesis_id=verification_input.hypothesis.id,
                            verdict=Verdict.BLOCKED,
                            reason=blocked_msg,
                        )
                    ],
                    evidence=list(cached_evidence),
                    blocked_reason=blocked_msg,
                    fingerprint=current_fingerprint,
                    resource_usage=ResourceUsage(wall_time_seconds=round(elapsed, 4)),
                )

            if verification_input.check_spec.check_type not in active_policy.allowed_check_types:
                elapsed = time.perf_counter() - start_time
                blocked_msg = (
                    f"Check type '{verification_input.check_spec.check_type.value}' is not "
                    f"permitted by policy. Allowed types: "
                    f"{[t.value for t in active_policy.allowed_check_types]}."
                )
                return VerificationResult(
                    verdict=Verdict.BLOCKED,
                    findings=[
                        Finding(
                            hypothesis_id=verification_input.hypothesis.id,
                            verdict=Verdict.BLOCKED,
                            reason=blocked_msg,
                        )
                    ],
                    evidence=list(cached_evidence),
                    blocked_reason=blocked_msg,
                    fingerprint=current_fingerprint,
                    resource_usage=ResourceUsage(wall_time_seconds=round(elapsed, 4)),
                )

            now = datetime.now(UTC)
            for evi in cached_evidence:
                # A. Reject mismatched check_id
                if evi.check_id != verification_input.check_spec.check_id:
                    elapsed = time.perf_counter() - start_time
                    blocked_msg = (
                        f"Evidence check_id '{evi.check_id}' does not match "
                        f"expected check_id '{verification_input.check_spec.check_id}'."
                    )
                    return VerificationResult(
                        verdict=Verdict.BLOCKED,
                        findings=[
                            Finding(
                                hypothesis_id=verification_input.hypothesis.id,
                                verdict=Verdict.BLOCKED,
                                reason=blocked_msg,
                            )
                        ],
                        evidence=list(cached_evidence),
                        blocked_reason=blocked_msg,
                        fingerprint=current_fingerprint,
                        resource_usage=ResourceUsage(wall_time_seconds=round(elapsed, 4)),
                    )

                # B. Reject stale fingerprint
                if is_evidence_stale(evi, current_fingerprint):
                    elapsed = time.perf_counter() - start_time
                    blocked_msg = (
                        f"Stale evidence rejected: cached fingerprint ({evi.fingerprint}) "
                        f"does not match current revision fingerprint ({current_fingerprint})."
                    )
                    return VerificationResult(
                        verdict=Verdict.BLOCKED,
                        findings=[],
                        evidence=list(cached_evidence),
                        blocked_reason=blocked_msg,
                        fingerprint=current_fingerprint,
                        resource_usage=ResourceUsage(wall_time_seconds=round(elapsed, 4)),
                    )

                # C. Reject incompatible execution mode / evidence type
                if active_policy.mode == ExecutionMode.TRUSTED_LOCAL:
                    if evi.evidence_type == "fake_execution":
                        elapsed = time.perf_counter() - start_time
                        blocked_msg = (
                            "Safety violation: cached fake evidence cannot be accepted under "
                            "'trusted_local' policy."
                        )
                        return VerificationResult(
                            verdict=Verdict.BLOCKED,
                            findings=[
                                Finding(
                                    hypothesis_id=verification_input.hypothesis.id,
                                    verdict=Verdict.BLOCKED,
                                    reason=blocked_msg,
                                )
                            ],
                            evidence=list(cached_evidence),
                            blocked_reason=blocked_msg,
                            fingerprint=current_fingerprint,
                            resource_usage=ResourceUsage(wall_time_seconds=round(elapsed, 4)),
                        )
                    if (
                        verification_input.check_spec.check_type == CheckType.PYTEST
                        and evi.evidence_type != "pytest_execution"
                    ):
                        elapsed = time.perf_counter() - start_time
                        blocked_msg = (
                            f"Incompatible evidence: evidence type '{evi.evidence_type}' is "
                            "incompatible with pytest check specification."
                        )
                        return VerificationResult(
                            verdict=Verdict.BLOCKED,
                            findings=[
                                Finding(
                                    hypothesis_id=verification_input.hypothesis.id,
                                    verdict=Verdict.BLOCKED,
                                    reason=blocked_msg,
                                )
                            ],
                            evidence=list(cached_evidence),
                            blocked_reason=blocked_msg,
                            fingerprint=current_fingerprint,
                            resource_usage=ResourceUsage(wall_time_seconds=round(elapsed, 4)),
                        )

                if (
                    active_policy.mode == ExecutionMode.FAKE
                    and evi.evidence_type != "fake_execution"
                ):
                    elapsed = time.perf_counter() - start_time
                    blocked_msg = (
                        f"Incompatible evidence: evidence type '{evi.evidence_type}' is "
                        "incompatible with fake policy mode."
                    )
                    return VerificationResult(
                        verdict=Verdict.BLOCKED,
                        findings=[
                            Finding(
                                hypothesis_id=verification_input.hypothesis.id,
                                verdict=Verdict.BLOCKED,
                                reason=blocked_msg,
                            )
                        ],
                        evidence=list(cached_evidence),
                        blocked_reason=blocked_msg,
                        fingerprint=current_fingerprint,
                        resource_usage=ResourceUsage(wall_time_seconds=round(elapsed, 4)),
                    )

                # D. Reject invalid provenance
                if not evi.evidence_id or not evi.evidence_id.strip():
                    elapsed = time.perf_counter() - start_time
                    blocked_msg = "Invalid evidence provenance: evidence_id is missing or empty."
                    return VerificationResult(
                        verdict=Verdict.BLOCKED,
                        findings=[
                            Finding(
                                hypothesis_id=verification_input.hypothesis.id,
                                verdict=Verdict.BLOCKED,
                                reason=blocked_msg,
                            )
                        ],
                        evidence=list(cached_evidence),
                        blocked_reason=blocked_msg,
                        fingerprint=current_fingerprint,
                        resource_usage=ResourceUsage(wall_time_seconds=round(elapsed, 4)),
                    )
                if evi.collected_at > now + timedelta(minutes=5):
                    elapsed = time.perf_counter() - start_time
                    blocked_msg = (
                        f"Invalid evidence provenance: collected_at timestamp "
                        f"({evi.collected_at}) is in the future."
                    )
                    return VerificationResult(
                        verdict=Verdict.BLOCKED,
                        findings=[
                            Finding(
                                hypothesis_id=verification_input.hypothesis.id,
                                verdict=Verdict.BLOCKED,
                                reason=blocked_msg,
                            )
                        ],
                        evidence=list(cached_evidence),
                        blocked_reason=blocked_msg,
                        fingerprint=current_fingerprint,
                        resource_usage=ResourceUsage(wall_time_seconds=round(elapsed, 4)),
                    )

                # E. Independent provenance authentication enforcement
                # Caller-provided cached execution evidence is disabled as authoritative
                # unless provenance is independently authenticated. Metadata consistency is
                # not authentication.
                if self._authenticator is None or not self._authenticator(evi):
                    elapsed = time.perf_counter() - start_time
                    blocked_msg = (
                        "Caller-provided cached evidence rejected: independent provenance "
                        "authentication is required to treat cached evidence as authoritative. "
                        "Metadata consistency alone is not authentication."
                    )
                    return VerificationResult(
                        verdict=Verdict.BLOCKED,
                        findings=[
                            Finding(
                                hypothesis_id=verification_input.hypothesis.id,
                                verdict=Verdict.BLOCKED,
                                reason=blocked_msg,
                            )
                        ],
                        evidence=list(cached_evidence),
                        blocked_reason=blocked_msg,
                        fingerprint=current_fingerprint,
                        resource_usage=ResourceUsage(wall_time_seconds=round(elapsed, 4)),
                    )

            # Evaluate fresh cached evidence directly (only reached if independently authenticated)
            findings: list[Finding] = [
                evaluate_hypothesis(verification_input.hypothesis, evi) for evi in cached_evidence
            ]
            elapsed = time.perf_counter() - start_time
            overall_verdict = findings[0].verdict if findings else Verdict.INCONCLUSIVE
            return VerificationResult(
                verdict=overall_verdict,
                findings=findings,
                evidence=list(cached_evidence),
                fingerprint=current_fingerprint,
                resource_usage=ResourceUsage(wall_time_seconds=round(elapsed, 4)),
            )

        # 3. Enforce policy and execution authorization
        blocked_reason = validate_execution_authorization(
            active_policy,
            verification_input.check_spec,
            repo_path=verification_input.repo_path,
        )
        if blocked_reason is not None:
            elapsed = time.perf_counter() - start_time
            finding = Finding(
                hypothesis_id=verification_input.hypothesis.id,
                verdict=Verdict.BLOCKED,
                reason=blocked_reason,
                evidence_ids=[],
            )
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                findings=[finding],
                evidence=[],
                blocked_reason=blocked_reason,
                fingerprint=current_fingerprint,
                resource_usage=ResourceUsage(wall_time_seconds=round(elapsed, 4)),
            )

        # 4. Resolve runner and execute check
        try:
            runner = self._resolve_runner(active_policy)
        except Exception as exc:
            elapsed = time.perf_counter() - start_time
            err_msg = f"Failed to resolve execution runner: {exc}"
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                findings=[
                    Finding(
                        hypothesis_id=verification_input.hypothesis.id,
                        verdict=Verdict.BLOCKED,
                        reason=err_msg,
                    )
                ],
                evidence=[],
                blocked_reason=err_msg,
                fingerprint=current_fingerprint,
                resource_usage=ResourceUsage(wall_time_seconds=round(elapsed, 4)),
            )

        # 5. Capture structured evidence
        evidence = runner.run(
            spec=verification_input.check_spec,
            repo_path=verification_input.repo_path,
            policy=active_policy,
            fingerprint=current_fingerprint,
        )

        # Reject fake evidence under trusted_local
        if (
            active_policy.mode == ExecutionMode.TRUSTED_LOCAL
            and evidence.evidence_type == "fake_execution"
        ):
            elapsed = time.perf_counter() - start_time
            blocked_msg = (
                "Safety violation: fake evidence cannot be accepted under 'trusted_local' policy."
            )
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                findings=[
                    Finding(
                        hypothesis_id=verification_input.hypothesis.id,
                        verdict=Verdict.BLOCKED,
                        reason=blocked_msg,
                    )
                ],
                evidence=[evidence],
                blocked_reason=blocked_msg,
                fingerprint=current_fingerprint,
                resource_usage=ResourceUsage(wall_time_seconds=round(elapsed, 4)),
            )

        # 6. Evaluate evidence against explicit expected observations
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
