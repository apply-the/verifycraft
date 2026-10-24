"""Unit tests for ReviewEngine edge cases and branch coverage."""

import subprocess
from pathlib import Path

import pytest

from verifycraft.contracts.review import (
    ConventionalLabel,
    FindingSeverity,
    RequirementStatus,
    ReviewFinding,
    ReviewRequest,
)
from verifycraft.review.engine import ReviewEngine
from verifycraft.review.providers.base import (
    ProviderConfigurationError,
    ProviderResponse,
    RawCandidateFinding,
    RawRequirementAssessment,
)
from verifycraft.review.providers.deterministic import FakeReviewerAdapter


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Tester"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.local"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    # Create initial commit so branch reference exists
    init_file = path / ".gitkeep"
    init_file.write_text("", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_review_engine_unsupported_provider(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    engine = ReviewEngine()
    req = ReviewRequest(repo_path=str(tmp_path), provider="nonexistent_llm")
    with pytest.raises(ProviderConfigurationError) as exc:
        engine.review(req)
    assert "Unsupported provider" in str(exc.value)


def test_review_engine_candidate_findings_ingestion(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    f = tmp_path / "app.py"
    f.write_text("def run(): return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Init"], cwd=tmp_path, check=True, capture_output=True)

    f.write_text("def run(): return 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Update"], cwd=tmp_path, check=True, capture_output=True)

    # Supply an external candidate finding directly in ReviewRequest
    candidate = ReviewFinding(
        id="cand-01",
        file_path="app.py",
        line=1,
        label=ConventionalLabel.SUGGESTION,
        subject="Consider adding type hints",
        description="Return type is unannotated.",
    )

    fake_adapter = FakeReviewerAdapter(
        response=ProviderResponse(
            summary="Checked app.py",
            candidate_findings=[],
            requirement_assessments=[],
        )
    )
    engine = ReviewEngine(provider_adapter=fake_adapter)
    req = ReviewRequest(
        repo_path=str(tmp_path),
        base_revision="HEAD~1",
        head_revision="HEAD",
        candidate_findings=[candidate],
        provider="fake",
    )

    report = engine.review(req)
    assert len(report.findings) == 1
    assert report.findings[0].file_path == "app.py"
    assert report.findings[0].subject == "Consider adding type hints"


def test_review_engine_fallback_for_unknown_label_and_status(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    f = tmp_path / "app.py"
    f.write_text("def run(): return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Init"], cwd=tmp_path, check=True, capture_output=True)

    f.write_text("def run(): return 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Update"], cwd=tmp_path, check=True, capture_output=True)

    # Provider returns unknown label and unknown severity and unknown status
    fake_adapter = FakeReviewerAdapter(
        response=ProviderResponse(
            summary="Checked app.py",
            candidate_findings=[
                RawCandidateFinding(
                    file_path="app.py",
                    line=1,
                    label="weird_label",
                    severity="extreme",
                    subject="Something weird",
                    description="Unknown enum values",
                )
            ],
            requirement_assessments=[
                RawRequirementAssessment(
                    requirement_id="REQ-01",
                    status="maybe_sorta_satisfied",
                    explanation="Unknown status",
                )
            ],
        )
    )

    engine = ReviewEngine(provider_adapter=fake_adapter)
    req = ReviewRequest(
        repo_path=str(tmp_path),
        base_revision="HEAD~1",
        head_revision="HEAD",
        requirements=["- **[REQ-01]**: App must return integer."],
        provider="fake",
    )

    report = engine.review(req)
    assert len(report.findings) == 1
    # Fallback to ISSUE and MEDIUM
    assert report.findings[0].label == ConventionalLabel.ISSUE
    assert report.findings[0].severity == FindingSeverity.MEDIUM

    # Requirement status fell back to UNCLEAR
    assert len(report.requirement_assessments) == 1
    assert report.requirement_assessments[0].status == RequirementStatus.UNCLEAR
