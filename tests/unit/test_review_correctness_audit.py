"""Release-blocking audit tests for PR #4 Final Correctness Gate.

Verifies:
1. Requirement assessment truthfulness (downgrades unsupported appears_satisfied,
   speculative questions do not mark not_satisfied).
2. Model-free candidate evidence honest normalization (empirically_verified -> unverified,
   zero provider calls).
3. WORKTREE mode declarations and untracked file reporting.
4. Non-exhaustive report notice when omitted scope is non-empty.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from verifycraft.contracts.review import (
    ConventionalLabel,
    EvidenceStatus,
    FindingSeverity,
    RequirementStatus,
    ReviewFinding,
    ReviewReport,
    ReviewRequest,
)
from verifycraft.review.engine import ReviewEngine
from verifycraft.review.git import ChangedFile, GitContextCollector, GitReviewContext
from verifycraft.review.providers.base import (
    ProviderResponse,
    RawCandidateFinding,
    RawRequirementAssessment,
)


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)


def test_provider_appears_satisfied_without_concrete_locations_downgraded() -> None:
    """A provider claiming appears_satisfied with NO source locations must be downgraded."""
    engine = ReviewEngine()
    context = GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        base_commit="a" * 40,
        head_commit="b" * 40,
        raw_diff="diff --git a/src/auth.py b/src/auth.py\n+def login(): pass",
        changed_files={"src/auth.py": ChangedFile(path="src/auth.py", status="modified")},
        related_files={},
    )
    raw_a = RawRequirementAssessment(
        requirement_id="REQ-AUTH-01",
        status="appears_satisfied",
        explanation="Looks compliant based on high-level code structure.",
        related_locations=[],  # No concrete source locations!
    )

    status, explanation = engine._assess_from_provider(
        raw_a=raw_a,
        req_id="REQ-AUTH-01",
        is_relevant=True,
        context=context,
    )

    assert status == RequirementStatus.UNCLEAR
    assert (
        "no verifiable source locations" in explanation
        or "concrete implementation evidence" in explanation
    )


def test_provider_appears_satisfied_with_fabricated_locations_downgraded() -> None:
    """Provider claiming appears_satisfied citing files not in diff context must be downgraded."""
    engine = ReviewEngine()
    context = GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        base_commit="a" * 40,
        head_commit="b" * 40,
        raw_diff="diff --git a/src/auth.py b/src/auth.py\n+def login(): pass",
        changed_files={"src/auth.py": ChangedFile(path="src/auth.py", status="modified")},
        related_files={},
    )
    raw_a = RawRequirementAssessment(
        requirement_id="REQ-AUTH-01",
        status="appears_satisfied",
        explanation="Tokens expire in token_vault.py.",
        related_locations=[
            "src/token_vault.py:42"
        ],  # Fabricated location, not in changed or related files!
    )

    status, explanation = engine._assess_from_provider(
        raw_a=raw_a,
        req_id="REQ-AUTH-01",
        is_relevant=True,
        context=context,
    )

    assert status == RequirementStatus.UNCLEAR
    assert "no verifiable source locations" in explanation


def test_speculative_question_does_not_mark_requirement_not_satisfied(tmp_path: Path) -> None:
    """A speculative question or suggestion finding must NEVER mark requirement not_satisfied."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    f = repo / "src" / "api.py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("def handle_request(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    f.write_text("def handle_request(req):\n    return req.data\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "update"], cwd=repo, check=True, capture_output=True)

    fake_adapter = MagicMock()
    fake_adapter.model_name = "fake-model"
    fake_adapter.review.return_value = ProviderResponse(
        summary="Review.",
        candidate_findings=[
            RawCandidateFinding(
                file_path="src/api.py",
                line=2,
                label="question",  # Clarifying question, NOT an issue!
                subject="Consider rate limits",
                description="Did you consider whether rate limits are needed?",
                requirement_ids=["REQ-RATE-01"],
            )
        ],
        requirement_assessments=[
            RawRequirementAssessment(
                requirement_id="REQ-RATE-01",
                status="not_satisfied",  # Provider claims not_satisfied solely from question!
                explanation="Rate limit question raised.",
                related_locations=["src/api.py:2"],
            )
        ],
    )

    engine = ReviewEngine(provider_adapter=fake_adapter)
    report = engine.review(
        ReviewRequest(
            repo_path=str(repo),
            base_revision="HEAD~1",
            head_revision="HEAD",
            requirements=["inline:[REQ-RATE-01] Requests must be rate limited"],
            provider="fake",
        )
    )

    # Must NOT be marked not_satisfied solely from speculative question
    assert len(report.requirement_assessments) == 1
    req_a = report.requirement_assessments[0]
    assert req_a.status == RequirementStatus.UNCLEAR
    assert (
        "unresolved questions or suggestions" in req_a.explanation
        or "marked unclear" in req_a.explanation
    )


def test_model_free_review_performs_zero_provider_calls(tmp_path: Path) -> None:
    """Model-free review must perform ZERO provider calls while ingesting candidates."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    f = repo / "src" / "service.py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("def start(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    f.write_text("def start():\n    initialize()\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "call init"], cwd=repo, check=True, capture_output=True)

    mock_adapter = MagicMock()
    engine = ReviewEngine(provider_adapter=mock_adapter)

    candidate = ReviewFinding(
        id="cand-001",
        file_path="src/service.py",
        line=2,
        label=ConventionalLabel.ISSUE,
        subject="Missing error handling",
        description="initialize() might fail",
        severity=FindingSeverity.MEDIUM,
        evidence_status=EvidenceStatus.EMPIRICALLY_VERIFIED,
    )

    report = engine.review(
        ReviewRequest(
            repo_path=str(repo),
            base_revision="HEAD~1",
            head_revision="HEAD",
            candidate_findings=[candidate],
            provider="none",
        )
    )

    # Provider adapter review method must NOT have been called
    mock_adapter.review.assert_not_called()

    assert len(report.findings) == 1
    finding = report.findings[0]
    # Evidence must be downgraded from empirically_verified to unverified
    assert finding.evidence_status == EvidenceStatus.UNVERIFIED
    assert "Evidence status downgraded" in finding.description
    assert "cand-001" in finding.description


def test_worktree_mode_reports_untracked_files_and_declares_limitations(tmp_path: Path) -> None:
    """WORKTREE mode must declare limitations and report untracked files in omitted_scope."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    tracked = repo / "tracked.py"
    tracked.write_text("# tracked\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    # Modify tracked file in working tree
    tracked.write_text("# tracked modified\n", encoding="utf-8")

    # Create untracked file
    untracked = repo / "untracked_new_file.py"
    untracked.write_text("# secret new code\n", encoding="utf-8")

    collector = GitContextCollector(str(repo))
    context = collector.collect(base_revision="HEAD", head_revision="WORKTREE")

    assert context.is_working_tree is True
    # Omitted scope must declare WORKTREE mode limitations
    assert any("WORKTREE mode active" in s for s in context.omitted_scope)
    assert any("untracked_new_file.py" in s for s in context.omitted_scope)


def test_incomplete_review_notice_in_markdown_report() -> None:
    """When omitted scope is non-empty, report must declare review is not exhaustive."""
    report = ReviewReport(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        summary="Review complete.",
        omitted_scope=["Diff truncated: exceeded 2500 lines"],
    )

    md = report.to_markdown()
    assert "Omitted Scope" in md
    assert "not exhaustive" in md
