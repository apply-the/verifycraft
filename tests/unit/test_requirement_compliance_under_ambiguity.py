"""Regression tests for requirement compliance classification under ambiguity.

Covers:
1. Ambiguous specifications (returns UNCLEAR with precedence/acceptance criteria explanation).
2. Non-blocking, speculative observations (do NOT force NOT_SATISFIED).
3. Proven violations (preserve NOT_SATISFIED for blocking or high-severity defects).
4. Contradictory assessments (resolves conflicting signals safely).
5. Compensating controls (preserves APPEARS_SATISFIED).
6. Deterministic offline replay of held_out_05 from recorded DeepSeek responses.
"""

from __future__ import annotations

import json
from pathlib import Path

from verifycraft.contracts.review import (
    ConventionalLabel,
    EvidenceStatus,
    FindingSeverity,
    RequirementStatus,
    ReviewFinding,
)
from verifycraft.review.engine import ReviewEngine
from verifycraft.review.git import ChangedFile, DiffHunk, GitReviewContext
from verifycraft.review.providers.base import (
    ProviderResponse,
    RawCandidateFinding,
    RawRequirementAssessment,
    ReviewerModelAdapter,
)
from verifycraft.review.requirements import ParsedRequirement

DUMMY_ACCOUNT_CONTENT = (
    "def get_account_identifier(account: dict) -> str:\n"
    "    # uuid adoption\n"
    "    return account.get('uuid')\n"
)
DUMMY_ACCOUNT_DIFF = (
    "@@ -1,1 +1,3 @@\n"
    "+def get_account_identifier(account: dict) -> str:\n"
    "+    # uuid adoption\n"
    "+    return account.get('uuid')\n"
)


def _make_dummy_context(
    file_path: str = "src/models/account.py",
    content: str = DUMMY_ACCOUNT_CONTENT,
) -> GitReviewContext:
    hunk = DiffHunk(
        old_start=1,
        old_lines=1,
        new_start=1,
        new_lines=3,
        content=DUMMY_ACCOUNT_DIFF,
    )
    cfile = ChangedFile(
        path=file_path,
        status="modified",
        content=content,
        changed_lines={1, 2, 3, 4, 5, 6, 7, 8, 9, 10},
        hunks=[hunk],
    )
    return GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision="feat",
        base_commit="0000000000000000000000000000000000000000",
        head_commit="1111111111111111111111111111111111111111",
        raw_diff=DUMMY_ACCOUNT_DIFF,
        changed_files={file_path: cfile},
        related_files={},
        is_working_tree=False,
    )


class _MockReplayAdapter(ReviewerModelAdapter):
    def __init__(self, response: ProviderResponse) -> None:
        self.response = response

    def review(
        self,
        prompt: str,
        system_instruction: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> ProviderResponse:
        return self.response


def test_ambiguous_specification_returns_unclear() -> None:
    """Clarification questions about unspecified precedence or criteria return UNCLEAR."""
    engine = ReviewEngine()
    req = ParsedRequirement(
        id="REQ-DATA-01",
        text="Accounts should adopt UUID identifiers during migration.",
        source_file="docs/migration.md",
    )
    question = ReviewFinding(
        id="rev-q01",
        file_path="src/models/account.py",
        line=3,
        label=ConventionalLabel.QUESTION,
        subject="UUID vs legacy_id priority is unspecified",
        description="The specification does not state whether UUID or legacy_id takes priority.",
        severity=FindingSeverity.MEDIUM,
        blocking=False,
        requirement_ids=["REQ-DATA-01"],
        evidence_status=EvidenceStatus.MODEL_INFERRED,
    )
    raw_assessments = {
        "REQ-DATA-01": RawRequirementAssessment(
            requirement_id="REQ-DATA-01",
            status="satisfied",
            explanation="Adopted UUID identifiers.",
            related_locations=["src/models/account.py:3"],
        )
    }
    context = _make_dummy_context()

    assessment = engine._assess_single_requirement(
        req=req,
        model_assessments_map=raw_assessments,
        deduped_findings=[question],
        dismissal_notes=[],
        context=context,
    )

    assert assessment.status == RequirementStatus.UNCLEAR
    expected_clause = "specification does not establish clear precedence or acceptance criteria"
    assert expected_clause in assessment.explanation
    assert "UUID vs legacy_id priority is unspecified" in assessment.explanation


def test_non_blocking_low_severity_issue_does_not_force_not_satisfied() -> None:
    """Non-blocking, low-severity code quality nits must not force NOT_SATISFIED."""
    engine = ReviewEngine()
    req = ParsedRequirement(
        id="REQ-DATA-01",
        text="Accounts should adopt UUID identifiers during migration.",
        source_file="docs/migration.md",
    )
    nit = ReviewFinding(
        id="rev-nit01",
        file_path="src/models/account.py",
        line=3,
        label=ConventionalLabel.ISSUE,
        subject="Truthiness-based fallback treats empty UUID as missing",
        description="Using 'or' means an empty string falls back to legacy_id.",
        severity=FindingSeverity.LOW,
        blocking=False,
        requirement_ids=["REQ-DATA-01"],
        evidence_status=EvidenceStatus.MODEL_INFERRED,
    )
    raw_assessments = {
        "REQ-DATA-01": RawRequirementAssessment(
            requirement_id="REQ-DATA-01",
            status="satisfied",
            explanation="UUID is returned when present, satisfying adoption during migration.",
            related_locations=["src/models/account.py:3"],
        )
    }
    context = _make_dummy_context()

    assessment = engine._assess_single_requirement(
        req=req,
        model_assessments_map=raw_assessments,
        deduped_findings=[nit],
        dismissal_notes=[],
        context=context,
    )

    # Low-severity non-blocking issue does NOT force not_satisfied; provider assessment holds
    assert assessment.status == RequirementStatus.APPEARS_SATISFIED
    assert "UUID is returned when present" in assessment.explanation


def test_proven_violation_forces_not_satisfied() -> None:
    """Blocking or high-severity defects force NOT_SATISFIED even if model claimed satisfied."""
    engine = ReviewEngine()
    req = ParsedRequirement(
        id="REQ-AUTH-01",
        text="User authentication must reject expired tokens before issuing a session.",
        source_file="docs/auth.md",
    )
    defect = ReviewFinding(
        id="rev-sec01",
        file_path="src/auth/service.py",
        line=10,
        label=ConventionalLabel.ISSUE,
        subject="Expired tokens are accepted as valid",
        description="The authenticate method fails to check token.expires_at.",
        severity=FindingSeverity.HIGH,
        blocking=True,
        requirement_ids=["REQ-AUTH-01"],
        evidence_status=EvidenceStatus.MODEL_INFERRED,
    )
    # Model hallucinated that requirement is satisfied
    raw_assessments = {
        "REQ-AUTH-01": RawRequirementAssessment(
            requirement_id="REQ-AUTH-01",
            status="satisfied",
            explanation="Token validation logic was updated.",
            related_locations=["src/auth/service.py:10"],
        )
    }
    context = _make_dummy_context("src/auth/service.py")

    assessment = engine._assess_single_requirement(
        req=req,
        model_assessments_map=raw_assessments,
        deduped_findings=[defect],
        dismissal_notes=[],
        context=context,
    )

    assert assessment.status == RequirementStatus.NOT_SATISFIED
    assert "unresolved issue(s) identified (rev-sec01)" in assessment.explanation


def test_non_blocking_high_severity_violation_forces_not_satisfied() -> None:
    """Non-blocking finding with high severity demonstrating noncompliance forces NOT_SATISFIED."""
    engine = ReviewEngine()
    req = ParsedRequirement(
        id="REQ-SEC-01",
        text="Cryptographic signature validation must use constant-time comparison.",
        source_file="docs/sec.md",
    )
    non_blocking_high = ReviewFinding(
        id="rev-sec02",
        file_path="src/crypto/signatures.py",
        line=6,
        label=ConventionalLabel.ISSUE,
        subject="Non-constant-time signature comparison enables timing attacks",
        description="Standard == operator violates constant-time comparison mandate.",
        severity=FindingSeverity.HIGH,
        blocking=False,
        requirement_ids=["REQ-SEC-01"],
        evidence_status=EvidenceStatus.MODEL_INFERRED,
    )
    raw_assessments = {
        "REQ-SEC-01": RawRequirementAssessment(
            requirement_id="REQ-SEC-01",
            status="not_satisfied",
            explanation="Uses == instead of constant-time comparison.",
            related_locations=["src/crypto/signatures.py:6"],
        )
    }
    context = _make_dummy_context("src/crypto/signatures.py")

    assessment = engine._assess_single_requirement(
        req=req,
        model_assessments_map=raw_assessments,
        deduped_findings=[non_blocking_high],
        dismissal_notes=[],
        context=context,
    )

    assert assessment.status == RequirementStatus.NOT_SATISFIED
    assert "rev-sec02" in assessment.explanation


def test_contradictory_assessments_resolution() -> None:
    """Contradictory signals (e.g. model claims not_satisfied with no issues) become UNCLEAR."""
    engine = ReviewEngine()
    req = ParsedRequirement(
        id="REQ-AUDIT-01",
        text="All transactions must record audit metadata.",
        source_file="docs/audit.md",
    )
    # Model claims not_satisfied without pointing to any actual violating issue in diff
    raw_assessments = {
        "REQ-AUDIT-01": RawRequirementAssessment(
            requirement_id="REQ-AUDIT-01",
            status="not_satisfied",
            explanation="Unsure if audit metadata is recorded.",
            related_locations=["src/models/account.py:3"],
        )
    }
    context = _make_dummy_context()

    assessment = engine._assess_single_requirement(
        req=req,
        model_assessments_map=raw_assessments,
        deduped_findings=[],
        dismissal_notes=[],
        context=context,
    )

    # Contradiction flagged: provider claimed non-satisfaction without concrete violating issues
    assert assessment.status == RequirementStatus.UNCLEAR
    assert "marked unclear for clarification" in assessment.explanation


def test_compensating_control_preserves_appears_satisfied() -> None:
    """Static compensating controls in context establish APPEARS_SATISFIED."""
    engine = ReviewEngine()
    req = ParsedRequirement(
        id="REQ-MATH-01",
        text="Error rate calculation must handle zero total requests without crashing.",
        source_file="docs/specs.md",
    )
    context = _make_dummy_context("src/analytics/metrics.py")

    assessment = engine._assess_single_requirement(
        req=req,
        model_assessments_map={},
        deduped_findings=[],
        dismissal_notes=["Compensating control active for REQ-MATH-01: zero-check guard verified."],
        context=context,
    )

    assert assessment.status == RequirementStatus.APPEARS_SATISFIED
    assert "Statically detected compensating control in context" in assessment.explanation


def test_held_out_05_deepseek_replay_regression() -> None:
    """Deterministic replay of recorded DeepSeek Variant B response for held_out_05."""
    from benchmarks.evaluate_review import (
        _build_synthetic_git_context,
        _run_variant_replay,
    )
    from benchmarks.review_dataset import load_review_eval_dataset

    cases = load_review_eval_dataset(split="held_out")
    case = next(c for c in cases if c.case_id == "held_out_05_ambiguous_partial_migration")
    evidence_path = Path("benchmarks/data/review_eval/reports/live_deepseek_evaluation_raw.json")
    raw_rep = json.loads(evidence_path.read_text(encoding="utf-8"))
    raw_case = next(c for c in raw_rep["per_case_metrics"] if c["case_id"] == case.case_id)
    vb = raw_case["variant_b"]

    candidate_findings = [RawCandidateFinding(**f) for f in vb["findings"]]
    assessments = [RawRequirementAssessment(**a) for a in vb["assessments"]]
    resp_b = ProviderResponse(
        summary="Replay",
        candidate_findings=candidate_findings,
        requirement_assessments=assessments,
    )
    context = _build_synthetic_git_context(case)

    report, _ = _run_variant_replay(case, context, resp_b)
    req_map = {a.requirement_id: a for a in report.requirement_assessments}

    assert "REQ-DATA-01" in req_map
    ra = req_map["REQ-DATA-01"]
    # Must be UNCLEAR, matching ground truth
    assert ra.status == RequirementStatus.UNCLEAR
    # Must identify missing migration precedence or acceptance criteria
    expected_clause = "specification does not establish clear precedence or acceptance criteria"
    assert expected_clause in ra.explanation
    assert "UUID vs legacy_id priority is unspecified" in ra.explanation


def test_req_math_01_and_req_auth_02_preservation() -> None:
    """Verifies that REQ-MATH-01 and REQ-AUTH-02 remain APPEARS_SATISFIED in replay."""
    from benchmarks.evaluate_review import (
        _build_synthetic_git_context,
        _run_variant_replay,
    )
    from benchmarks.review_dataset import load_review_eval_dataset

    cases = load_review_eval_dataset(split="all")
    evidence_path = Path("benchmarks/data/review_eval/reports/live_deepseek_evaluation_raw.json")
    raw_rep = json.loads(evidence_path.read_text(encoding="utf-8"))

    for cid, expected_req in [
        ("dev_02_compensating_control_division", "REQ-MATH-01"),
        ("held_out_04_compensating_helper_auth", "REQ-AUTH-02"),
    ]:
        case = next(c for c in cases if c.case_id == cid)
        raw_case = next(c for c in raw_rep["per_case_metrics"] if c["case_id"] == cid)
        vb = raw_case["variant_b"]
        candidate_findings = [RawCandidateFinding(**f) for f in vb["findings"]]
        assessments = [RawRequirementAssessment(**a) for a in vb["assessments"]]
        resp_b = ProviderResponse(
            summary="Replay",
            candidate_findings=candidate_findings,
            requirement_assessments=assessments,
        )
        context = _build_synthetic_git_context(case)

        report, _ = _run_variant_replay(case, context, resp_b)
        req_map = {a.requirement_id: a for a in report.requirement_assessments}

        assert expected_req in req_map
        assert req_map[expected_req].status == RequirementStatus.APPEARS_SATISFIED
