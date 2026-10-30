"""Evaluation runner comparing Direct Gemini Review vs VerifyCraft Review across 3 variants.

Execution Modes:
1. SIMULATED:
   - Hermetic, deterministic, reproducible contract fixture validation.
   - Validates pipeline contracts, AST compensating control checks, and line bounds.
   - Never claimed as an empirical Gemini comparison.
   - Tokens, latency, and costs are synthetic estimates.
2. LIVE:
   - Calls the actual configured Gemini model via official Google GenAI SDK.
   - Requires GEMINI_API_KEY or GOOGLE_API_KEY.
   - Fails closed immediately if credentials are unavailable.
   - NEVER silently falls back to simulated results.
   - Records actual model identity, real token usage, and measured latency.

Evaluation Variants:
- Variant A: Direct Gemini, diff plus explicit requirements. (Diff-only baseline)
- Variant B: Direct Gemini, same bounded surrounding context and requirements as VerifyCraft.
- Variant C: VerifyCraft, using the same model and source context as B, with downstream validation.
- Variant C-Replay: Replays exact captured raw candidates from Variant B through VerifyCraft
  pipeline to isolate downstream filtering, line grounding, and comment rendering from LLM variance.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, TextIO

from benchmarks.adjudication import (
    AdjudicationCatalog,
    AdjudicationDecision,
)
from benchmarks.review_dataset import (
    ExpectedIssue,
    GroundTruth,
    ReviewEvalCase,
    load_review_eval_dataset,
)
from verifycraft.contracts.review import (
    ReviewReport,
    ReviewRequest,
)
from verifycraft.review.engine import ReviewEngine
from verifycraft.review.git import ChangedFile, DiffHunk, GitReviewContext
from verifycraft.review.providers.base import (
    ProviderConfigurationError,
    ProviderResponse,
    RawCandidateFinding,
    RawRequirementAssessment,
    ReviewerModelAdapter,
    load_dotenv_if_present,
)
from verifycraft.review.providers.registry import (
    PROVIDER_CONFIGS,
    is_local_provider,
    resolve_provider_adapter,
)
from verifycraft.review.requirements import ParsedRequirement

VARIANT_A_KEY = "variant_a"
VARIANT_B_KEY = "variant_b"
VARIANT_C_KEY = "variant_c"
VARIANT_REPLAY_KEY = "variant_replay"

SIMULATED_DISCLAIMER = (
    "[DISCLAIMER] These results are generated using SimulatedGeminiReviewer for "
    "hermetic contract and processing validation. They are NOT empirical Gemini "
    "measurements and must not be cited as real-world LLM evaluation data."
)


class BenchmarkProgressReporter:
    """Live progress reporter printing to stderr and supporting quiet mode."""

    def __init__(self, quiet: bool = False, stream: TextIO | None = None) -> None:
        self.quiet = quiet
        self.stream = stream if stream is not None else sys.stderr

    def log(self, message: str) -> None:
        """Write informational progress to stream if not quiet."""
        if not self.quiet:
            self.stream.write(f"{message}\n")
            self.stream.flush()

    def error(self, message: str) -> None:
        """Always write errors to stream, even when quiet is enabled."""
        self.stream.write(f"ERROR: {message}\n")
        self.stream.flush()

    def banner(
        self, mode: str, provider: str, model: str, split: str, total_cases: int
    ) -> None:
        if self.quiet:
            return
        planned = f" | Planned provider calls: {total_cases * 3}" if mode == "live" else ""
        self.log("=" * 80)
        self.log(" VerifyCraft Review Quality Evaluation")
        self.log(f" Mode: {mode.upper()} | Provider: {provider} | Model: {model}")
        self.log(f" Split: {split.upper()} ({total_cases} cases){planned}")
        self.log("=" * 80)

    def case_start(self, index: int, total: int, case_id: str) -> None:
        if not self.quiet:
            self.log(f"[{index}/{total}] Case: {case_id}")

    def variant_start(self, variant_name: str) -> None:
        if not self.quiet:
            self.log(f"  -> {variant_name}: started call...")

    def variant_done(
        self, variant_name: str, latency: float, tokens: int | None = None
    ) -> None:
        if not self.quiet:
            tok_str = f" (tokens: {tokens})" if tokens is not None else ""
            self.log(f"  <- {variant_name}: completed in {latency:.2f}s{tok_str}")


@dataclass
class VariantMetrics:
    """Metrics for one evaluation variant on a single case."""

    variant_name: str
    correct_findings: int
    false_positives: int
    false_positives_clean: int
    invalid_locations: int
    requirements_correct: int
    total_requirements: int
    comments_count: int
    tokens_used: int
    prompt_tokens: int | None
    output_tokens: int | None
    latency_seconds: float
    estimated_cost_usd: float
    findings: list[dict[str, Any]]
    assessments: list[dict[str, Any]]


@dataclass
class CaseEvaluationMetrics:
    """Evaluation metrics comparing all three variants plus replay for a single case."""

    case_id: str
    name: str
    split: str
    category: str
    expected_verdict: str
    expected_defects_count: int
    total_requirements: int
    variant_a: VariantMetrics
    variant_b: VariantMetrics
    variant_c: VariantMetrics
    variant_replay: VariantMetrics


@dataclass
class AggregateVariantSummary:
    """Aggregated metrics across all cases for a specific review variant."""

    variant_name: str
    display_title: str
    total_cases: int
    expected_defects: int
    correct_findings: int
    defect_recall: float
    false_positives: int
    false_positives_clean: int
    actionable_precision: float
    invalid_locations: int
    requirements_correct: int
    total_requirements: int
    requirements_accuracy: float
    total_comments: int
    total_tokens: int
    total_prompt_tokens: int
    total_output_tokens: int
    total_latency_seconds: float
    estimated_cost_usd: float


class ReplayReviewerAdapter(ReviewerModelAdapter):
    """Adapter that replays an exact captured ProviderResponse without secondary model calls."""

    def __init__(self, response: ProviderResponse, model_name: str = "replay-captured") -> None:
        self.response = response
        self.model_name = model_name

    def review(
        self,
        prompt: str,
        system_instruction: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> ProviderResponse:
        return self.response


class SimulatedGeminiReviewer(ReviewerModelAdapter):
    """Simulated Gemini reviewer producing deterministic direct LLM responses for the 16 cases.

    NOTE: This is a synthetic test fixture used for hermetic contract validation.
    It does NOT execute real Gemini models and CANNOT be used to claim empirical LLM superiority.
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
                prompt_tokens=320,
                output_tokens=100,
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
                prompt_tokens=290,
                output_tokens=90,
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
                prompt_tokens=270,
                output_tokens=80,
            )

        # Case 4: Clean refactor
        if "_clean_token" in lowered:
            return ProviderResponse(
                summary="Clean refactor extracting helper token cleaning method.",
                candidate_findings=[],
                requirement_assessments=[],
                tokens_used=260,
                prompt_tokens=220,
                output_tokens=40,
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
                requirement_assessments=[
                    RawRequirementAssessment(
                        requirement_id="REQ-PERF-01",
                        status="appears_satisfied",
                        explanation="Requests set timeout=10.0 which appears reasonably fast.",
                        related_locations=["src/api/client.py:4"],
                    )
                ],
                tokens_used=410,
                prompt_tokens=310,
                output_tokens=100,
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
                prompt_tokens=330,
                output_tokens=100,
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
                prompt_tokens=280,
                output_tokens=90,
            )

        # Case 8: Multi-file missing argument
        # Discovered if helper is in context (Variant B and C); missed in diff-only (Variant A)
        if "process_order" in lowered and "currency" in lowered:
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
                prompt_tokens=340,
                output_tokens=100,
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
                prompt_tokens=250,
                output_tokens=80,
            )

        # Case 10: Surrounding code regression
        # Discovered if get_timeout_ms is in context (B and C); missed in diff-only (A)
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
                prompt_tokens=350,
                output_tokens=110,
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
                prompt_tokens=310,
                output_tokens=100,
            )

        # Held-out 2: Clean typing
        if "find_matching_ids" in lowered:
            return ProviderResponse(
                summary="Clean typing protocol modernization.",
                candidate_findings=[],
                requirement_assessments=[],
                tokens_used=240,
                prompt_tokens=200,
                output_tokens=40,
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
                prompt_tokens=350,
                output_tokens=100,
            )

        # Held-out 4: Compensating helper auth
        if "ensure_active_account" in lowered or "render_profile" in lowered:
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
                prompt_tokens=300,
                output_tokens=90,
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
                prompt_tokens=290,
                output_tokens=90,
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
                prompt_tokens=310,
                output_tokens=90,
            )

        return ProviderResponse(
            summary="Review completed without notable issues.",
            candidate_findings=[],
            requirement_assessments=[],
            tokens_used=250,
            prompt_tokens=210,
            output_tokens=40,
        )


def _build_synthetic_git_context(case: ReviewEvalCase) -> GitReviewContext:
    """Build in-memory GitReviewContext from evaluation case metadata."""
    changed_files: dict[str, ChangedFile] = {}
    for p, content in case.changed_files.items():
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


def _build_diff_only_prompt(case: ReviewEvalCase) -> str:
    """Build direct diff-only review prompt without surrounding file context (Variant A)."""
    parts = [
        "# Code Review Request",
        "",
        "## Unified Diff",
        "```diff",
        case.raw_diff or "(No changes in diff)",
        "```",
        "",
    ]
    if case.requirements:
        parts.append("## Explicit Requirements")
        parts.append("")
        for req in case.requirements:
            parts.append(f"- **[{req.id}]**: {req.text}")
        parts.append("")
    return "\n".join(parts)


def _get_finding_attr(f: Any, attr: str, default: Any = None) -> Any:
    """Extract an attribute from a finding object, pydantic model, or dictionary."""
    if isinstance(f, dict):
        return f.get(attr, default)
    return getattr(f, attr, default)


def _is_finding_match(
    f: Any,
    exp: ExpectedIssue,
    case: ReviewEvalCase,
) -> bool:
    """Check if finding matches expected defect with grounded file and line proximity."""
    f_path = _get_finding_attr(f, "file_path")
    if f_path != exp.file_path:
        return False

    label_raw = _get_finding_attr(f, "label", "issue")
    label_val = getattr(label_raw, "value", str(label_raw)).lower()
    blocking = bool(_get_finding_attr(f, "blocking", False))
    is_defect_claim = label_val == "issue" or blocking
    if not is_defect_claim:
        return False

    subj = str(_get_finding_attr(f, "subject", ""))
    desc = str(_get_finding_attr(f, "description", ""))
    combined = f"{subj} {desc}".lower()

    # Reject if finding contains any forbidden false positive phrase
    for forb in case.ground_truth.forbidden_false_positives:
        if forb.lower() in combined:
            return False

    # Check semantic match: primary subject_contains or any documented aliases
    has_semantic_match = exp.subject_contains.lower() in combined
    if not has_semantic_match and getattr(exp, "aliases", None):
        has_semantic_match = any(alias.lower() in combined for alias in exp.aliases)

    if not has_semantic_match:
        return False

    # Check evidence anchors if specified
    if getattr(exp, "evidence_anchors", None):
        has_anchor = any(anchor.lower() in combined for anchor in exp.evidence_anchors)
        # If neither primary subject_contains nor evidence anchor is mentioned, reject broad alias
        if not has_anchor and (exp.subject_contains.lower() not in combined):
            return False

    line = _get_finding_attr(f, "line", None)
    if not exp.line_range or line is None:
        return True
    return bool(exp.line_range[0] - 3 <= line <= exp.line_range[-1] + 3)


def _is_invalid_location(file_path: str, line: int | None, case: ReviewEvalCase) -> bool:
    """Check if finding cites a non-existent file or out-of-bounds line coordinate."""
    all_files = {**case.changed_files, **case.related_files}
    if file_path not in all_files:
        return True
    if line is not None:
        file_lines = len(all_files[file_path].splitlines())
        if line < 1 or line > file_lines:
            return True
    return False


def _match_findings_to_expected(
    findings: list[Any],
    expected_issues: list[ExpectedIssue],
    case: ReviewEvalCase,
) -> tuple[int, list[Any], list[Any]]:
    """Match candidate findings to expected issues in a 1-to-1 assignment.

    Returns:
        (correct_count, matched_findings, duplicate_findings)
    """
    matched_exps: set[int] = set()
    matched_findings: list[Any] = []
    duplicate_findings: list[Any] = []
    used_finding_indices: set[int] = set()

    for exp_idx, exp in enumerate(expected_issues):
        for f_idx, f in enumerate(findings):
            if f_idx in used_finding_indices:
                continue
            if _is_finding_match(f, exp, case):
                matched_exps.add(exp_idx)
                used_finding_indices.add(f_idx)
                matched_findings.append(f)
                break

    for f_idx, f in enumerate(findings):
        if f_idx in used_finding_indices:
            continue
        for exp in expected_issues:
            if _is_finding_match(f, exp, case):
                duplicate_findings.append(f)
                break

    return len(matched_exps), matched_findings, duplicate_findings


def _is_finding_false_positive(
    f: Any,
    gt: GroundTruth,
    case: ReviewEvalCase,
    matched_findings: list[Any] | None = None,
    duplicate_findings: list[Any] | None = None,
    adjudications: AdjudicationCatalog | None = None,
) -> bool:
    """Determine whether a candidate finding represents an unwarranted defect alert."""
    label_raw = _get_finding_attr(f, "label", "issue")
    label = getattr(label_raw, "value", str(label_raw)).lower()
    blocking = bool(_get_finding_attr(f, "blocking", False))
    is_defect_claim = label == "issue" or blocking
    if not is_defect_claim:
        return False

    if matched_findings is not None and f in matched_findings:
        return False

    if duplicate_findings is not None and f in duplicate_findings:
        return False

    if matched_findings is None and any(
        _is_finding_match(f, exp, case) for exp in gt.expected_issues
    ):
        return False

    # Check adjudications
    if adjudications:
        adj = adjudications.find_adjudication(
            case_id=case.case_id,
            file_path=_get_finding_attr(f, "file_path", ""),
            line=_get_finding_attr(f, "line", None),
            subject=_get_finding_attr(f, "subject", ""),
        )
        if adj:
            return adj.decision not in (
                AdjudicationDecision.KNOWN_TRUE_POSITIVE,
                AdjudicationDecision.ADJUDICATED_TRUE_POSITIVE,
                AdjudicationDecision.DUPLICATE_FINDING,
            )

    return True


def _count_correct_findings(
    findings: list[Any],
    expected_issues: list[ExpectedIssue],
    case: ReviewEvalCase,
) -> int:
    """Count expected ground-truth issues successfully discovered by findings."""
    correct_count, _, _ = _match_findings_to_expected(findings, expected_issues, case)
    return correct_count


def _count_false_positives(
    findings: list[Any],
    gt: GroundTruth,
    case: ReviewEvalCase,
    matched_findings: list[Any] | None = None,
    duplicate_findings: list[Any] | None = None,
    adjudications: AdjudicationCatalog | None = None,
) -> tuple[int, int]:
    """Count total false positives and false positives specifically on clean changes."""
    fps = 0
    fps_clean = 0
    for f in findings:
        if _is_finding_false_positive(
            f=f,
            gt=gt,
            case=case,
            matched_findings=matched_findings,
            duplicate_findings=duplicate_findings,
            adjudications=adjudications,
        ):
            fps += 1
            if gt.expected_verdict == "clean":
                fps_clean += 1
    return fps, fps_clean


def _count_invalid_locations(findings: list[Any], case: ReviewEvalCase) -> int:
    """Count fabricated or out-of-bounds code locations in findings."""
    invalids = 0
    for f in findings:
        file_path = _get_finding_attr(f, "file_path", "")
        line = _get_finding_attr(f, "line", None)
        if _is_invalid_location(file_path, line, case):
            invalids += 1
    return invalids


def _normalize_requirement_status(status_val: Any) -> str:
    """Normalize requirement assessment status to canonical vocabulary."""
    s = str(status_val).lower().strip().replace("-", "_")
    if s.startswith("requirementstatus."):
        s = s.split(".", 1)[1]
    if s in ("satisfied", "appears_satisfied"):
        return "appears_satisfied"
    return s


def _count_correct_requirements(
    assessments: list[Any],
    expected_requirements: dict[str, str],
) -> int:
    """Count requirements whose assessment status matches ground truth."""
    correct = 0
    for req_id, exp_status in expected_requirements.items():
        norm_exp = _normalize_requirement_status(exp_status)
        for a in assessments:
            a_req_id = str(_get_finding_attr(a, "requirement_id", ""))
            if a_req_id.upper() == req_id.upper():
                actual_status = _get_finding_attr(a, "status", "")
                actual_val = getattr(actual_status, "value", str(actual_status))
                norm_act = _normalize_requirement_status(actual_val)
                if norm_act == norm_exp:
                    correct += 1
                    break
    return correct


def _calculate_estimated_cost(
    tokens_used: int,
    prompt_tokens: int | None,
    output_tokens: int | None,
) -> float:
    """Calculate estimated API cost using standard pricing ($0.075/1M input, $0.30/1M output)."""
    if prompt_tokens is not None and output_tokens is not None:
        return (prompt_tokens * 0.000000075) + (output_tokens * 0.00000030)
    return tokens_used * 0.00000015


def _score_variant(
    variant_name: str,
    findings: list[Any],
    assessments: list[Any],
    case: ReviewEvalCase,
    tokens_used: int,
    prompt_tokens: int | None,
    output_tokens: int | None,
    latency_seconds: float,
    adjudications: AdjudicationCatalog | None = None,
) -> VariantMetrics:
    """Score review output against case ground truth."""
    gt = case.ground_truth
    correct_findings, matched, duplicates = _match_findings_to_expected(
        findings, gt.expected_issues, case
    )
    fps, fps_clean = _count_false_positives(
        findings=findings,
        gt=gt,
        case=case,
        matched_findings=matched,
        duplicate_findings=duplicates,
        adjudications=adjudications,
    )
    invalids = _count_invalid_locations(findings, case)
    reqs_correct = _count_correct_requirements(assessments, gt.expected_requirements)

    cost_usd = _calculate_estimated_cost(tokens_used, prompt_tokens, output_tokens)

    finding_dicts = [
        f if isinstance(f, dict) else (f.model_dump() if hasattr(f, "model_dump") else asdict(f))
        for f in findings
    ]
    assessment_dicts = [
        a if isinstance(a, dict) else (a.model_dump() if hasattr(a, "model_dump") else asdict(a))
        for a in assessments
    ]

    return VariantMetrics(
        variant_name=variant_name,
        correct_findings=correct_findings,
        false_positives=fps,
        false_positives_clean=fps_clean,
        invalid_locations=invalids,
        requirements_correct=reqs_correct,
        total_requirements=len(case.requirements),
        comments_count=len(findings),
        tokens_used=tokens_used,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        latency_seconds=latency_seconds,
        estimated_cost_usd=round(cost_usd, 6),
        findings=finding_dicts,
        assessments=assessment_dicts,
    )


def _run_variant_a(
    case: ReviewEvalCase,
    adapter: ReviewerModelAdapter,
) -> tuple[ProviderResponse, float]:
    """Execute Variant A: Direct Gemini Diff-Only."""
    prompt = _build_diff_only_prompt(case)
    t0 = time.perf_counter()
    resp = adapter.review(prompt)
    latency = round(time.perf_counter() - t0, 4)
    return resp, latency


def _run_variant_b(
    case: ReviewEvalCase,
    context: GitReviewContext,
    engine: ReviewEngine,
    adapter: ReviewerModelAdapter,
) -> tuple[ProviderResponse, float]:
    """Execute Variant B: Direct Gemini Context-Rich (same prompt as VerifyCraft)."""
    parsed_reqs = [
        ParsedRequirement(
            id=r.id,
            source_file=r.source_file,
            line_number=r.line_number,
            text=r.text,
        )
        for r in case.requirements
    ]
    prompt = engine._build_prompt(context=context, requirements=parsed_reqs)
    t0 = time.perf_counter()
    resp = adapter.review(prompt)
    latency = round(time.perf_counter() - t0, 4)
    return resp, latency


def _run_variant_c(
    case: ReviewEvalCase,
    context: GitReviewContext,
    engine: ReviewEngine,
) -> tuple[ReviewReport, float]:
    """Execute Variant C: VerifyCraft Context-Rich with downstream validation pipeline."""
    req_strings = [f"- **[{r.id}]**: {r.text}" for r in case.requirements]
    req = ReviewRequest(
        repo_path=".",
        base_revision="main",
        head_revision=case.head_commit[:8] if not case.is_working_tree else "WORKTREE",
        requirements=req_strings,
        provider="eval",
    )
    t0 = time.perf_counter()
    report = engine.review(req, context_override=context)
    latency = round(time.perf_counter() - t0, 4)
    return report, latency


def _run_variant_replay(
    case: ReviewEvalCase,
    context: GitReviewContext,
    raw_resp_b: ProviderResponse,
) -> tuple[ReviewReport, float]:
    """Execute Variant C-Replay: replay captured Variant B candidates through VerifyCraft."""
    req_strings = [f"- **[{r.id}]**: {r.text}" for r in case.requirements]
    req = ReviewRequest(
        repo_path=".",
        base_revision="main",
        head_revision=case.head_commit[:8] if not case.is_working_tree else "WORKTREE",
        requirements=req_strings,
        provider="eval",
    )
    replay_adapter = ReplayReviewerAdapter(raw_resp_b)
    replay_engine = ReviewEngine(provider_adapter=replay_adapter)
    t0 = time.perf_counter()
    report = replay_engine.review(req, context_override=context)
    latency = round(time.perf_counter() - t0, 4)
    return report, latency


def evaluate_single_case(
    case: ReviewEvalCase,
    adapter: ReviewerModelAdapter,
    adjudications: AdjudicationCatalog | None = None,
    reporter: BenchmarkProgressReporter | None = None,
) -> CaseEvaluationMetrics:
    """Evaluate all 3 variants plus candidate replay on a single evaluation case."""
    context = _build_synthetic_git_context(case)
    engine = ReviewEngine(provider_adapter=adapter)

    # 1. Variant A: Direct Gemini Diff-Only
    if reporter:
        reporter.variant_start("Variant A (Direct Diff-Only)")
    resp_a, latency_a = _run_variant_a(case, adapter)
    if reporter:
        reporter.variant_done("Variant A", latency_a, resp_a.tokens_used)
    metrics_a = _score_variant(
        variant_name=VARIANT_A_KEY,
        findings=resp_a.candidate_findings,
        assessments=resp_a.requirement_assessments,
        case=case,
        tokens_used=resp_a.tokens_used or 0,
        prompt_tokens=getattr(resp_a, "prompt_tokens", None),
        output_tokens=getattr(resp_a, "output_tokens", None),
        latency_seconds=latency_a,
        adjudications=adjudications,
    )

    # 2. Variant B: Direct Gemini Context-Rich
    if reporter:
        reporter.variant_start("Variant B (Direct Context-Rich)")
    resp_b, latency_b = _run_variant_b(case, context, engine, adapter)
    if reporter:
        reporter.variant_done("Variant B", latency_b, resp_b.tokens_used)
    metrics_b = _score_variant(
        variant_name=VARIANT_B_KEY,
        findings=resp_b.candidate_findings,
        assessments=resp_b.requirement_assessments,
        case=case,
        tokens_used=resp_b.tokens_used or 0,
        prompt_tokens=getattr(resp_b, "prompt_tokens", None),
        output_tokens=getattr(resp_b, "output_tokens", None),
        latency_seconds=latency_b,
        adjudications=adjudications,
    )

    # 3. Variant C: VerifyCraft Context-Rich
    if reporter:
        reporter.variant_start("Variant C (VerifyCraft)")
    report_c, latency_c = _run_variant_c(case, context, engine)
    if reporter:
        reporter.variant_done("Variant C", latency_c, report_c.resource_usage.tokens_used)
    metrics_c = _score_variant(
        variant_name=VARIANT_C_KEY,
        findings=report_c.findings,
        assessments=report_c.requirement_assessments,
        case=case,
        tokens_used=report_c.resource_usage.tokens_used or 0,
        prompt_tokens=getattr(report_c.resource_usage, "prompt_tokens", None),
        output_tokens=getattr(report_c.resource_usage, "output_tokens", None),
        latency_seconds=latency_c,
        adjudications=adjudications,
    )

    # 4. Variant C-Replay: Replay Variant B through VerifyCraft pipeline
    if reporter:
        reporter.variant_start("Variant C-Replay (Replayed B)")
    report_replay, latency_replay = _run_variant_replay(case, context, resp_b)
    if reporter:
        replay_tokens = report_replay.resource_usage.tokens_used
        reporter.variant_done("Variant C-Replay", latency_replay, replay_tokens)
    metrics_replay = _score_variant(
        variant_name=VARIANT_REPLAY_KEY,
        findings=report_replay.findings,
        assessments=report_replay.requirement_assessments,
        case=case,
        tokens_used=report_replay.resource_usage.tokens_used or 0,
        prompt_tokens=getattr(report_replay.resource_usage, "prompt_tokens", None),
        output_tokens=getattr(report_replay.resource_usage, "output_tokens", None),
        latency_seconds=latency_replay,
        adjudications=adjudications,
    )

    return CaseEvaluationMetrics(
        case_id=case.case_id,
        name=case.name,
        split=case.split,
        category=case.category,
        expected_verdict=case.ground_truth.expected_verdict,
        expected_defects_count=len(case.ground_truth.expected_issues),
        total_requirements=len(case.requirements),
        variant_a=metrics_a,
        variant_b=metrics_b,
        variant_c=metrics_c,
        variant_replay=metrics_replay,
    )


def _aggregate_variant_metrics(
    variant_attr: str,
    display_title: str,
    case_metrics: list[CaseEvaluationMetrics],
    total_expected_defects: int,
) -> AggregateVariantSummary:
    """Aggregate metrics across cases for a specific review variant."""
    var_list = [getattr(c, variant_attr) for c in case_metrics]
    tot_correct = sum(v.correct_findings for v in var_list)
    tot_fp = sum(v.false_positives for v in var_list)
    tot_fp_clean = sum(v.false_positives_clean for v in var_list)
    tot_inv = sum(v.invalid_locations for v in var_list)
    tot_req_corr = sum(v.requirements_correct for v in var_list)
    tot_reqs = sum(v.total_requirements for v in var_list)
    tot_comments = sum(v.comments_count for v in var_list)
    tot_tokens = sum(v.tokens_used for v in var_list)
    tot_prompt_tokens = sum(v.prompt_tokens or 0 for v in var_list)
    tot_output_tokens = sum(v.output_tokens or 0 for v in var_list)
    tot_latency = round(sum(v.latency_seconds for v in var_list), 4)
    tot_cost = round(sum(v.estimated_cost_usd for v in var_list), 6)

    precision = (
        round(tot_correct / (tot_correct + tot_fp), 4) if (tot_correct + tot_fp) > 0 else 1.0
    )
    recall = round(tot_correct / total_expected_defects, 4) if total_expected_defects > 0 else 1.0
    req_acc = round(tot_req_corr / tot_reqs, 4) if tot_reqs > 0 else 1.0

    return AggregateVariantSummary(
        variant_name=variant_attr,
        display_title=display_title,
        total_cases=len(case_metrics),
        expected_defects=total_expected_defects,
        correct_findings=tot_correct,
        defect_recall=recall,
        false_positives=tot_fp,
        false_positives_clean=tot_fp_clean,
        actionable_precision=precision,
        invalid_locations=tot_inv,
        requirements_correct=tot_req_corr,
        total_requirements=tot_reqs,
        requirements_accuracy=req_acc,
        total_comments=tot_comments,
        total_tokens=tot_tokens,
        total_prompt_tokens=tot_prompt_tokens,
        total_output_tokens=tot_output_tokens,
        total_latency_seconds=tot_latency,
        estimated_cost_usd=tot_cost,
    )


def _print_evaluation_banner(
    mode: str,
    split: str,
    cases_count: int,
    model_name: str,
    live_active: bool,
    provider: str = "gemini",
) -> None:
    """Print standard benchmark header banner."""
    print("=" * 80)
    print(" VerifyCraft Review Quality Evaluation (3-Variant Benchmark)")
    if mode == "simulated":
        print(" EXECUTION MODE: SIMULATED (Synthetic Hermetic Contract Fixture Validation)")
        print(f" {SIMULATED_DISCLAIMER}")
    else:
        status_msg = f"LIVE {provider.upper()} VALIDATION ACTIVE" if live_active else "LIVE BLOCKED"
        print(f" EXECUTION MODE: LIVE ({status_msg})")
    print(
        f" Evaluation Split: {split.upper()} ({cases_count} cases) | "
        f"Provider: {provider} | Model: {model_name}"
    )
    print("=" * 80)


def _print_case_metrics_table(results: list[CaseEvaluationMetrics]) -> None:
    """Print per-case comparative table."""
    header = (
        f"{'Case ID':<34} "
        f"{'A: Dir-Diff':<12} "
        f"{'B: Dir-Ctx':<12} "
        f"{'C: VerifyCraft':<14} "
        f"{'Replay':<10}"
    )
    print(header)
    print("-" * 88)
    for r in results:
        str_a = f"{r.variant_a.false_positives}fp/{r.variant_a.correct_findings}c"
        str_b = f"{r.variant_b.false_positives}fp/{r.variant_b.correct_findings}c"
        str_c = f"{r.variant_c.false_positives}fp/{r.variant_c.correct_findings}c"
        str_rep = f"{r.variant_replay.false_positives}fp/{r.variant_replay.correct_findings}c"
        print(f"{r.case_id:<34} {str_a:<12} {str_b:<12} {str_c:<14} {str_rep:<10}")
    print("=" * 88)


def _format_table_row(label: str, a: str, b: str, c: str, rep: str) -> str:
    """Format table row with 5 aligned columns."""
    return f"{label:<32} {a:<15} {b:<15} {c:<15} {rep:<15}"


def _print_aggregate_metrics(
    agg_a: AggregateVariantSummary,
    agg_b: AggregateVariantSummary,
    agg_c: AggregateVariantSummary,
    agg_rep: AggregateVariantSummary,
    gate_eval: dict[str, Any] | None = None,
    is_simulated: bool = False,
) -> None:
    """Print comparative aggregate summary table."""
    sim_note = " (Simulated Estimate)" if is_simulated else ""
    print(" Aggregate Evaluation Results Across 3 Variants:")
    print(
        _format_table_row(
            "Metric", "A: Direct-Diff", "B: Direct-Ctx", "C: VerifyCraft", "Replay (B->VC)"
        )
    )
    print("-" * 92)
    print(
        _format_table_row(
            "Actionable Precision",
            f"{agg_a.actionable_precision:.1%}",
            f"{agg_b.actionable_precision:.1%}",
            f"{agg_c.actionable_precision:.1%}",
            f"{agg_rep.actionable_precision:.1%}",
        )
    )
    print(
        _format_table_row(
            "Defect Recall (Yield)",
            f"{agg_a.defect_recall:.1%}",
            f"{agg_b.defect_recall:.1%}",
            f"{agg_c.defect_recall:.1%}",
            f"{agg_rep.defect_recall:.1%}",
        )
    )
    print(
        _format_table_row(
            "Total False Positives",
            str(agg_a.false_positives),
            str(agg_b.false_positives),
            str(agg_c.false_positives),
            str(agg_rep.false_positives),
        )
    )
    print(
        _format_table_row(
            "FP on Clean Changes",
            str(agg_a.false_positives_clean),
            str(agg_b.false_positives_clean),
            str(agg_c.false_positives_clean),
            str(agg_rep.false_positives_clean),
        )
    )
    print(
        _format_table_row(
            "Invalid Code Locations",
            str(agg_a.invalid_locations),
            str(agg_b.invalid_locations),
            str(agg_c.invalid_locations),
            str(agg_rep.invalid_locations),
        )
    )
    print(
        _format_table_row(
            "Requirements Accuracy",
            f"{agg_a.requirements_accuracy:.1%}",
            f"{agg_b.requirements_accuracy:.1%}",
            f"{agg_c.requirements_accuracy:.1%}",
            f"{agg_rep.requirements_accuracy:.1%}",
        )
    )
    print(
        _format_table_row(
            "Total Comments Count",
            str(agg_a.total_comments),
            str(agg_b.total_comments),
            str(agg_c.total_comments),
            str(agg_rep.total_comments),
        )
    )
    print(
        _format_table_row(
            "Total Tokens" + sim_note,
            str(agg_a.total_tokens),
            str(agg_b.total_tokens),
            str(agg_c.total_tokens),
            str(agg_rep.total_tokens),
        )
    )
    print(
        _format_table_row(
            "Estimated Cost (USD)" + sim_note,
            f"${agg_a.estimated_cost_usd:.5f}",
            f"${agg_b.estimated_cost_usd:.5f}",
            f"${agg_c.estimated_cost_usd:.5f}",
            f"${agg_rep.estimated_cost_usd:.5f}",
        )
    )
    print("=" * 92)
    print(" Interpretability & Decomposition Analysis:")
    print("  1. Contribution of Context Enrichment (A vs B):")
    recall_delta = agg_b.defect_recall - agg_a.defect_recall
    print(f"     Defect Recall Delta: {recall_delta:+.1%}")
    print("     (Surrounding & multi-file bugs discovered via bounded context)")
    print("  2. Contribution of VerifyCraft Downstream Pipeline (B vs C):")
    prec_delta = agg_c.actionable_precision - agg_b.actionable_precision
    fps_prev = agg_b.false_positives - agg_c.false_positives
    req_delta = agg_c.requirements_accuracy - agg_b.requirements_accuracy
    print(f"     Precision Delta:     {prec_delta:+.1%} (FPs prevented: {fps_prev})")
    print(f"     Requirements Delta:  {req_delta:+.1%} (Ambiguous claims corrected)")
    print(f"     Location Grounding:  {agg_c.invalid_locations} invalid coordinates")
    print("  3. Replay Pipeline Fidelity (B candidates -> VC pipeline):")
    print("     Replaying B's exact raw candidates through VerifyCraft confirms")
    print("     pipeline filtering independently of model nondeterminism.")
    if gate_eval:
        print("  4. Defensible Release Decision Policy:")
        print(f"     Gate Status:         {gate_eval['status']}")
        print(f"     Recommendation:      {gate_eval['recommendation']}")
        quality_str = "PASS" if gate_eval["quality_criteria_passed"] else "FAIL"
        print(f"     Quality Criteria:    {quality_str}")
        print("     Maintainer Approval: REQUIRED (Never emitted autonomously)")
    print("=" * 92)


def assess_release_gate(
    mode: str,
    live_active: bool,
    credentials_available: bool,
    live_provider_call_completed: bool,
    agg_b: AggregateVariantSummary,
    agg_c: AggregateVariantSummary,
    split: str = "all",
    provider: str = "gemini",
    missing_credentials_msg: str | None = None,
) -> dict[str, Any]:
    """Evaluate defensible multi-stage release decision policy.

    Explicitly separates:
    1. Credentials availability
    2. Live provider call completion
    3. Complete live evaluation completion (all cases, not just subset)
    4. Objective quality criteria pass
    5. Maintainer manual approval (never granted autonomously)
    """
    failures: list[str] = []
    is_complete_suite = split == "all"
    live_evaluation_completed = live_active and is_complete_suite

    if agg_c.actionable_precision < 0.85:
        failures.append(
            f"Actionable precision ({agg_c.actionable_precision:.1%}) < 85.0% threshold"
        )
    if agg_c.defect_recall < 0.90:
        failures.append(f"Defect recall ({agg_c.defect_recall:.1%}) < 90.0% threshold")
    if agg_c.invalid_locations > 0:
        failures.append(f"Invalid code locations detected ({agg_c.invalid_locations} found)")
    if agg_c.defect_recall < agg_b.defect_recall:
        failures.append(
            f"Recall regression: VerifyCraft ({agg_c.defect_recall:.1%}) < "
            f"Direct Context ({agg_b.defect_recall:.1%})"
        )
    if agg_c.actionable_precision < agg_b.actionable_precision:
        failures.append(
            f"Precision regression: VerifyCraft ({agg_c.actionable_precision:.1%}) < "
            f"Direct Context ({agg_b.actionable_precision:.1%})"
        )

    quality_passed = len(failures) == 0

    if mode == "simulated":
        status = "SIMULATED_TEST_ONLY"
        recommendation = "NO-GO (Simulated Benchmark Only — Real-Provider Live Validation Required)"
        summary_text = (
            "Hermetic simulated evaluation passes contract and fixture checks, "
            "but cannot satisfy the v0.2.0 release gate. Empirical live provider "
            "validation is required."
        )
    elif not credentials_available:
        status = "BLOCKED"
        recommendation = f"NO-GO (Live Evaluation Blocked — Missing Credentials for '{provider}')"
        summary_text = missing_credentials_msg or (
            "Live evaluation could not execute because required credentials for "
            f"provider '{provider}' were not found in the environment."
        )
    elif not live_provider_call_completed:
        status = "PROVIDER_CALL_FAILED"
        recommendation = "NO-GO (Live Provider Calls Failed During Evaluation)"
        summary_text = "Live evaluation started, but provider calls failed or timed out."
    elif not is_complete_suite:
        status = "PARTIAL_EVALUATION_ONLY"
        recommendation = (
            f"NO-GO (Partial Evaluation Only — Complete 'all' split evaluation required "
            f"for release gate; evaluated '{split}')"
        )
        summary_text = (
            f"Live evaluation was executed on the partial '{split}' subset. The v0.2.0 "
            f"release gate requires evaluation across the complete suite (all 16 cases)."
        )
    elif not quality_passed:
        status = "FAILED_QUALITY_GATE"
        recommendation = f"NO-GO (Quality Criteria Not Met: {'; '.join(failures)})"
        summary_text = "Live evaluation completed, but quality thresholds were not satisfied."
    else:
        status = "PENDING_MAINTAINER_APPROVAL"
        recommendation = (
            "PENDING_MAINTAINER_APPROVAL (Automated live evaluation passed; manual "
            "review of Conventional Comments, false positives, and requirement assessments "
            "required before release)"
        )
        summary_text = (
            "All automated quality criteria passed (precision, recall, zero invalid locations, "
            "positive delta over direct baseline). Maintainer manual review of Conventional "
            "Comments and requirements truthfulness is required prior to tagging or "
            "publishing v0.2.0."
        )

    return {
        "status": status,
        "recommendation": recommendation,
        "summary": summary_text,
        "credentials_available": credentials_available,
        "live_provider_call_completed": live_provider_call_completed,
        "live_evaluation_completed": live_evaluation_completed,
        "split_evaluated": split,
        "is_complete_evaluation": is_complete_suite,
        "quality_criteria_passed": quality_passed,
        "maintainer_approval_granted": False,
        "quality_audit": {
            "actionable_precision": agg_c.actionable_precision,
            "actionable_precision_passed": agg_c.actionable_precision >= 0.85,
            "defect_recall": agg_c.defect_recall,
            "defect_recall_passed": agg_c.defect_recall >= 0.90,
            "invalid_locations": agg_c.invalid_locations,
            "invalid_locations_passed": agg_c.invalid_locations == 0,
            "value_add_over_baseline": (
                agg_c.defect_recall >= agg_b.defect_recall
                and agg_c.actionable_precision >= agg_b.actionable_precision
            ),
            "failures": failures,
        },
    }


def _check_credentials_available(provider: str, base_url: str | None = None) -> bool:
    canonical = provider.lower()
    if is_local_provider(canonical, base_url=base_url):
        return True
    cfg = PROVIDER_CONFIGS.get(canonical)
    if cfg and cfg.api_key_env_var and os.environ.get(cfg.api_key_env_var):
        return True
    if canonical == "gemini":
        return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    if canonical == "bedrock":
        return bool(
            os.environ.get("AWS_ACCESS_KEY_ID")
            or os.environ.get("AWS_PROFILE")
            or os.environ.get("AWS_SESSION_TOKEN")
        )
    if canonical in ("openai", "openai-compatible"):
        return bool(os.environ.get("OPENAI_API_KEY"))
    return False


def _print_rescore_report(res: dict[str, Any]) -> None:
    """Print human-readable comparative rescoring report."""
    print("=" * 96)
    print(" VerifyCraft Review Quality Evaluation — Offline Rescore Report")
    print(f" Source Evidence File: {res['source_report_path']}")
    print(f" Provider / Model:     {res['provider']} / {res['model_name']}")
    print(f" Scorer / Dataset:     v{res['rescorer_version']} / v{res['dataset_version']}")
    print(f" Adjudications:        v{res['adjudications_version']}")
    print(f" Evaluated Cases:      {res['cases_count']} ({res['split'].upper()} split)")
    print("=" * 96)
    print(" 1. Ground-Truth Classification Deltas (Old vs Rescored Decisions):")
    if not res["decision_deltas"]:
        print("    (No per-case classification changes; scores are identical)")
    else:
        for d in res["decision_deltas"]:
            print(
                f"    - [{d['case_id']} | {d['variant']}] {d['metric']}: "
                f"{d['old_value']} -> {d['new_value']}"
            )
            print(f"      Rationale: {d['explanation']}")
    print("-" * 96)
    print(" 2. Comparative Rescored Metrics Across Variants:")
    print(
        _format_table_row(
            "Metric", "A: Direct-Diff", "B: Direct-Ctx", "C: VerifyCraft", "Replay (B->VC)"
        )
    )
    print("-" * 96)
    v_a = res["variants"][VARIANT_A_KEY]
    v_b = res["variants"][VARIANT_B_KEY]
    v_c = res["variants"][VARIANT_C_KEY]
    v_rep = res["variants"][VARIANT_REPLAY_KEY]
    print(
        _format_table_row(
            "Actionable Precision",
            f"{v_a['actionable_precision']:.1%}",
            f"{v_b['actionable_precision']:.1%}",
            f"{v_c['actionable_precision']:.1%}",
            f"{v_rep['actionable_precision']:.1%}",
        )
    )
    print(
        _format_table_row(
            "Defect Recall (Yield)",
            f"{v_a['defect_recall']:.1%}",
            f"{v_b['defect_recall']:.1%}",
            f"{v_c['defect_recall']:.1%}",
            f"{v_rep['defect_recall']:.1%}",
        )
    )
    print(
        _format_table_row(
            "Total False Positives",
            str(v_a["false_positives"]),
            str(v_b["false_positives"]),
            str(v_c["false_positives"]),
            str(v_rep["false_positives"]),
        )
    )
    print(
        _format_table_row(
            "FP on Clean Changes",
            str(v_a["false_positives_clean"]),
            str(v_b["false_positives_clean"]),
            str(v_c["false_positives_clean"]),
            str(v_rep["false_positives_clean"]),
        )
    )
    print(
        _format_table_row(
            "Invalid Code Locations",
            str(v_a["invalid_locations"]),
            str(v_b["invalid_locations"]),
            str(v_c["invalid_locations"]),
            str(v_rep["invalid_locations"]),
        )
    )
    print(
        _format_table_row(
            "Requirements Accuracy",
            f"{v_a['requirements_accuracy']:.1%}",
            f"{v_b['requirements_accuracy']:.1%}",
            f"{v_c['requirements_accuracy']:.1%}",
            f"{v_rep['requirements_accuracy']:.1%}",
        )
    )
    print("-" * 96)
    print(" 3. Release Gate Decision Policy Audit:")
    print(f"    Previous Live Gate Status:  {res['old_gate_status']}")
    print(f"    Rescored Gate Status:       {res['new_gate_status']}")
    print(f"    Previous Recommendation:    {res['old_recommendation']}")
    print(f"    Rescored Recommendation:    {res['new_recommendation']}")
    print("=" * 96)


def rescore_report(
    report_path: Path | str,
    adjudications_path: Path | str | None = None,
    dataset_path: Path | str | None = None,
    verbose: bool = True,
    reporter: BenchmarkProgressReporter | None = None,
) -> dict[str, Any]:
    """Offline rescore an existing benchmark JSON report.

    Uses updated matching and adjudications.
    """
    if reporter is None:
        reporter = BenchmarkProgressReporter()

    p = Path(report_path)
    if not p.exists():
        reporter.error(f"Report file not found: {report_path}")
        raise SystemExit(1)

    try:
        raw_rep = json.loads(p.read_text(encoding="utf-8"))
    except Exception as err:
        reporter.error(f"Invalid JSON in report file '{report_path}': {err}")
        raise SystemExit(1) from err

    if not isinstance(raw_rep, dict) or "per_case_metrics" not in raw_rep:
        reporter.error(
            f"Incompatible report format: missing 'per_case_metrics' in '{report_path}'."
        )
        raise SystemExit(1)

    raw_cases = raw_rep["per_case_metrics"]
    if not isinstance(raw_cases, list) or len(raw_cases) == 0:
        reporter.error(
            f"Incompatible report format: 'per_case_metrics' is empty in '{report_path}'."
        )
        raise SystemExit(1)

    cases_list = load_review_eval_dataset(dataset_path=dataset_path, split="all")
    cases_by_id = {c.case_id: c for c in cases_list}

    adj_path = (
        adjudications_path
        if adjudications_path is not None
        else Path(__file__).parent / "data" / "review_eval" / "adjudications.json"
    )
    adjudications = AdjudicationCatalog.load(adj_path)

    adj_ver = adjudications.records[0].version if adjudications.records else "1.0.0"
    reporter.log(f"Rescoring report '{p.name}' ({len(raw_cases)} cases) with catalog v{adj_ver}...")

    new_results: list[CaseEvaluationMetrics] = []
    decision_deltas: list[dict[str, Any]] = []

    for c_raw in raw_cases:
        cid = c_raw.get("case_id")
        if not cid or cid not in cases_by_id:
            reporter.error(f"Unknown or missing case_id '{cid}' in report.")
            raise SystemExit(1)
        case = cases_by_id[cid]

        rescored_vars: dict[str, VariantMetrics] = {}
        for vname in (VARIANT_A_KEY, VARIANT_B_KEY, VARIANT_C_KEY, VARIANT_REPLAY_KEY):
            vm_raw = c_raw.get(vname, {})
            findings = vm_raw.get("findings", [])
            assessments = vm_raw.get("assessments", [])

            old_corr = vm_raw.get("correct_findings", 0)
            old_fps = vm_raw.get("false_positives", 0)
            old_reqs = vm_raw.get("requirements_correct", 0)

            v_metrics = _score_variant(
                variant_name=vname,
                findings=findings,
                assessments=assessments,
                case=case,
                tokens_used=vm_raw.get("tokens_used", 0),
                prompt_tokens=vm_raw.get("prompt_tokens"),
                output_tokens=vm_raw.get("output_tokens"),
                latency_seconds=vm_raw.get("latency_seconds", 0.0),
                adjudications=adjudications,
            )
            rescored_vars[vname] = v_metrics

            if v_metrics.correct_findings != old_corr:
                decision_deltas.append(
                    {
                        "case_id": cid,
                        "variant": vname,
                        "metric": "correct_findings",
                        "old_value": old_corr,
                        "new_value": v_metrics.correct_findings,
                        "explanation": (
                            f"True positive count updated ({old_corr} -> "
                            f"{v_metrics.correct_findings}) "
                            "via ground-truth semantic defect alias matching."
                        ),
                    }
                )
            if v_metrics.false_positives != old_fps:
                decision_deltas.append(
                    {
                        "case_id": cid,
                        "variant": vname,
                        "metric": "false_positives",
                        "old_value": old_fps,
                        "new_value": v_metrics.false_positives,
                        "explanation": (
                            f"False positive count updated ({old_fps} -> "
                            f"{v_metrics.false_positives}) "
                            "via semantic matching and ground-truth adjudication."
                        ),
                    }
                )
            if v_metrics.requirements_correct != old_reqs:
                decision_deltas.append(
                    {
                        "case_id": cid,
                        "variant": vname,
                        "metric": "requirements_correct",
                        "old_value": old_reqs,
                        "new_value": v_metrics.requirements_correct,
                        "explanation": (
                            f"Requirement compliance updated ({old_reqs} -> "
                            f"{v_metrics.requirements_correct}) via status "
                            "vocabulary normalization ('satisfied' -> 'appears_satisfied')."
                        ),
                    }
                )

        new_results.append(
            CaseEvaluationMetrics(
                case_id=case.case_id,
                name=case.name,
                split=case.split,
                category=case.category,
                expected_verdict=case.ground_truth.expected_verdict,
                expected_defects_count=len(case.ground_truth.expected_issues),
                total_requirements=len(case.requirements),
                variant_a=rescored_vars[VARIANT_A_KEY],
                variant_b=rescored_vars[VARIANT_B_KEY],
                variant_c=rescored_vars[VARIANT_C_KEY],
                variant_replay=rescored_vars[VARIANT_REPLAY_KEY],
            )
        )

    total_expected_defects = sum(
        len(cases_by_id[c["case_id"]].ground_truth.expected_issues) for c in raw_cases
    )
    agg_a = _aggregate_variant_metrics(
        VARIANT_A_KEY, "Variant A (Direct Diff-Only)", new_results, total_expected_defects
    )
    agg_b = _aggregate_variant_metrics(
        VARIANT_B_KEY, "Variant B (Direct Context-Rich)", new_results, total_expected_defects
    )
    agg_c = _aggregate_variant_metrics(
        VARIANT_C_KEY, "Variant C (VerifyCraft)", new_results, total_expected_defects
    )
    agg_rep = _aggregate_variant_metrics(
        VARIANT_REPLAY_KEY, "Variant C-Replay (Replayed B)", new_results, total_expected_defects
    )

    mode_val = raw_rep.get("execution_mode", "live")
    provider_val = raw_rep.get("provider", "deepseek")
    split_val = raw_rep.get("split", "all")
    new_gate = assess_release_gate(
        mode=mode_val,
        live_active=(mode_val == "live"),
        credentials_available=(mode_val == "live"),
        live_provider_call_completed=True,
        agg_b=agg_b,
        agg_c=agg_c,
        split=split_val,
        provider=provider_val,
    )

    rescore_summary: dict[str, Any] = {
        "evaluation_version": "2.1.0",
        "rescorer_version": "2.1.0",
        "dataset_version": "1.1.0",
        "adjudications_version": adj_ver,
        "source_report_path": str(p.resolve()),
        "execution_mode": mode_val,
        "provider": provider_val,
        "model_name": raw_rep.get("model_name", "deepseek-chat"),
        "split": split_val,
        "cases_count": len(new_results),
        "total_expected_defects": total_expected_defects,
        "old_gate_status": raw_rep.get("release_gate", {}).get("status", "UNKNOWN"),
        "new_gate_status": new_gate["status"],
        "old_recommendation": raw_rep.get("release_gate", {}).get("recommendation", ""),
        "new_recommendation": new_gate["recommendation"],
        "variants": {
            VARIANT_A_KEY: asdict(agg_a),
            VARIANT_B_KEY: asdict(agg_b),
            VARIANT_C_KEY: asdict(agg_c),
            VARIANT_REPLAY_KEY: asdict(agg_rep),
        },
        "old_variants": raw_rep.get("variants", {}),
        "comparative_analysis": {
            "context_enrichment_impact": {
                "variant_a_recall": agg_a.defect_recall,
                "variant_b_recall": agg_b.defect_recall,
                "recall_delta": round(agg_b.defect_recall - agg_a.defect_recall, 4),
            },
            "downstream_validation_impact": {
                "variant_b_precision": agg_b.actionable_precision,
                "variant_c_precision": agg_c.actionable_precision,
                "precision_delta": round(
                    agg_c.actionable_precision - agg_b.actionable_precision, 4
                ),
                "false_positives_prevented": agg_b.false_positives - agg_c.false_positives,
                "invalid_locations_eliminated": agg_b.invalid_locations - agg_c.invalid_locations,
                "requirements_accuracy_delta": round(
                    agg_c.requirements_accuracy - agg_b.requirements_accuracy, 4
                ),
            },
            "replay_pipeline_fidelity": {
                "variant_b_candidates_evaluated": agg_b.total_comments,
                "replay_findings_retained": agg_rep.total_comments,
                "replay_precision": agg_rep.actionable_precision,
            },
        },
        "release_gate": new_gate,
        "decision_deltas": decision_deltas,
        "per_case_metrics": [asdict(r) for r in new_results],
    }

    if verbose:
        _print_rescore_report(rescore_summary)

    return rescore_summary


def run_evaluation(
    split: Literal["dev", "held_out", "all"] = "all",
    mode: Literal["simulated", "live"] = "simulated",
    provider: str = "gemini",
    model: str | None = None,
    base_url: str | None = None,
    allow_insecure_http: bool = False,
    verbose: bool = True,
    quiet: bool = False,
    adjudications_path: Path | str | None = None,
    reporter: BenchmarkProgressReporter | None = None,
) -> dict[str, Any]:
    """Execute the 3-variant review quality evaluation suite across all cases."""
    if reporter is None:
        reporter = BenchmarkProgressReporter(quiet=quiet)

    cases = load_review_eval_dataset(split=split)

    adj_path = (
        adjudications_path
        if adjudications_path is not None
        else Path(__file__).parent / "data" / "review_eval" / "adjudications.json"
    )
    adjudications = AdjudicationCatalog.load(adj_path)

    credentials_available = (
        _check_credentials_available(provider, base_url=base_url) if mode == "live" else False
    )
    if mode == "live":
        try:
            resolved_adapter = resolve_provider_adapter(
                provider_name=provider,
                model=model,
                base_url=base_url,
                allow_insecure_http=allow_insecure_http,
            )
            if resolved_adapter is None:
                raise ProviderConfigurationError(
                    f"Model-free provider '{provider}' cannot be evaluated in live benchmark mode."
                )
            adapter: ReviewerModelAdapter = resolved_adapter
            live_active = True
        except ProviderConfigurationError as err:
            cfg = PROVIDER_CONFIGS.get(provider.lower())
            env_hint = (cfg.api_key_env_var if cfg else None) or f"{provider.upper()}_API_KEY"
            error_msg = (
                f"Live evaluation requested (--mode live) for provider '{provider}', "
                f"but configuration failed: {err}\n"
                f"Fails closed: export {env_hint}=\"your-{provider}-key\" or pass --env-file."
            )
            reporter.error(error_msg)
            raise SystemExit(1) from err
    else:
        adapter = SimulatedGeminiReviewer()
        live_active = False

    model_display = getattr(adapter, "model_name", model or "simulated")
    reporter.banner(
        mode=mode,
        provider=provider,
        model=model_display,
        split=split,
        total_cases=len(cases),
    )

    results: list[CaseEvaluationMetrics] = []
    start_time = time.perf_counter()

    for i, c in enumerate(cases):
        reporter.case_start(i + 1, len(cases), c.case_id)
        m = evaluate_single_case(
            c, adapter, adjudications=adjudications, reporter=reporter
        )
        results.append(m)

    total_time = round(time.perf_counter() - start_time, 4)
    total_expected_defects = sum(len(c.ground_truth.expected_issues) for c in cases)

    agg_a = _aggregate_variant_metrics(
        VARIANT_A_KEY, "Variant A (Direct Diff-Only)", results, total_expected_defects
    )
    agg_b = _aggregate_variant_metrics(
        VARIANT_B_KEY, "Variant B (Direct Context-Rich)", results, total_expected_defects
    )
    agg_c = _aggregate_variant_metrics(
        VARIANT_C_KEY, "Variant C (VerifyCraft)", results, total_expected_defects
    )
    agg_rep = _aggregate_variant_metrics(
        VARIANT_REPLAY_KEY, "Variant C-Replay (Replayed B)", results, total_expected_defects
    )

    summary: dict[str, Any] = {
        "evaluation_version": "2.1.0",
        "execution_mode": mode,
        "provider": provider,
        "live_gate_status": "COMPLETED" if live_active else "BLOCKED (Missing API Credentials)",
        "model_name": getattr(adapter, "model_name", "simulated"),
        "split": split,
        "cases_count": len(cases),
        "total_expected_defects": total_expected_defects,
        "token_accounting_type": "actual_api_usage" if live_active else "synthetic_estimate",
        "wall_time_seconds": total_time,
        "variants": {
            VARIANT_A_KEY: asdict(agg_a),
            VARIANT_B_KEY: asdict(agg_b),
            VARIANT_C_KEY: asdict(agg_c),
            VARIANT_REPLAY_KEY: asdict(agg_rep),
        },
        "comparative_analysis": {
            "context_enrichment_impact": {
                "variant_a_recall": agg_a.defect_recall,
                "variant_b_recall": agg_b.defect_recall,
                "recall_delta": round(agg_b.defect_recall - agg_a.defect_recall, 4),
            },
            "downstream_validation_impact": {
                "variant_b_precision": agg_b.actionable_precision,
                "variant_c_precision": agg_c.actionable_precision,
                "precision_delta": round(
                    agg_c.actionable_precision - agg_b.actionable_precision, 4
                ),
                "false_positives_prevented": agg_b.false_positives - agg_c.false_positives,
                "invalid_locations_eliminated": agg_b.invalid_locations - agg_c.invalid_locations,
                "requirements_accuracy_delta": round(
                    agg_c.requirements_accuracy - agg_b.requirements_accuracy, 4
                ),
            },
            "replay_pipeline_fidelity": {
                "variant_b_candidates_evaluated": agg_b.total_comments,
                "replay_findings_retained": agg_rep.total_comments,
                "replay_precision": agg_rep.actionable_precision,
            },
        },
        "release_gate": assess_release_gate(
            mode=mode,
            live_active=live_active,
            credentials_available=credentials_available if mode == "live" else False,
            live_provider_call_completed=bool(live_active and len(results) > 0),
            agg_b=agg_b,
            agg_c=agg_c,
            split=split,
            provider=provider,
        ),
        "per_case_metrics": [asdict(r) for r in results],
    }
    summary["release_recommendation"] = summary["release_gate"]["recommendation"]

    reporter.log(f"Benchmark finished in {total_time:.2f}s across {len(cases)} cases.")
    reporter.log(f"Release gate status: {summary['release_gate']['status']}")

    if verbose:
        _print_evaluation_banner(
            mode,
            split,
            len(cases),
            summary["model_name"],
            live_active,
            provider=provider,
        )
        _print_case_metrics_table(results)
        _print_aggregate_metrics(
            agg_a,
            agg_b,
            agg_c,
            agg_rep,
            gate_eval=summary["release_gate"],
            is_simulated=(mode == "simulated"),
        )

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run VerifyCraft Review Quality Evaluation across 3 variants."
    )
    parser.add_argument(
        "--mode",
        choices=["simulated", "live"],
        default="simulated",
        help="Benchmark mode: 'simulated' (synthetic fixtures) or 'live' (real model calls).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Convenience alias for --mode live.",
    )
    parser.add_argument(
        "--provider",
        default="gemini",
        help="Reviewer model provider: 'gemini', 'deepseek', 'openai', 'anthropic', 'ollama', etc.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model identifier override (e.g. 'deepseek-flash', 'gpt-4o', 'gemini-2.5-flash').",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Base URL for custom OpenAI-compatible or local inference server.",
    )
    parser.add_argument(
        "--allow-insecure-http",
        action="store_true",
        help="Explicitly allow plain HTTP for non-loopback endpoints (insecure).",
    )
    parser.add_argument(
        "--split",
        choices=["dev", "held_out", "all"],
        default="all",
        help="Evaluation dataset split to run.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON report.",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress non-error progress output on stderr.",
    )
    parser.add_argument(
        "--rescore",
        type=Path,
        default=None,
        help="Path to existing evaluation JSON report to rescore offline without model calls.",
    )
    parser.add_argument(
        "--adjudications",
        type=Path,
        default=None,
        help="Path to ground-truth adjudications catalog JSON.",
    )
    parser.add_argument(
        "--load-dotenv",
        action="store_true",
        help="Explicitly load environment variables from .env in repository root.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Explicit path to environment file to load.",
    )
    args = parser.parse_args()

    opt_in = (
        bool(args.load_dotenv)
        or args.env_file is not None
        or os.environ.get("VERIFYCRAFT_LOAD_DOTENV", "").strip().lower() in ("1", "true", "yes")
    )
    if opt_in:
        load_dotenv_if_present(env_path=args.env_file, opt_in=True)

    reporter = BenchmarkProgressReporter(quiet=args.quiet)

    if args.rescore:
        rescored = rescore_report(
            report_path=args.rescore,
            adjudications_path=args.adjudications,
            verbose=not args.json,
            reporter=reporter,
        )
        if args.json:
            print(json.dumps(rescored, indent=2))
        return

    mode_val: Literal["simulated", "live"] = (
        "live" if args.live or args.mode == "live" else "simulated"
    )
    report = run_evaluation(
        split=args.split,
        mode=mode_val,
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        allow_insecure_http=args.allow_insecure_http,
        verbose=not args.json,
        quiet=args.quiet,
        adjudications_path=args.adjudications,
        reporter=reporter,
    )

    if args.json:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
