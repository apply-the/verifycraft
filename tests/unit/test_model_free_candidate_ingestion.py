"""Unit tests for model-free candidate ingestion and CLI provider configuration.

Per PR #4 Mission:
- Make ReviewRequest.candidate_findings operational without requiring Gemini or fake provider.
- Accept agent-supplied structured candidates, validate grounding, process through pipeline.
- Reject invalid or unsupported claims conservatively (e.g. untouched files).
- Do not claim that deterministic formatting constitutes independent empirical verification.
- Enforce explicit provider selection in CLI or require --candidates.
- Emit remote source disclosure note on Gemini provider.
- Maintain CLI/SDK parity.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from verifycraft.cli import main
from verifycraft.contracts.review import (
    ConventionalLabel,
    EvidenceStatus,
    FindingSeverity,
    RequirementStatus,
    ReviewFinding,
    ReviewRequest,
)
from verifycraft.review.engine import ReviewEngine


@pytest.fixture
def repo_with_change(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

    f = repo / "calculator.py"
    f.write_text("def divide(a, b):\n    return a / b\n", encoding="utf-8")
    subprocess.run(["git", "add", "calculator.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    # Change
    f.write_text("def divide(a, b):\n    # add comment\n    return a / b\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-am", "touch file"], cwd=repo, check=True, capture_output=True
    )
    return repo


def test_sdk_model_free_candidate_ingestion(repo_with_change: Path) -> None:
    """Ensures host agents can supply candidate findings processed without external models."""
    engine = ReviewEngine()
    candidates = [
        ReviewFinding(
            id="agent-01",
            file_path="calculator.py",
            line=3,
            label=ConventionalLabel.ISSUE,
            subject="Unhandled division by zero",
            description="Denominator b is not checked for zero before division.",
            severity=FindingSeverity.HIGH,
            blocking=True,
            requirement_ids=["REQ-CALC-01"],
            evidence_status=EvidenceStatus.EMPIRICALLY_VERIFIED,  # Should be normalized!
        )
    ]

    req_file = repo_with_change / "requirements.md"
    req_file.write_text("[REQ-CALC-01] Division by zero must be guarded.\n", encoding="utf-8")

    request = ReviewRequest(
        repo_path=str(repo_with_change),
        base_revision="HEAD~1",
        head_revision="HEAD",
        requirements=[str(req_file)],
        candidate_findings=candidates,
        provider="none",
    )

    report = engine.review(request)

    assert report.model_info is not None
    assert report.model_info.provider == "none"
    assert report.model_info.model_name == "model-free"
    assert len(report.findings) == 1
    f = report.findings[0]
    assert f.file_path == "calculator.py"
    assert f.line == 3
    # Verifies conservative grounding normalization
    assert f.evidence_status == EvidenceStatus.STATIC_ANALYZED
    assert f.blocking is True

    # Associated requirement is marked NOT_SATISFIED due to the unresolved issue
    assert len(report.requirement_assessments) == 1
    assert report.requirement_assessments[0].requirement_id == "REQ-CALC-01"
    assert report.requirement_assessments[0].status == RequirementStatus.NOT_SATISFIED


def test_reject_candidate_on_untouched_file(repo_with_change: Path) -> None:
    """Ensures candidate findings on files not touched in the diff/context are discarded."""
    engine = ReviewEngine()
    candidates = [
        ReviewFinding(
            id="agent-ghost",
            file_path="unknown_file.py",
            line=10,
            label=ConventionalLabel.ISSUE,
            subject="Spurious finding on untouched file",
            description="Should be conservatively rejected.",
            severity=FindingSeverity.MEDIUM,
        )
    ]

    request = ReviewRequest(
        repo_path=str(repo_with_change),
        base_revision="HEAD~1",
        head_revision="HEAD",
        candidate_findings=candidates,
        provider="none",
    )

    report = engine.review(request)
    assert len(report.findings) == 0
    assert any(
        "untouched file 'unknown_file.py' was discarded" in note for note in report.omitted_scope
    )


def test_unexamined_requirement_never_silently_satisfied(repo_with_change: Path) -> None:
    """Regression test: Unexamined requirements must NEVER be marked appears_satisfied."""
    engine = ReviewEngine()
    req_file = repo_with_change / "requirements.md"
    # REQ-UNTOUCHED is completely unrelated to calculator changes
    req_file.write_text(
        "[REQ-UNTOUCHED] Database connections must use TLS encryption.\n",
        encoding="utf-8",
    )

    request = ReviewRequest(
        repo_path=str(repo_with_change),
        base_revision="HEAD~1",
        head_revision="HEAD",
        requirements=[str(req_file)],
        provider="none",
    )

    report = engine.review(request)
    assert len(report.requirement_assessments) == 1
    assessment = report.requirement_assessments[0]
    assert assessment.requirement_id == "REQ-UNTOUCHED"
    assert assessment.status == RequirementStatus.NOT_ASSESSED
    assert "not assessed" in assessment.explanation.lower()


def test_cli_model_free_candidate_ingestion(
    repo_with_change: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ensures CLI supports --candidates for model-free review with Conventional Comments."""
    cand_file = tmp_path / "candidates.json"
    cand_data = [
        {
            "file_path": "calculator.py",
            "line": 3,
            "label": "issue",
            "subject": "Missing zero division check",
            "description": "b can be 0",
            "severity": "high",
            "blocking": True,
        }
    ]
    cand_file.write_text(json.dumps(cand_data), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "review",
                str(repo_with_change),
                "--base",
                "HEAD~1",
                "--head",
                "HEAD",
                "--candidates",
                str(cand_file),
                "--format",
                "comments",
            ]
        )
    assert exc.value.code == 0

    out, _ = capsys.readouterr()
    assert "calculator.py:3" in out
    assert "issue (blocking): Missing zero division check" in out


def test_cli_explicit_provider_or_candidates_required(
    repo_with_change: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ensures CLI rejects review invocation without explicit provider or candidates."""
    with pytest.raises(SystemExit) as exc:
        main(["review", str(repo_with_change)])
    assert exc.value.code == 1

    _, err = capsys.readouterr()
    assert "Explicit provider selection is required" in err


def test_cli_remote_disclosure_note(
    repo_with_change: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ensures remote provider selection discloses source context transmission."""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key")
    with pytest.raises(SystemExit):
        main(
            [
                "review",
                str(repo_with_change),
                "--provider",
                "gemini",
                "--timeout",
                "1.0",
            ]
        )

    _, err = capsys.readouterr()
    assert "VerifyCraft: Remote provider 'gemini' selected" in err
    assert "transmitted to the Gemini API" in err
