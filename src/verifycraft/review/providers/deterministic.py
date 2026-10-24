"""Deterministic reviewer adapter for testing, hermetic CI, and reproducible benchmarks."""

from __future__ import annotations

from collections.abc import Callable

from verifycraft.review.providers.base import (
    ProviderResponse,
    RawCandidateFinding,
    RawRequirementAssessment,
    ReviewerModelAdapter,
)


class FakeReviewerAdapter(ReviewerModelAdapter):
    """Returns a pre-configured ProviderResponse or delegates to a custom handler."""

    def __init__(
        self,
        response: ProviderResponse | None = None,
        handler: Callable[[str], ProviderResponse] | None = None,
    ):
        self.response = response or ProviderResponse(
            summary="Default fake review summary.",
            candidate_findings=[],
            requirement_assessments=[],
        )
        self.handler = handler

    def review(
        self,
        prompt: str,
        system_instruction: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> ProviderResponse:
        if self.handler:
            return self.handler(prompt)
        return self.response


class DeterministicReviewerAdapter(ReviewerModelAdapter):
    """Deterministic, rule-aware adapter for testing acceptance scenarios without live LLM calls."""

    def __init__(self, mode: str = "auto"):
        self.mode = mode

    def review(
        self,
        prompt: str,
        system_instruction: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> ProviderResponse:
        """Inspect prompt content and requirements to deterministically evaluate scenarios."""
        # 1. Security boundary check (Scenario G):
        if "ignore previous instructions" in prompt.lower() or "disclose secrets" in prompt.lower():
            return ProviderResponse(
                summary=(
                    "Review completed. Untrusted prompt injection attempts were "
                    "identified in source/context and safely ignored."
                ),
                candidate_findings=[
                    RawCandidateFinding(
                        file_path="README.md" if "README.md" in prompt else "tests/test_auth.py",
                        line=None,
                        label="nitpick",
                        decorations=["non-blocking"],
                        subject="Untrusted instruction block detected in repository data",
                        description=(
                            "Content in the repository attempts to alter reviewer instructions. "
                            "VerifyCraft treats all repository content as untrusted data."
                        ),
                        severity="low",
                        blocking=False,
                    )
                ],
                requirement_assessments=[],
                tokens_used=120,
            )

        # 2. Ambiguous requirement check (Scenario E):
        if "reasonably fast" in prompt.lower() or "ambiguous" in prompt.lower():
            return ProviderResponse(
                summary=(
                    "Review completed with clarification questions regarding "
                    "vague requirement statements."
                ),
                candidate_findings=[
                    RawCandidateFinding(
                        file_path="src/auth.py" if "src/auth.py" in prompt else "auth.py",
                        line=None,
                        label="question",
                        decorations=["non-blocking"],
                        subject="Ambiguous token expiration timeframe",
                        description=(
                            "Requirement specifies tokens should expire 'reasonably fast' "
                            "without an exact TTL in minutes/seconds."
                        ),
                        severity="info",
                        blocking=False,
                        requirement_ids=["REQ-01"] if "REQ-01" in prompt else [],
                        suggested_action="Define explicit numeric TTL in specification.",
                    )
                ],
                requirement_assessments=[
                    RawRequirementAssessment(
                        requirement_id="REQ-01" if "REQ-01" in prompt else "REQ-AUTH-01",
                        status="unclear",
                        explanation=(
                            "Requirement statement is vague ('reasonably fast') and cannot "
                            "be verified without an explicit threshold."
                        ),
                        related_locations=["src/auth.py"],
                    )
                ],
                tokens_used=150,
            )

        # 3. Invalid location simulation (Scenario F):
        if self.mode == "invalid_location" or "simulate_invalid_location" in prompt:
            return ProviderResponse(
                summary="Review proposing findings with simulated invalid line coordinates.",
                candidate_findings=[
                    RawCandidateFinding(
                        file_path="src/auth.py" if "src/auth.py" in prompt else "auth.py",
                        line=9999,  # Nonexistent line
                        label="issue",
                        decorations=["blocking"],
                        subject="Potential issue with out-of-bounds line coordinate",
                        description="Tests that invalid line anchors are safely downgraded.",
                        severity="medium",
                        blocking=True,
                    ),
                    RawCandidateFinding(
                        file_path="nonexistent_module.py",
                        line=10,
                        label="suggestion",
                        decorations=["non-blocking"],
                        subject="Finding in non-existent file",
                        description="This finding references a file not present in the change.",
                        severity="low",
                        blocking=False,
                    ),
                ],
                requirement_assessments=[],
                tokens_used=140,
            )

        # 4. Auth token expiration checks (Scenario A vs Scenario B):
        has_expiration_req = (
            "expire after 15 minutes" in prompt.lower()
            or "expiration" in prompt.lower()
            or "req-auth-01" in prompt.lower()
        )
        if has_expiration_req:
            has_compensating_control = (
                "check_token_expiration" in prompt
                or "token.is_expired()" in prompt
                or "validate_expiration" in prompt
                or "expires_at < current_time" in prompt
                or "is_expired" in prompt
            )

            if has_compensating_control:
                # Scenario B: Compensating control present in helper! Do NOT emit false issue.
                return ProviderResponse(
                    summary=(
                        "Authentication changes reviewed. Token expiration requirement "
                        "REQ-AUTH-01 is satisfied via compensating logic in the helper module."
                    ),
                    candidate_findings=[],
                    requirement_assessments=[
                        RawRequirementAssessment(
                            requirement_id="REQ-AUTH-01",
                            status="appears_satisfied",
                            explanation=(
                                "Token expiration is enforced by the helper module "
                                "before session creation."
                            ),
                            related_locations=[
                                "src/auth_helper.py"
                                if "src/auth_helper.py" in prompt
                                else "auth_helper.py"
                            ],
                        )
                    ],
                    tokens_used=210,
                )
            else:
                # Scenario A: Missing expiration! Emit issue (blocking) on changed line.
                return ProviderResponse(
                    summary=(
                        "Review identified critical security issue: missing token expiration "
                        "check required by specification."
                    ),
                    candidate_findings=[
                        RawCandidateFinding(
                            file_path="src/auth.py" if "src/auth.py" in prompt else "auth.py",
                            line=2,
                            label="issue",
                            decorations=["blocking"],
                            subject="Token expiration check is omitted in authentication handler",
                            description=(
                                "`authenticate_user()` grants session tokens without "
                                "verifying token expiration timestamp."
                            ),
                            severity="critical",
                            blocking=True,
                            requirement_ids=["REQ-AUTH-01"],
                            suggested_action=(
                                "Verify `token.expires_at > current_time()` before issuing "
                                "session; raise `TokenExpiredError` if expired."
                            ),
                        )
                    ],
                    requirement_assessments=[
                        RawRequirementAssessment(
                            requirement_id="REQ-AUTH-01",
                            status="not_satisfied",
                            explanation=(
                                "Authentication handler creates sessions without verifying "
                                "15-minute expiration timestamp."
                            ),
                            related_locations=["src/auth.py:2"],
                        )
                    ],
                    tokens_used=230,
                )

        # 5. Logic regression without requirements (Scenario C):
        if (
            "zero_division" in prompt.lower()
            or "divide by zero" in prompt.lower()
            or "defect" in prompt.lower()
        ):
            return ProviderResponse(
                summary=(
                    "Review identified an unhandled edge case leading to runtime division by zero."
                ),
                candidate_findings=[
                    RawCandidateFinding(
                        file_path="src/calculator.py"
                        if "src/calculator.py" in prompt
                        else "calculator.py",
                        line=10,
                        label="issue",
                        decorations=["blocking"],
                        subject="Unhandled ZeroDivisionError when denominator is zero",
                        description=(
                            "`calculate_ratio()` divides numerator by denominator "
                            "without checking if denominator is zero."
                        ),
                        severity="high",
                        blocking=True,
                        suggested_action=(
                            "Add `if denominator == 0: raise ValueError('Cannot divide by zero')`."
                        ),
                    )
                ],
                requirement_assessments=[],
                tokens_used=180,
            )

        # 6. Clean change (Scenario D):
        return ProviderResponse(
            summary=(
                "Code changes reviewed. Implementation is clean, adheres to conventions, "
                "and introduces no identifiable defects."
            ),
            candidate_findings=[],
            requirement_assessments=[],
            tokens_used=110,
        )
