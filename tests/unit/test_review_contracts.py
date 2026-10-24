"""Unit tests for code review contracts and Conventional Comments formatting."""

import pytest
from pydantic import ValidationError

from verifycraft.contracts.review import (
    ConventionalLabel,
    EvidenceStatus,
    FindingSeverity,
    ModelInfo,
    RequirementAssessment,
    RequirementStatus,
    ReviewFinding,
    ReviewReport,
    ReviewResourceUsage,
)


def test_review_finding_conventional_comments_formatting() -> None:
    finding = ReviewFinding(
        id="rev-001",
        file_path="src/auth.py",
        line=42,
        label=ConventionalLabel.ISSUE,
        decorations=["blocking"],
        subject="Token expiration check is omitted",
        description="`authenticate_user()` does not verify `token.expires_at` timestamp.",
        severity=FindingSeverity.CRITICAL,
        blocking=True,
        requirement_ids=["REQ-AUTH-01"],
        evidence_status=EvidenceStatus.MODEL_INFERRED,
        suggested_action="Verify token expiration before creating session.",
    )

    comment = finding.format_conventional_comment()
    assert comment.startswith("issue (blocking): Token expiration check is omitted")
    assert "`authenticate_user()` does not verify `token.expires_at` timestamp." in comment
    assert "- **Requirement:** REQ-AUTH-01" in comment
    assert "- **Suggested Action:** Verify token expiration before creating session." in comment
    assert "- **Grounding:** model_inferred" in comment


def test_review_finding_canonical_labels() -> None:
    labels = [
        ConventionalLabel.PRAISE,
        ConventionalLabel.NITPICK,
        ConventionalLabel.SUGGESTION,
        ConventionalLabel.ISSUE,
        ConventionalLabel.TODO,
        ConventionalLabel.QUESTION,
        ConventionalLabel.THOUGHT,
    ]
    for label in labels:
        f = ReviewFinding(
            id="rev-test",
            file_path="test.py",
            label=label,
            subject="Test subject",
            description="Test description",
        )
        assert f.format_conventional_comment().startswith(label.value)


def test_review_finding_validation() -> None:
    # Prohibit empty file path
    with pytest.raises(ValidationError):
        ReviewFinding(
            id="rev-1",
            file_path="",
            subject="Subj",
            description="Desc",
        )

    # Prohibit absolute paths
    with pytest.raises(ValidationError):
        ReviewFinding(
            id="rev-1",
            file_path="/etc/passwd",
            subject="Subj",
            description="Desc",
        )

    # Prohibit path traversal
    with pytest.raises(ValidationError):
        ReviewFinding(
            id="rev-1",
            file_path="../outside.py",
            subject="Subj",
            description="Desc",
        )


def test_review_report_serialization() -> None:
    report = ReviewReport(
        repo_path=".",
        base_revision="main",
        head_revision="feature/auth",
        base_commit="1234567890abcdef",
        head_commit="abcdef1234567890",
        findings=[
            ReviewFinding(
                id="rev-01",
                file_path="src/auth.py",
                line=15,
                label=ConventionalLabel.ISSUE,
                decorations=["blocking"],
                subject="Missing expiry",
                description="Token expiry omitted.",
                blocking=True,
            )
        ],
        requirement_assessments=[
            RequirementAssessment(
                requirement_id="REQ-AUTH-01",
                text="Tokens expire after 15m.",
                status=RequirementStatus.NOT_SATISFIED,
                explanation="Auth handler omits check.",
                source_file="docs/auth.md",
            )
        ],
        summary="Found 1 blocking issue.",
        omitted_scope=["binary.bin"],
        resource_usage=ReviewResourceUsage(
            files_reviewed=1,
            diff_lines=25,
            context_bytes=1024,
            wall_time_seconds=0.15,
            tokens_used=150,
        ),
        model_info=ModelInfo(provider="deterministic", model_name="rule-engine"),
    )

    assert report.has_blocking_issues is True

    # Test JSON round-trip
    json_str = report.to_json()
    reconstructed = ReviewReport.from_json(json_str)
    assert reconstructed.base_revision == "main"
    assert len(reconstructed.findings) == 1
    assert reconstructed.findings[0].file_path == "src/auth.py"
    assert reconstructed.has_blocking_issues is True

    # Test Markdown rendering
    md = report.to_markdown()
    assert "# VerifyCraft Code Review Report" in md
    assert "REQ-AUTH-01" in md
    assert "issue (blocking): Missing expiry" in md
    assert "binary.bin" in md


def test_review_report_clean_summary() -> None:
    report = ReviewReport(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        findings=[],
        requirement_assessments=[],
        summary="All changes are clean.",
        omitted_scope=[],
        resource_usage=ReviewResourceUsage(),
    )
    assert report.has_blocking_issues is False
    comments = report.to_conventional_comments()
    assert "No issues or actionable findings identified" in comments
