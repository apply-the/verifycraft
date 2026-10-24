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


def test_review_engine_candidate_anchor_offset_and_deduplication(tmp_path: Path) -> None:
    """Verifies that line anchoring notes and duplicate finding filtering work properly."""
    _init_repo(tmp_path)
    f = tmp_path / "app.py"
    f.write_text("line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\nline9\nline10\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Init"], cwd=tmp_path, check=True, capture_output=True)

    # Change only line 1
    f.write_text("line1_modified\nline2\nline3\nline4\nline5\nline6\nline7\nline8\nline9\nline10\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Change line 1"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    # Candidate 1 is on line 9 (unchanged line) -> should be anchored to line 1 with note
    cand1 = ReviewFinding(
        id="c-01",
        file_path="app.py",
        line=9,
        label=ConventionalLabel.NOTE,
        subject="Note on unchanged line",
        description="Original description",
    )
    # Duplicate candidate with exact same file, line, label, subject
    cand2 = ReviewFinding(
        id="c-02",
        file_path="app.py",
        line=9,
        label=ConventionalLabel.NOTE,
        subject="Note on unchanged line",
        description="Duplicate description",
    )

    engine = ReviewEngine()
    req = ReviewRequest(
        repo_path=str(tmp_path),
        base_revision="HEAD~1",
        head_revision="HEAD",
        candidate_findings=[cand1, cand2],
        provider="none",
    )
    report = engine.review(req)
    assert len(report.findings) == 1
    f_res = report.findings[0]
    assert f_res.line is None
    assert "downgraded to file-level comment" in f_res.description
    assert "Original description" in f_res.description


def test_review_engine_provider_unsupported_claim_downgrade(tmp_path: Path) -> None:
    """Verifies that provider claiming satisfied for untouched requirement is downgraded."""
    _init_repo(tmp_path)
    f = tmp_path / "calc.py"
    f.write_text("def add(a, b): return a + b\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Update calc"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    f.write_text("def add(a, b): return a + b + 0\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Update calc 2"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    # Provider claims APPEARS_SATISFIED for unrelated requirement REQ-DB
    fake_adapter = FakeReviewerAdapter(
        response=ProviderResponse(
            summary="Fake review",
            candidate_findings=[],
            requirement_assessments=[
                RawRequirementAssessment(
                    requirement_id="REQ-DB",
                    status="appears_satisfied",
                    explanation="Database looks great (hallucination).",
                )
            ],
        )
    )
    engine = ReviewEngine(provider_adapter=fake_adapter)
    req = ReviewRequest(
        repo_path=str(tmp_path),
        base_revision="HEAD~1",
        head_revision="HEAD",
        requirements=["- **[REQ-DB]**: Database connection pool must use SSL."],
        provider="fake",
    )
    report = engine.review(req)
    assert len(report.requirement_assessments) == 1
    ra = report.requirement_assessments[0]
    assert ra.requirement_id == "REQ-DB"
    assert ra.status == RequirementStatus.NOT_ASSESSED
    assert "Provider claimed 'appears_satisfied', but no relevant changes" in ra.explanation


def test_review_engine_unassessed_requirement_relevant_to_diff(tmp_path: Path) -> None:
    """Verifies unexamined requirement with diff keywords is marked UNCLEAR."""
    _init_repo(tmp_path)
    f = tmp_path / "calc.py"
    f.write_text("def multiply(a, b): return a * b\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    f.write_text("def multiply(a, b): return a * b * 1\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Update calc"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    # REQ-CALC-02 has 'multiply' keyword which matches the diff, but model provides no assessment
    fake_adapter = FakeReviewerAdapter(
        response=ProviderResponse(
            summary="Checked diff",
            candidate_findings=[],
            requirement_assessments=[],
        )
    )
    engine = ReviewEngine(provider_adapter=fake_adapter)
    req = ReviewRequest(
        repo_path=str(tmp_path),
        base_revision="HEAD~1",
        head_revision="HEAD",
        requirements=["- **[REQ-CALC-02]**: Multiply function must handle overflow."],
        provider="fake",
    )
    report = engine.review(req)
    assert len(report.requirement_assessments) == 1
    ra = report.requirement_assessments[0]
    assert ra.requirement_id == "REQ-CALC-02"
    assert ra.status == RequirementStatus.UNCLEAR
    assert "Changes touch components related to" in ra.explanation


def test_review_engine_requirement_with_clarifying_findings(tmp_path: Path) -> None:
    """Verifies that non-violating clarifying questions mark requirement UNCLEAR."""
    _init_repo(tmp_path)
    f = tmp_path / "calc.py"
    f.write_text("def calc(): return 42\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Init"], cwd=tmp_path, check=True, capture_output=True)

    f.write_text("def calc(): return 43\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Update"], cwd=tmp_path, check=True, capture_output=True)

    question_cand = ReviewFinding(
        id="q-01",
        file_path="calc.py",
        line=1,
        label=ConventionalLabel.QUESTION,
        subject="Clarification on return constant",
        description="Why 43 instead of 42?",
        requirement_ids=["REQ-01"],
    )
    engine = ReviewEngine()
    req = ReviewRequest(
        repo_path=str(tmp_path),
        base_revision="HEAD~1",
        head_revision="HEAD",
        requirements=["- **[REQ-01]**: Calculation return value."],
        candidate_findings=[question_cand],
        provider="none",
    )
    report = engine.review(req)
    assert len(report.requirement_assessments) == 1
    ra = report.requirement_assessments[0]
    assert ra.status == RequirementStatus.UNCLEAR
    assert "has unresolved questions or suggestions" in ra.explanation


def test_requirement_relevance_stopwords_only(tmp_path: Path) -> None:
    """Verifies requirement relevance returns False when requirement contains only stop words."""
    from verifycraft.review.git import GitContextCollector
    from verifycraft.review.requirements import ParsedRequirement

    _init_repo(tmp_path)
    f = tmp_path / "calc.py"
    f.write_text("def calc(): return 1\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Init"], cwd=tmp_path, check=True, capture_output=True)

    f.write_text("def calc(): return 2\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Update"], cwd=tmp_path, check=True, capture_output=True)

    collector = GitContextCollector(repo_path=str(tmp_path))
    ctx = collector.collect(base_revision="HEAD~1", head_revision="HEAD")

    engine = ReviewEngine()
    # "with this that from have" are all in engine.STOP_WORDS, and "in", "it" < 3 chars
    req = ParsedRequirement(id="REQ-01", text="with this that from have in it", source_file=None)
    assert engine._is_requirement_relevant_to_context(req, ctx) is False
