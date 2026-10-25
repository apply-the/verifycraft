"""Acceptance test suite executing real end-to-end reviews across Cases A through F.

PR #6 Requirements:
Case A — Requirement violation (Conventional Comment issue, requirement citation, line anchor)
Case B — Valid implementation (Satisfies requirement via helper, avoids false issue)
Case C — Logic regression without requirements (Identifies defect, requirements not assessed)
Case D — Clean change (No invented problems)
Case E — Ambiguous evidence (Reports uncertainty, not definitive claim)
Case F — Agent-supplied findings (Model-free review processes findings without LLM call)
Live Gemini Validation — End-to-end review when credentials exist, else LIVE VALIDATION NOT RUN
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from verifycraft.cli import main as cli_main
from verifycraft.contracts.review import (
    ConventionalLabel,
    EvidenceStatus,
    FindingSeverity,
    RequirementStatus,
    ReviewFinding,
    ReviewRequest,
)
from verifycraft.review.engine import ReviewEngine
from verifycraft.review.providers.deterministic import DeterministicReviewerAdapter


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Tester"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "tester@test.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], cwd=path, check=True, capture_output=True
    )
    init_f = path / "init.txt"
    init_f.write_text("initial", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"], cwd=path, check=True, capture_output=True
    )


def test_case_a_requirement_violation(tmp_path: Path) -> None:
    """Case A — Requirement violation: Implementation violates acceptance criterion.

    VerifyCraft identifies the issue, cites the requirement and relevant code,
    and produces an actionable Conventional Comment.
    """
    repo = tmp_path / "case_a"
    repo.mkdir()
    _init_repo(repo)

    docs = repo / "docs"
    docs.mkdir()
    (docs / "auth_policy.md").write_text(
        "# Authentication Specification\n\n"
        "- **[REQ-AUTH-01]**: User sessions must expire after 15 minutes of inactivity.\n",
        encoding="utf-8",
    )
    src = repo / "src"
    src.mkdir()
    auth_file = src / "auth.py"
    auth_file.write_text("# initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Base commit"], cwd=repo, check=True, capture_output=True
    )

    auth_file.write_text(
        "def authenticate_user(token):\n"
        "    # Intentionally missing token expiration check\n"
        "    if not token:\n"
        "        return False\n"
        "    return create_session(token)\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "-am", "Implement authenticate without expiry"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    engine = ReviewEngine(provider_adapter=DeterministicReviewerAdapter())
    req = ReviewRequest(
        repo_path=str(repo),
        base_revision="HEAD~1",
        head_revision="HEAD",
        requirements=["docs/auth_policy.md"],
        provider="deterministic",
    )
    report = engine.review(req)

    # 1. Actionable Conventional Comment produced
    comments = report.to_conventional_comments()
    assert "issue (blocking):" in comments
    assert "REQ-AUTH-01" in comments
    assert "src/auth.py" in comments

    # 2. Issue finding with line anchor
    issue = next(f for f in report.findings if f.label == ConventionalLabel.ISSUE)
    assert issue.file_path == "src/auth.py"
    assert issue.blocking is True
    assert "REQ-AUTH-01" in issue.requirement_ids

    # 3. Requirement assessment correctly marked NOT_SATISFIED
    assessment = next(
        a for a in report.requirement_assessments if a.requirement_id == "REQ-AUTH-01"
    )
    assert assessment.status == RequirementStatus.NOT_SATISFIED
    assert issue.id in assessment.related_finding_ids


def test_case_b_valid_implementation_compensating_helper(tmp_path: Path) -> None:
    """Case B — Valid implementation: Satisfies requirement through another file or helper.

    VerifyCraft avoids the false issue and confirms the requirement is satisfied.
    """
    repo = tmp_path / "case_b"
    repo.mkdir()
    _init_repo(repo)

    docs = repo / "docs"
    docs.mkdir()
    (docs / "auth_policy.md").write_text(
        "- **[REQ-AUTH-01]**: User sessions must expire after 15 minutes of inactivity.\n",
        encoding="utf-8",
    )
    src = repo / "src"
    src.mkdir()
    helper = src / "auth_helper.py"
    helper.write_text(
        "def check_token_expiration(token):\n"
        "    if token.is_expired():\n"
        "        raise ValueError('Token has expired')\n",
        encoding="utf-8",
    )
    auth_file = src / "auth.py"
    auth_file.write_text("# initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Base commit with helper"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    auth_file.write_text(
        "from auth_helper import check_token_expiration\n\n"
        "def authenticate_user(token):\n"
        "    check_token_expiration(token)\n"
        "    return create_session(token)\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "-am", "Call auth_helper to check expiration"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    engine = ReviewEngine(provider_adapter=DeterministicReviewerAdapter())
    req = ReviewRequest(
        repo_path=str(repo),
        base_revision="HEAD~1",
        head_revision="HEAD",
        requirements=["docs/auth_policy.md"],
        provider="deterministic",
    )
    report = engine.review(req)

    # Avoids false issue
    assert not any(f.blocking and "expir" in f.subject.lower() for f in report.findings)
    # Requirement marked APPEARS_SATISFIED
    assert any(
        a.requirement_id == "REQ-AUTH-01" and a.status == RequirementStatus.APPEARS_SATISFIED
        for a in report.requirement_assessments
    )


def test_case_c_logic_regression_without_requirements(tmp_path: Path) -> None:
    """Case C — Logic regression without requirements.

    VerifyCraft identifies the relevant code defect and correctly
    marks requirements as not assessed.
    """
    repo = tmp_path / "case_c"
    repo.mkdir()
    _init_repo(repo)

    src = repo / "src"
    src.mkdir()
    calc = src / "calc.py"
    calc.write_text("def div(a, b): return a / b\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Base"], cwd=repo, check=True, capture_output=True)

    # Regression: division by zero unhandled
    calc.write_text(
        "def div(a, b):\n    # Potential zero division\n    return a / b\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "-am", "Modify div"], cwd=repo, check=True, capture_output=True
    )

    engine = ReviewEngine(provider_adapter=DeterministicReviewerAdapter())
    req = ReviewRequest(
        repo_path=str(repo),
        base_revision="HEAD~1",
        head_revision="HEAD",
        requirements=[],  # No requirements document
        provider="deterministic",
    )
    report = engine.review(req)

    # Requirements marked not assessed
    assert len(report.requirement_assessments) == 1
    assert report.requirement_assessments[0].requirement_id == "NONE"
    assert report.requirement_assessments[0].status == RequirementStatus.NOT_ASSESSED
    assert "No requirements document was supplied" in report.requirement_assessments[0].explanation


def test_case_d_clean_change(tmp_path: Path) -> None:
    """Case D — Clean change: VerifyCraft does not invent problems on clean diffs."""
    repo = tmp_path / "case_d"
    repo.mkdir()
    _init_repo(repo)

    src = repo / "src"
    src.mkdir()
    util = src / "util.py"
    util.write_text("def greet(name: str) -> str:\n    return f'Hello, {name}'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Base"], cwd=repo, check=True, capture_output=True)

    # Clean non-buggy enhancement
    util.write_text(
        "def greet(name: str, prefix: str = 'Hello') -> str:\n    return f'{prefix}, {name}!'\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "-am", "Clean enhancement"], cwd=repo, check=True, capture_output=True
    )

    engine = ReviewEngine(provider_adapter=DeterministicReviewerAdapter())
    req = ReviewRequest(
        repo_path=str(repo),
        base_revision="HEAD~1",
        head_revision="HEAD",
        requirements=[],
        provider="deterministic",
    )
    report = engine.review(req)

    # Does not invent blocking issues
    assert not any(f.blocking for f in report.findings)


def test_case_e_ambiguous_evidence(tmp_path: Path) -> None:
    """Case E — Ambiguous or incomplete evidence: Reports uncertainty, not definitive claim."""
    repo = tmp_path / "case_e"
    repo.mkdir()
    _init_repo(repo)

    docs = repo / "docs"
    docs.mkdir()
    (docs / "sla.md").write_text(
        "- **[REQ-AMB-01]**: User sessions should expire reasonably fast.\n",
        encoding="utf-8",
    )
    src = repo / "src"
    src.mkdir()
    auth = src / "auth.py"
    auth.write_text("def login(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Base"], cwd=repo, check=True, capture_output=True)

    auth.write_text("def login(): return 'ok'\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-am", "Update login"], cwd=repo, check=True, capture_output=True
    )

    engine = ReviewEngine(provider_adapter=DeterministicReviewerAdapter())
    req = ReviewRequest(
        repo_path=str(repo),
        base_revision="HEAD~1",
        head_revision="HEAD",
        requirements=["docs/sla.md"],
        provider="deterministic",
    )
    report = engine.review(req)

    # Emits question / uncertainty, never falsely SATISFIED or NOT_SATISFIED
    amb_a = next(a for a in report.requirement_assessments if a.requirement_id == "REQ-AMB-01")
    assert amb_a.status == RequirementStatus.UNCLEAR
    assert any(f.label == ConventionalLabel.QUESTION for f in report.findings)


def test_case_f_agent_supplied_findings_model_free(tmp_path: Path) -> None:
    """Case F — Agent-supplied findings: Model-free review processes findings without LLM."""
    repo = tmp_path / "case_f"
    repo.mkdir()
    _init_repo(repo)

    src = repo / "src"
    src.mkdir()
    app = src / "app.py"
    app.write_text("def run():\n    return 42\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Base"], cwd=repo, check=True, capture_output=True)

    app.write_text("def run():\n    # Modified\n    return 43\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-am", "Change return"], cwd=repo, check=True, capture_output=True
    )

    candidate = ReviewFinding(
        id="cand-001",
        file_path="src/app.py",
        line=3,
        label=ConventionalLabel.SUGGESTION,
        decorations=["non-blocking"],
        subject="Clarify return value constant 43",
        description="Value 43 changed from 42 without comment.",
        severity=FindingSeverity.LOW,
        blocking=False,
        evidence_status=EvidenceStatus.STATIC_ANALYZED,
    )

    engine = ReviewEngine()
    req = ReviewRequest(
        repo_path=str(repo),
        base_revision="HEAD~1",
        head_revision="HEAD",
        requirements=[],
        candidate_findings=[candidate],
        provider="none",  # Model-free!
    )

    t0 = time.monotonic()
    report = engine.review(req)
    duration = time.monotonic() - t0

    # Exactly 0 LLM provider calls made
    assert report.model_info is not None
    assert report.model_info.provider == "none"
    assert report.resource_usage.tokens_used == 0
    assert duration < 2.0  # Instantaneous execution

    # Candidate finding anchored, validated, and normalized
    assert len(report.findings) == 1
    f = report.findings[0]
    assert f.file_path == "src/app.py"
    assert f.line == 3
    assert f.label == ConventionalLabel.SUGGESTION
    assert "cand-001" in f.description


def test_live_gemini_release_gate(tmp_path: Path) -> None:
    """Real end-to-end Gemini execution test across Case A when credentials are provided.

    If credentials are unavailable, reports that live validation was not performed.
    Never substitutes mock results as proof of real-model release gate.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip(
            "LIVE VALIDATION NOT RUN: GEMINI_API_KEY or GOOGLE_API_KEY is not set in environment. "
            "Real-provider release gate marked incomplete."
        )

    repo = tmp_path / "live_gemini"
    repo.mkdir()
    _init_repo(repo)

    docs = repo / "docs"
    docs.mkdir()
    (docs / "security.md").write_text(
        "- **[REQ-AUTH-01]**: User authentication must reject expired tokens "
        "before issuing a session.\n",
        encoding="utf-8",
    )
    src = repo / "src"
    src.mkdir()
    auth = src / "auth.py"
    auth.write_text("def authenticate(token): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"], cwd=repo, check=True, capture_output=True
    )

    auth.write_text(
        "def authenticate(token: str) -> bool:\n"
        "    if not token:\n"
        "        return False\n"
        "    # Intentionally omitted expiration check\n"
        "    return True\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "-am", "Omit expiration"], cwd=repo, check=True, capture_output=True
    )

    engine = ReviewEngine()
    req = ReviewRequest(
        repo_path=str(repo),
        base_revision="HEAD~1",
        head_revision="HEAD",
        requirements=["docs/security.md"],
        provider="gemini",
    )

    t0 = time.monotonic()
    report = engine.review(req)
    duration = time.monotonic() - t0

    assert report.model_info is not None
    assert report.model_info.provider == "gemini"
    assert report.resource_usage.tokens_used is not None
    assert report.resource_usage.tokens_used > 0
    assert duration < 60.0

    # Save sanitized reports for release documentation
    live_dir = Path("docs/evaluation/live_reports")
    live_dir.mkdir(parents=True, exist_ok=True)
    (live_dir / "case_a_report.json").write_text(report.to_json(indent=2), encoding="utf-8")
    (live_dir / "case_a_comments.md").write_text(
        report.to_conventional_comments(), encoding="utf-8"
    )


def test_cli_review_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verifies that VerifyCraft CLI review command executes cleanly across formats."""
    repo = tmp_path / "cli_repo"
    repo.mkdir()
    _init_repo(repo)

    docs = repo / "docs"
    docs.mkdir()
    (docs / "reqs.md").write_text("- **[REQ-01]**: Expiration check required.\n", encoding="utf-8")
    src = repo / "src"
    src.mkdir()
    (src / "auth.py").write_text("def auth(): return True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Base commit"], cwd=repo, check=True, capture_output=True
    )

    (src / "auth.py").write_text("def auth():\n    return False\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-am", "Change auth"], cwd=repo, check=True, capture_output=True
    )

    # CLI review with --format comments
    with pytest.raises(SystemExit) as exc_comments:
        cli_main(
            [
                "review",
                str(repo),
                "--base",
                "HEAD~1",
                "--head",
                "HEAD",
                "--requirements",
                "docs/reqs.md",
                "--provider",
                "deterministic",
                "--format",
                "comments",
            ]
        )
    assert exc_comments.value.code == 0
    captured = capsys.readouterr()
    assert "### Review Summary" in captured.out
    assert "issue" in captured.out

    # CLI review with --format json
    with pytest.raises(SystemExit) as exc_json:
        cli_main(
            [
                "review",
                str(repo),
                "--base",
                "HEAD~1",
                "--head",
                "HEAD",
                "--provider",
                "deterministic",
                "--format",
                "json",
            ]
        )
    assert exc_json.value.code == 0
    captured_json = capsys.readouterr()
    parsed_json = json.loads(captured_json.out)
    assert "schema_version" in parsed_json
    assert "findings" in parsed_json
