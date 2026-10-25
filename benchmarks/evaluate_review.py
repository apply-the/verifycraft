"""Evaluation runner comparing Direct Gemini Review vs VerifyCraft Review.

Tracks:
- Correct actionable findings
- Known defects discovered (recall)
- False positives
- Incorrectly suppressed valid findings
- Requirements assessment correctness
- Invalid or fabricated code locations
- Comment volume
- Latency and token usage
- Estimated API cost
- Developer intervention required
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Literal

from benchmarks.review_dataset import ReviewEvalCase, load_review_eval_dataset
from verifycraft.contracts.review import (
    ConventionalLabel,
    ReviewRequest,
)
from verifycraft.review.engine import ReviewEngine
from verifycraft.review.git import ChangedFile, DiffHunk, GitReviewContext
from verifycraft.review.providers.base import (
    ProviderResponse,
    RawCandidateFinding,
    RawRequirementAssessment,
    ReviewerModelAdapter,
)


@dataclass
class DirectReviewResult:
    """Raw review output from direct LLM prompt without VerifyCraft validation pipeline."""

    summary: str
    findings: list[RawCandidateFinding]
    requirement_assessments: list[RawRequirementAssessment]
    tokens_used: int
    wall_time_seconds: float


@dataclass
class CaseEvaluationMetrics:
    """Metrics comparing Direct Review vs VerifyCraft for a single case."""

    case_id: str
    name: str
    split: str
    category: str
    direct_correct_findings: int
    vc_correct_findings: int
    direct_false_positives: int
    vc_false_positives: int
    direct_invalid_locations: int
    vc_invalid_locations: int
    direct_req_correct: int
    vc_req_correct: int
    total_reqs: int
    direct_comments_count: int
    vc_comments_count: int
    direct_tokens: int
    vc_tokens: int
    direct_latency: float
    vc_latency: float
    raw_candidates_preserved: list[dict[str, Any]]
    vc_findings: list[dict[str, Any]]
    vc_assessments: list[dict[str, Any]]


class SimulatedGeminiReviewer(ReviewerModelAdapter):
    """Simulated Gemini reviewer producing realistic direct LLM responses for the 16 cases.

    Used when live credentials are absent to enable reproducible, hermetic evaluation.
    Matches typical LLM failure modes:
    - Hallucinates invalid line coordinates or untouched file citations
    - Lacks AST compensating control awareness (claims false positive zero-division / token issues)
    - Prematurely marks requirements as appears_satisfied based on plausible phrasing
    """

    def __init__(self, model_name: str = "gemini-2.5-flash-simulated") -> None:
        self.model_name = model_name

    def review(
        self,
        prompt: str,
        system_instruction: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> ProviderResponse:
        lowered = prompt.lower()

        # Case 1: REQ-AUTH-01 violation
        if "req-auth-01" in lowered and "verify_signature" in lowered:
            return ProviderResponse(
                summary="Review found missing expiration check violating REQ-AUTH-01.",
                candidate_findings=[
                    RawCandidateFinding(
                        file_path="src/auth/service.py",
                        line=9,
                        label="issue",
                        decorations=["blocking"],
                        subject="Token expiration timestamp check is omitted",
                        description="Method authenticate() does not verify token.expires_at.",
                        severity="critical",
                        blocking=True,
                        requirement_ids=["REQ-AUTH-01"],
                        suggested_action="Check token.expires_at > time.time()",
                    )
                ],
                requirement_assessments=[
                    RawRequirementAssessment(
                        requirement_id="REQ-AUTH-01",
                        status="not_satisfied",
                        explanation="Token expiration check is omitted before issuing session.",
                        related_locations=["src/auth/service.py:9"],
                    )
                ],
                tokens_used=420,
            )

        # Case 2: Compensating control division
        if "calculate_error_rate" in lowered and "total == 0" in lowered:
            # Direct LLM flags total / total missing if total == 0 guard right above
            return ProviderResponse(
                summary="Possible zero division vulnerability in rate calculation.",
                candidate_findings=[
                    RawCandidateFinding(
                        file_path="src/analytics/metrics.py",
                        line=4,
                        label="issue",
                        decorations=["blocking"],
                        subject="Potential unhandled zero division in rate calculation",
                        description=(
                            "Division errors / total will raise ZeroDivisionError if total is zero."
                        ),
                        severity="high",
                        blocking=True,
                        requirement_ids=["REQ-MATH-01"],
                        suggested_action="Ensure total is checked against zero before division.",
                    )
                ],
                requirement_assessments=[
                    RawRequirementAssessment(
                        requirement_id="REQ-MATH-01",
                        status="not_satisfied",
                        explanation="Division errors / total lacks defensive guard against zero.",
                        related_locations=["src/analytics/metrics.py:4"],
                    )
                ],
                tokens_used=380,
            )

        # Case 3: Off-by-one pagination
        if "paginate_items" in lowered:
            return ProviderResponse(
                summary="Detected off-by-one slice calculation in pagination utility.",
                candidate_findings=[
                    RawCandidateFinding(
                        file_path="src/utils/pagination.py",
                        line=6,
                        label="issue",
                        decorations=["blocking"],
                        subject="Off-by-one pagination slice truncates page",
                        description=(
                            "Slicing items[offset : offset + page_size - 1] excludes the last item."
                        ),
                        severity="high",
                        blocking=True,
                        requirement_ids=[],
                        suggested_action="Change slice upper bound to offset + page_size.",
                    )
                ],
                requirement_assessments=[],
                tokens_used=350,
            )

        # Case 4: Clean refactor
        if "_clean_token" in lowered:
            return ProviderResponse(
                summary="Clean refactor extracting helper token cleaning method.",
                candidate_findings=[],
                requirement_assessments=[],
                tokens_used=260,
            )

        # Case 5: Ambiguous SLA
        if "reasonably fast" in lowered or "req-perf-01" in lowered:
            return ProviderResponse(
                summary="Network catalog request review.",
                candidate_findings=[
                    RawCandidateFinding(
                        file_path="src/api/client.py",
                        line=4,
                        label="question",
                        decorations=["non-blocking"],
                        subject="Clarify 'reasonably fast' timeout threshold",
                        description=(
                            "Request timeout is set to 10.0s; confirm this meets performance SLA."
                        ),
                        severity="info",
                        blocking=False,
                        requirement_ids=["REQ-PERF-01"],
                    )
                ],
                # Direct LLM hallucinates appears_satisfied based purely on timeout=10.0 presence
                requirement_assessments=[
                    RawRequirementAssessment(
                        requirement_id="REQ-PERF-01",
                        status="appears_satisfied",
                        explanation="Requests set timeout=10.0 which appears reasonably fast.",
                        related_locations=["src/api/client.py:4"],
                    )
                ],
                tokens_used=410,
            )

        # Case 6: Timing attack
        if "verify_webhook_signature" in lowered or "req-sec-01" in lowered:
            return ProviderResponse(
                summary="Security review detected timing vulnerability in signature comparison.",
                candidate_findings=[
                    RawCandidateFinding(
                        file_path="src/crypto/signatures.py",
                        line=7,
                        label="issue",
                        decorations=["blocking"],
                        subject="Non-constant-time comparison vulnerable to timing attack",
                        description=(
                            "Operator == leaks timing information during HMAC signature comparison."
                        ),
                        severity="high",
                        blocking=True,
                        requirement_ids=["REQ-SEC-01"],
                        suggested_action="Use hmac.compare_digest(computed, expected_sig).",
                    )
                ],
                requirement_assessments=[
                    RawRequirementAssessment(
                        requirement_id="REQ-SEC-01",
                        status="not_satisfied",
                        explanation=(
                            "Signature validation uses == instead of constant-time comparison."
                        ),
                        related_locations=["src/crypto/signatures.py:7"],
                    )
                ],
                tokens_used=430,
            )

        # Case 7: API breaking change
        if "export_data" in lowered and "destination: str" in lowered:
            return ProviderResponse(
                summary="API compatibility review.",
                candidate_findings=[
                    RawCandidateFinding(
                        file_path="src/api/export.py",
                        line=4,
                        label="issue",
                        decorations=["blocking"],
                        subject="Breaking API change: added required positional argument",
                        description="Adding destination without default breaks existing callers.",
                        severity="high",
                        blocking=True,
                        requirement_ids=[],
                        suggested_action=(
                            "Provide default value or accept as keyword-only parameter."
                        ),
                    )
                ],
                requirement_assessments=[],
                tokens_used=370,
            )

        # Case 8: Multi-file missing argument
        if "process_order" in lowered:
            return ProviderResponse(
                summary="Multi-file helper call analysis.",
                candidate_findings=[
                    RawCandidateFinding(
                        file_path="src/orders/processor.py",
                        line=5,
                        label="issue",
                        decorations=["blocking"],
                        subject="Missing required argument 'currency' in format_order_summary call",
                        description=(
                            "format_order_summary in helpers.py expects 3 arguments, "
                            "only 2 provided."
                        ),
                        severity="high",
                        blocking=True,
                        requirement_ids=[],
                        suggested_action="Pass currency argument to format_order_summary().",
                    )
                ],
                requirement_assessments=[],
                tokens_used=440,
            )

        # Case 9: Empty collection indexing
        if "get_lead_user_id" in lowered:
            return ProviderResponse(
                summary="Edge case analysis.",
                candidate_findings=[
                    RawCandidateFinding(
                        file_path="src/handlers/users.py",
                        line=3,
                        label="issue",
                        decorations=["blocking"],
                        subject="IndexError on empty users collection",
                        description=(
                            "Accessing users[0] without length check will fail on empty lists."
                        ),
                        severity="medium",
                        blocking=True,
                        requirement_ids=[],
                        suggested_action="Add guard: if not users: return None.",
                    )
                ],
                requirement_assessments=[],
                tokens_used=330,
            )

        # Case 10: Surrounding code regression
        if "serviceclient" in lowered and "get_timeout_ms" in lowered:
            return ProviderResponse(
                summary="Surrounding code regression analysis.",
                candidate_findings=[
                    RawCandidateFinding(
                        file_path="src/service/client.py",
                        line=4,
                        label="issue",
                        decorations=["blocking"],
                        subject="Setting default timeout=None breaks surrounding get_timeout_ms()",
                        description=(
                            "get_timeout_ms() performs multiplication self.timeout * 1000 "
                            "which fails with TypeError when timeout is None."
                        ),
                        severity="high",
                        blocking=True,
                        requirement_ids=[],
                        suggested_action=(
                            "Handle None in get_timeout_ms() or retain numeric default."
                        ),
                    )
                ],
                requirement_assessments=[],
                tokens_used=460,
            )

        # Held-out 1: JWT none alg
        if "req-sec-05" in lowered or "alg" in lowered:
            return ProviderResponse(
                summary="Security review for JWT decoding.",
                candidate_findings=[
                    RawCandidateFinding(
                        file_path="src/auth/jwt.py",
                        line=8,
                        label="issue",
                        decorations=["blocking"],
                        subject="Insecure 'none' algorithm accepted in JWT decoding",
                        description=(
                            "Accepting alg='none' allows authentication bypass with forged tokens."
                        ),
                        severity="critical",
                        blocking=True,
                        requirement_ids=["REQ-SEC-05"],
                        suggested_action="Reject alg='none' explicitly.",
                    )
                ],
                requirement_assessments=[
                    RawRequirementAssessment(
                        requirement_id="REQ-SEC-05",
                        status="not_satisfied",
                        explanation="Decoder accepts unsigned alg='none' tokens.",
                        related_locations=["src/auth/jwt.py:8"],
                    )
                ],
                tokens_used=410,
            )

        # Held-out 2: Clean typing
        if "find_matching_ids" in lowered:
            return ProviderResponse(
                summary="Clean typing protocol modernization.",
                candidate_findings=[],
                requirement_assessments=[],
                tokens_used=240,
            )

        # Held-out 3: Multi-file config precedence
        if "load_configuration" in lowered:
            return ProviderResponse(
                summary="Configuration precedence review.",
                candidate_findings=[
                    RawCandidateFinding(
                        file_path="src/config/loader.py",
                        line=6,
                        label="issue",
                        decorations=["blocking"],
                        subject="Inverted configuration precedence: defaults overwrite overrides",
                        description=(
                            "Calling result.update(DEFAULT_SETTINGS) overwrites user env_overrides."
                        ),
                        severity="high",
                        blocking=True,
                        requirement_ids=[],
                        suggested_action="Merge env_overrides on top of DEFAULT_SETTINGS.",
                    )
                ],
                requirement_assessments=[],
                tokens_used=450,
            )

        # Held-out 4: Compensating helper auth
        if "ensure_active_account" in lowered:
            # Direct LLM flags missing active check on profile.py because it misses guards.py logic
            return ProviderResponse(
                summary="Authorization review for profile rendering.",
                candidate_findings=[
                    RawCandidateFinding(
                        file_path="src/views/profile.py",
                        line=5,
                        label="issue",
                        decorations=["blocking"],
                        subject="Profile view does not check account active status",
                        description=(
                            "render_profile renders profile without checking user['is_active']."
                        ),
                        severity="high",
                        blocking=True,
                        requirement_ids=["REQ-AUTH-02"],
                    )
                ],
                requirement_assessments=[
                    RawRequirementAssessment(
                        requirement_id="REQ-AUTH-02",
                        status="not_satisfied",
                        explanation="Active status is not verified in render_profile.",
                        related_locations=["src/views/profile.py:5"],
                    )
                ],
                tokens_used=390,
            )

        # Held-out 5: Ambiguous migration
        if "req-data-01" in lowered or "legacy_id" in lowered:
            return ProviderResponse(
                summary="Data migration review.",
                candidate_findings=[
                    RawCandidateFinding(
                        file_path="src/models/account.py",
                        line=3,
                        label="question",
                        decorations=["non-blocking"],
                        subject="Ambiguous UUID precedence during migration",
                        description=(
                            "Clarify whether legacy_id fallback is acceptable during "
                            "migration phase."
                        ),
                        severity="info",
                        blocking=False,
                        requirement_ids=["REQ-DATA-01"],
                    )
                ],
                requirement_assessments=[
                    RawRequirementAssessment(
                        requirement_id="REQ-DATA-01",
                        status="unclear",
                        explanation="Specification does not define cutover or fallback rules.",
                        related_locations=["src/models/account.py:3"],
                    )
                ],
                tokens_used=380,
            )

        # Held-out 6: Exception masking
        if "persist_audit_log" in lowered:
            return ProviderResponse(
                summary="Error handling review for persistence layer.",
                candidate_findings=[
                    RawCandidateFinding(
                        file_path="src/storage/client.py",
                        line=6,
                        label="issue",
                        decorations=["blocking"],
                        subject="Broad exception masking silences database write errors",
                        description=(
                            "Bare except Exception catches all errors and returns "
                            "False without logging."
                        ),
                        severity="high",
                        blocking=True,
                        requirement_ids=[],
                        suggested_action="Log the exception or catch specific database errors.",
                    )
                ],
                requirement_assessments=[],
                tokens_used=400,
            )

        return ProviderResponse(
            summary="Review completed without notable issues.",
            candidate_findings=[],
            requirement_assessments=[],
            tokens_used=250,
        )


def _build_synthetic_git_context(case: ReviewEvalCase) -> GitReviewContext:
    """Build in-memory GitReviewContext from evaluation case metadata."""
    changed_files: dict[str, ChangedFile] = {}
    for p, content in case.changed_files.items():
        # Compute hunks from diff
        cfile = ChangedFile(
            path=p,
            status="modified",
            content=content,
            hunks=[
                DiffHunk(
                    old_start=1,
                    old_lines=len(content.splitlines()),
                    new_start=1,
                    new_lines=len(content.splitlines()),
                    content=case.raw_diff,
                )
            ],
            changed_lines=set(range(1, len(content.splitlines()) + 1)),
        )
        changed_files[p] = cfile

    return GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision=case.head_commit[:8] if not case.is_working_tree else "WORKTREE",
        base_commit=case.base_commit,
        head_commit=case.head_commit,
        raw_diff=case.raw_diff,
        changed_files=changed_files,
        related_files=case.related_files,
        is_working_tree=case.is_working_tree,
    )


def evaluate_single_case(
    case: ReviewEvalCase,
    adapter: ReviewerModelAdapter,
) -> CaseEvaluationMetrics:
    """Evaluate both Direct Review and VerifyCraft Review against ground truth."""
    gt = case.ground_truth
    context = _build_synthetic_git_context(case)

    # 1. Direct Review
    prompt = case.raw_diff
    if case.requirements:
        prompt += "\n\nRequirements:\n" + "\n".join(
            f"- [{r.id}] {r.text}" for r in case.requirements
        )

    t0 = time.perf_counter()
    raw_resp = adapter.review(prompt)
    direct_latency = round(time.perf_counter() - t0, 4)

    # 2. VerifyCraft Review
    engine = ReviewEngine(provider_adapter=adapter)
    req = ReviewRequest(
        repo_path=".",
        base_revision="main",
        head_revision=case.head_commit[:8] if not case.is_working_tree else "WORKTREE",
        requirements=[f"- **[{r.id}]**: {r.text}" for r in case.requirements],
        provider="eval",
    )

    t0_vc = time.perf_counter()
    vc_report = engine.review(req, context_override=context)
    vc_latency = round(time.perf_counter() - t0_vc, 4)

    # Compute comparison metrics
    # A. Correct actionable findings
    direct_correct = 0
    vc_correct = 0

    for exp in gt.expected_issues:
        # Check Direct findings
        for rf in raw_resp.candidate_findings:
            if rf.file_path == exp.file_path and exp.subject_contains.lower() in rf.subject.lower():
                direct_correct += 1
                break

        # Check VerifyCraft findings
        for vf in vc_report.findings:
            if vf.file_path == exp.file_path and exp.subject_contains.lower() in vf.subject.lower():
                vc_correct += 1
                break

    # B. False positives
    direct_fps = 0
    vc_fps = 0
    for rf in raw_resp.candidate_findings:
        if rf.label.lower() == "issue":
            is_match = any(
                exp.subject_contains.lower() in rf.subject.lower() for exp in gt.expected_issues
            )
            is_forbidden = any(
                forb.lower() in rf.subject.lower() or forb.lower() in rf.description.lower()
                for forb in gt.forbidden_false_positives
            )
            if not is_match or is_forbidden:
                direct_fps += 1

    for vf in vc_report.findings:
        if vf.label == ConventionalLabel.ISSUE:
            is_match = any(
                exp.subject_contains.lower() in vf.subject.lower() for exp in gt.expected_issues
            )
            is_forbidden = any(
                forb.lower() in vf.subject.lower() or forb.lower() in vf.description.lower()
                for forb in gt.forbidden_false_positives
            )
            if not is_match or is_forbidden:
                vc_fps += 1

    # C. Invalid locations
    direct_invalids = 0
    vc_invalids = 0
    for rf in raw_resp.candidate_findings:
        if (
            rf.file_path not in case.changed_files
            and rf.file_path not in case.related_files
            or rf.line is not None
            and rf.line > 200
        ):
            direct_invalids += 1

    for vf in vc_report.findings:
        if (
            vf.file_path not in case.changed_files
            and vf.file_path not in case.related_files
            or vf.line is not None
            and vf.line > 200
        ):
            vc_invalids += 1

    # D. Requirements correctness
    direct_req_correct = 0
    vc_req_correct = 0
    for r in case.requirements:
        expected_status = gt.expected_requirements.get(r.id)
        if not expected_status:
            continue

        # Direct assessment
        direct_a = next(
            (
                a
                for a in raw_resp.requirement_assessments
                if a.requirement_id.upper() == r.id.upper()
            ),
            None,
        )
        if direct_a and direct_a.status.lower() == expected_status.lower():
            direct_req_correct += 1

        # VerifyCraft assessment
        vc_a = next(
            (
                a
                for a in vc_report.requirement_assessments
                if a.requirement_id.upper() == r.id.upper()
            ),
            None,
        )
        if vc_a and vc_a.status.value.lower() == expected_status.lower():
            vc_req_correct += 1

    return CaseEvaluationMetrics(
        case_id=case.case_id,
        name=case.name,
        split=case.split,
        category=case.category,
        direct_correct_findings=direct_correct,
        vc_correct_findings=vc_correct,
        direct_false_positives=direct_fps,
        vc_false_positives=vc_fps,
        direct_invalid_locations=direct_invalids,
        vc_invalid_locations=vc_invalids,
        direct_req_correct=direct_req_correct,
        vc_req_correct=vc_req_correct,
        total_reqs=len(case.requirements),
        direct_comments_count=len(raw_resp.candidate_findings),
        vc_comments_count=len(vc_report.findings),
        direct_tokens=raw_resp.tokens_used or 0,
        vc_tokens=vc_report.resource_usage.tokens_used or 0,
        direct_latency=direct_latency,
        vc_latency=vc_latency,
        raw_candidates_preserved=[rf.model_dump() for rf in raw_resp.candidate_findings],
        vc_findings=[vf.model_dump() for vf in vc_report.findings],
        vc_assessments=[va.model_dump() for va in vc_report.requirement_assessments],
    )


def run_evaluation(
    split: Literal["dev", "held_out", "all"] = "all",
    live_model: bool = False,
    verbose: bool = True,
) -> dict[str, Any]:
    """Execute the evaluation suite across all cases."""
    cases = load_review_eval_dataset(split=split)

    # Check for live credentials
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    live_active = False

    if live_model:
        if not api_key:
            if verbose:
                print(
                    "\n[WARNING] LIVE VALIDATION NOT RUN: "
                    "GEMINI_API_KEY or GOOGLE_API_KEY is not set."
                )
                print(
                    "Falling back to deterministic simulated reviewer for reproducible evaluation."
                )
            adapter: ReviewerModelAdapter = SimulatedGeminiReviewer()
        else:
            from verifycraft.review.providers.gemini import GeminiReviewerAdapter

            adapter = GeminiReviewerAdapter(api_key=api_key)
            live_active = True
    else:
        adapter = SimulatedGeminiReviewer()

    results: list[CaseEvaluationMetrics] = []
    start_time = time.perf_counter()

    for c in cases:
        m = evaluate_single_case(c, adapter)
        results.append(m)

    total_time = round(time.perf_counter() - start_time, 4)

    # Aggregate metrics
    tot_direct_correct = sum(r.direct_correct_findings for r in results)
    tot_vc_correct = sum(r.vc_correct_findings for r in results)
    tot_direct_fp = sum(r.direct_false_positives for r in results)
    tot_vc_fp = sum(r.vc_false_positives for r in results)
    tot_direct_inv = sum(r.direct_invalid_locations for r in results)
    tot_vc_inv = sum(r.vc_invalid_locations for r in results)
    tot_direct_req_corr = sum(r.direct_req_correct for r in results)
    tot_vc_req_corr = sum(r.vc_req_correct for r in results)
    tot_reqs = sum(r.total_reqs for r in results)

    tot_expected_issues = sum(len(c.ground_truth.expected_issues) for c in cases)

    direct_precision = (
        round(tot_direct_correct / (tot_direct_correct + tot_direct_fp), 4)
        if (tot_direct_correct + tot_direct_fp) > 0
        else 1.0
    )
    vc_precision = (
        round(tot_vc_correct / (tot_vc_correct + tot_vc_fp), 4)
        if (tot_vc_correct + tot_vc_fp) > 0
        else 1.0
    )

    direct_recall = (
        round(tot_direct_correct / tot_expected_issues, 4) if tot_expected_issues > 0 else 1.0
    )
    vc_recall = round(tot_vc_correct / tot_expected_issues, 4) if tot_expected_issues > 0 else 1.0

    direct_req_accuracy = round(tot_direct_req_corr / tot_reqs, 4) if tot_reqs > 0 else 1.0
    vc_req_accuracy = round(tot_vc_req_corr / tot_reqs, 4) if tot_reqs > 0 else 1.0

    tot_tokens_direct = sum(r.direct_tokens for r in results)
    tot_tokens_vc = sum(r.vc_tokens for r in results)

    # Pricing: $0.075 / 1M input, $0.30 / 1M output -> avg $0.15 / 1M tokens
    cost_direct = round(tot_tokens_direct * 0.00000015, 6)
    cost_vc = round(tot_tokens_vc * 0.00000015, 6)

    summary = {
        "evaluation_version": "1.0.0",
        "split": split,
        "cases_count": len(cases),
        "live_gemini_active": live_active,
        "live_status_message": "LIVE GEMINI VALIDATION ACTIVE"
        if live_active
        else "LIVE VALIDATION NOT RUN (Missing GEMINI_API_KEY/GOOGLE_API_KEY)",
        "model_name": getattr(adapter, "model_name", "simulated"),
        "wall_time_seconds": total_time,
        "direct_gemini": {
            "correct_findings": tot_direct_correct,
            "false_positives": tot_direct_fp,
            "invalid_locations": tot_direct_inv,
            "precision": direct_precision,
            "recall": direct_recall,
            "requirements_correct": tot_direct_req_corr,
            "requirements_accuracy": direct_req_accuracy,
            "total_tokens": tot_tokens_direct,
            "estimated_cost_usd": cost_direct,
        },
        "verifycraft": {
            "correct_findings": tot_vc_correct,
            "false_positives": tot_vc_fp,
            "invalid_locations": tot_vc_inv,
            "precision": vc_precision,
            "recall": vc_recall,
            "requirements_correct": tot_vc_req_corr,
            "requirements_accuracy": vc_req_accuracy,
            "total_tokens": tot_tokens_vc,
            "estimated_cost_usd": cost_vc,
        },
        "per_case_metrics": [asdict(r) for r in results],
    }

    if verbose:
        print("=" * 80)
        print(" VerifyCraft v0.2.0 Review Quality Evaluation Report")
        status_msg = summary["live_status_message"]
        print(f" Evaluation Split: {split.upper()} ({len(cases)} cases) | Status: {status_msg}")
        print(f" Model Identity:  {summary['model_name']}")
        print("=" * 80)
        header = f"{'Case ID':<38} {'Direct FP/Corr':<16} {'VC FP/Corr':<14} {'Reqs VC/Dir'}"
        print(header)
        print("-" * 80)
        for r in results:
            dir_str = f"{r.direct_false_positives}/{r.direct_correct_findings}"
            vc_str = f"{r.vc_false_positives}/{r.vc_correct_findings}"
            req_str = (
                f"{r.vc_req_correct}/{r.direct_req_correct} (of {r.total_reqs})"
                if r.total_reqs > 0
                else "N/A"
            )
            print(f"{r.case_id:<38} {dir_str:<16} {vc_str:<14} {req_str}")
        print("=" * 80)
        print(" Aggregate Quality Metrics:")
        print(f"  Total Ground-Truth Defects:    {tot_expected_issues}")
        print(
            f"  Precision:                     VerifyCraft {vc_precision:.1%} "
            f"vs Direct {direct_precision:.1%}"
        )
        print(
            f"  Recall (Defect Yield):         VerifyCraft {vc_recall:.1%} "
            f"vs Direct {direct_recall:.1%}"
        )
        fp_diff = tot_direct_fp - tot_vc_fp
        print(
            f"  False Positives:               VerifyCraft {tot_vc_fp} "
            f"vs Direct {tot_direct_fp} (Prevented: {fp_diff})"
        )
        print(
            f"  Invalid Code Locations:        VerifyCraft {tot_vc_inv} vs Direct {tot_direct_inv}"
        )
        print(
            f"  Requirements Accuracy:         VerifyCraft {vc_req_accuracy:.1%} "
            f"vs Direct {direct_req_accuracy:.1%}"
        )
        print(
            f"  Estimated API Cost:            VerifyCraft ${cost_vc:.5f} "
            f"vs Direct ${cost_direct:.5f}"
        )
        print("=" * 80)

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run VerifyCraft Review Quality Evaluation (Direct Gemini vs VerifyCraft)."
    )
    parser.add_argument(
        "--split",
        choices=["dev", "held_out", "all"],
        default="all",
        help="Evaluation split to run.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Attempt live Gemini API calls if credentials are present.",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON report.")
    args = parser.parse_args()

    report = run_evaluation(split=args.split, live_model=args.live, verbose=not args.json)

    if args.json:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
