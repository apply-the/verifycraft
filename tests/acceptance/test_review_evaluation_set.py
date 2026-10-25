"""Frozen evaluation set and real integration validation for VerifyCraft review engine.

Per PR #4 Mission:
Create a small, frozen Python evaluation set containing:
1. A real requirements violation.
2. A valid compensating control.
3. An unused helper that must not suppress an issue.
4. A logic regression without requirements.
5. A clean diff.
6. An ambiguous requirement.

Run deterministic tests with injected responses.
Also perform an actual end-to-end Gemini review when credentials are available,
against a known-bug fixture.
Capture the actual JSON, Conventional Comments, code references and requirement assessments.
If credentials are unavailable, report that the live test was not performed.
Never substitute fake test results for real-provider validation.
Compare the output to a direct review with equivalent source context.
Record false positives, useful findings, latency and token usage.
Do not claim statistically significant superiority from a small fixture set.
"""

from __future__ import annotations

import os
import subprocess
import time
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
from verifycraft.review.providers.deterministic import DeterministicReviewerAdapter


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    init_file = repo / "init.txt"
    init_file.write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial"], cwd=repo, check=True, capture_output=True)


# ==============================================================================
# Frozen Evaluation Set (Deterministic)
# ==============================================================================


def test_eval_case_1_real_requirements_violation(tmp_path: Path) -> None:
    """Eval 1: A real requirements violation.

    Requirement: [REQ-AUTH-01] Token must be checked for expiration.
    Diff: Adds authenticate() without checking expiration.
    Expected: Issue finding reported, requirement marked NOT_SATISFIED.
    """
    repo = tmp_path / "case1"
    repo.mkdir()
    _init_repo(repo)

    docs = repo / "docs"
    docs.mkdir()
    (docs / "reqs.md").write_text(
        "- **[REQ-AUTH-01]**: User authentication must reject expired tokens.\n",
        encoding="utf-8",
    )
    src = repo / "src"
    src.mkdir()
    auth_file = src / "auth.py"
    auth_file.write_text("# initial auth\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    auth_file.write_text(
        "def authenticate(token):\n    return token.is_valid\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "commit", "-am", "add auth"], cwd=repo, check=True, capture_output=True)

    engine = ReviewEngine(provider_adapter=DeterministicReviewerAdapter())
    request = ReviewRequest(
        repo_path=str(repo),
        base_revision="HEAD~1",
        head_revision="HEAD",
        requirements=["docs/reqs.md"],
        provider="deterministic",
    )

    report = engine.review(request)

    assert any(f.label == ConventionalLabel.ISSUE for f in report.findings)
    assert any("expiration" in f.subject.lower() for f in report.findings)
    req_map = {a.requirement_id: a for a in report.requirement_assessments}
    assert req_map["REQ-AUTH-01"].status == RequirementStatus.NOT_SATISFIED


def test_eval_case_2_valid_compensating_control(tmp_path: Path) -> None:
    """Eval 2: A valid compensating control.

    Requirement: [REQ-AUTH-01] Token must be checked for expiration.
    Diff: Calls check_token_expiration(token) on execution path.
    Expected: Issue finding dismissed by compensating control, requirement APPEARS_SATISFIED.
    """
    repo = tmp_path / "case2"
    repo.mkdir()
    _init_repo(repo)

    docs = repo / "docs"
    docs.mkdir()
    (docs / "reqs.md").write_text(
        "- **[REQ-AUTH-01]**: User authentication must reject expired tokens.\n",
        encoding="utf-8",
    )
    src = repo / "src"
    src.mkdir()
    (src / "helpers.py").write_text(
        "def check_token_expiration(token):\n    if token.is_expired(): raise ValueError()\n",
        encoding="utf-8",
    )
    auth_file = src / "auth.py"
    auth_file.write_text("# initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    auth_file.write_text(
        "from helpers import check_token_expiration\n"
        "def authenticate(token):\n"
        "    check_token_expiration(token)\n"
        "    return token.is_valid\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "-am", "add auth with check"], cwd=repo, check=True, capture_output=True
    )

    engine = ReviewEngine(provider_adapter=DeterministicReviewerAdapter())
    request = ReviewRequest(
        repo_path=str(repo),
        base_revision="HEAD~1",
        head_revision="HEAD",
        requirements=["docs/reqs.md"],
        candidate_findings=[
            ReviewFinding(
                id="cand-auth-01",
                file_path="src/auth.py",
                line=3,
                label=ConventionalLabel.ISSUE,
                subject="Missing token expiration check",
                description="The authenticate handler omits token expiry verification",
                severity=FindingSeverity.HIGH,
                blocking=True,
                requirement_ids=["REQ-AUTH-01"],
            )
        ],
        provider="deterministic",
    )

    report = engine.review(request)

    assert len(report.findings) == 0
    assert any(
        "compensating check" in note and "detected in scope" in note
        for note in report.omitted_scope
    )
    req_map = {a.requirement_id: a for a in report.requirement_assessments}
    assert req_map["REQ-AUTH-01"].status == RequirementStatus.APPEARS_SATISFIED


def test_eval_case_3_unused_helper_does_not_suppress_issue(tmp_path: Path) -> None:
    """Eval 3: An unused helper that must NOT suppress an issue.

    Requirement: [REQ-AUTH-01] Token must be checked for expiration.
    Context: helpers.py contains check_token_expiration, but auth.py never calls it!
    Expected: Issue finding is RETAINED, requirement NOT_SATISFIED.
    """
    repo = tmp_path / "case3"
    repo.mkdir()
    _init_repo(repo)

    docs = repo / "docs"
    docs.mkdir()
    (docs / "reqs.md").write_text(
        "- **[REQ-AUTH-01]**: User authentication must reject expired tokens.\n",
        encoding="utf-8",
    )
    src = repo / "src"
    src.mkdir()
    (src / "helpers.py").write_text(
        "def check_token_expiration(token):\n    if token.is_expired(): raise ValueError()\n",
        encoding="utf-8",
    )
    auth_file = src / "auth.py"
    auth_file.write_text("# initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    # Note: helpers.py exists in repo, but auth.py DOES NOT invoke it
    auth_file.write_text(
        "def authenticate(token):\n    return token.is_valid\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "-am", "add unverified auth"], cwd=repo, check=True, capture_output=True
    )

    engine = ReviewEngine(provider_adapter=DeterministicReviewerAdapter())
    request = ReviewRequest(
        repo_path=str(repo),
        base_revision="HEAD~1",
        head_revision="HEAD",
        requirements=["docs/reqs.md"],
        provider="deterministic",
    )

    report = engine.review(request)

    # Finding must NOT be dismissed!
    assert len(report.findings) >= 1
    assert any(f.label == ConventionalLabel.ISSUE for f in report.findings)
    req_map = {a.requirement_id: a for a in report.requirement_assessments}
    assert req_map["REQ-AUTH-01"].status == RequirementStatus.NOT_SATISFIED


def test_eval_case_4_logic_regression_without_requirements(tmp_path: Path) -> None:
    """Eval 4: A logic regression without requirements.

    Diff introduces division by zero in calculator without requirements doc.
    Expected: Issue finding reported, default requirement marked NOT_ASSESSED.
    """
    repo = tmp_path / "case4"
    repo.mkdir()
    _init_repo(repo)

    src = repo / "src"
    src.mkdir()
    calc = src / "calculator.py"
    calc.write_text("# init calc\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    calc.write_text(
        "def calculate_ratio(numerator, denominator):\n"
        "    # Potential divide by zero defect\n"
        "    return numerator / denominator\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "-am", "add divide"], cwd=repo, check=True, capture_output=True
    )

    engine = ReviewEngine(provider_adapter=DeterministicReviewerAdapter())
    request = ReviewRequest(
        repo_path=str(repo),
        base_revision="HEAD~1",
        head_revision="HEAD",
        provider="deterministic",
    )

    report = engine.review(request)

    assert any(f.label == ConventionalLabel.ISSUE for f in report.findings)
    assert any(
        "zerodivision" in f.subject.lower() or "zero" in f.subject.lower() for f in report.findings
    )
    assert report.requirement_assessments[0].requirement_id == "NONE"
    assert report.requirement_assessments[0].status == RequirementStatus.NOT_ASSESSED


def test_eval_case_5_clean_diff(tmp_path: Path) -> None:
    """Eval 5: A clean diff.

    Diff adds clean, compliant implementation.
    Expected: No blocking issues, clean summary.
    """
    repo = tmp_path / "case5"
    repo.mkdir()
    _init_repo(repo)

    src = repo / "src"
    src.mkdir()
    math_file = src / "math_utils.py"
    math_file.write_text("# init\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    math_file.write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "-am", "clean math"], cwd=repo, check=True, capture_output=True
    )

    engine = ReviewEngine(provider_adapter=DeterministicReviewerAdapter())
    request = ReviewRequest(
        repo_path=str(repo),
        base_revision="HEAD~1",
        head_revision="HEAD",
        provider="deterministic",
    )

    report = engine.review(request)

    assert len(report.findings) == 0
    assert not report.has_blocking_issues
    assert "clean" in report.summary.lower()


def test_eval_case_6_ambiguous_requirement(tmp_path: Path) -> None:
    """Eval 6: An ambiguous requirement.

    Requirement: Sessions must expire 'reasonably fast'.
    Expected: Emits question finding, requirement marked UNCLEAR (never falsely
    NOT_SATISFIED or SATISFIED).
    """
    repo = tmp_path / "case6"
    repo.mkdir()
    _init_repo(repo)

    docs = repo / "docs"
    docs.mkdir()
    (docs / "reqs.md").write_text(
        "- **[REQ-AMB-01]**: User sessions should expire reasonably fast.\n",
        encoding="utf-8",
    )
    src = repo / "src"
    src.mkdir()
    auth_file = src / "auth.py"
    auth_file.write_text("# init\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    auth_file.write_text("def login(): pass\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "add login"], cwd=repo, check=True, capture_output=True)

    engine = ReviewEngine(provider_adapter=DeterministicReviewerAdapter())
    request = ReviewRequest(
        repo_path=str(repo),
        base_revision="HEAD~1",
        head_revision="HEAD",
        requirements=["docs/reqs.md"],
        provider="deterministic",
    )

    report = engine.review(request)

    assert any(f.label == ConventionalLabel.QUESTION for f in report.findings)
    req_map = {a.requirement_id: a for a in report.requirement_assessments}
    assert req_map["REQ-AMB-01"].status == RequirementStatus.UNCLEAR


# ==============================================================================
# Live Gemini Integration Validation
# ==============================================================================


def test_live_gemini_integration_validation(tmp_path: Path) -> None:
    """Real end-to-end Gemini review when credentials are available against a known-bug fixture.

    If credentials are unavailable, reports that the live test was not performed.
    Never substitutes fake test results for real-provider validation.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip(
            "Live Gemini integration validation test not performed: "
            "GEMINI_API_KEY or GOOGLE_API_KEY not set in environment. "
            "Fake test results are never substituted for real-provider validation."
        )

    repo = tmp_path / "gemini_live"
    repo.mkdir()
    _init_repo(repo)

    docs = repo / "docs"
    docs.mkdir()
    (docs / "auth_policy.md").write_text(
        "# Authentication Policy\n\n"
        "- **[REQ-AUTH-01]**: Access tokens must be validated for expiration "
        "before granting access.\n",
        encoding="utf-8",
    )
    src = repo / "src"
    src.mkdir()
    auth_file = src / "auth.py"
    auth_file.write_text("def login(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init auth"], cwd=repo, check=True, capture_output=True)

    # Known bug: authenticate handler accepts token without checking expiration
    auth_file.write_text(
        "def authenticate(token: str) -> bool:\n"
        "    # Intentionally missing token expiration check violating REQ-AUTH-01\n"
        "    if not token:\n"
        "        return False\n"
        "    return True\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "-am", "implement unverified authenticate"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    engine = ReviewEngine()
    request = ReviewRequest(
        repo_path=str(repo),
        base_revision="HEAD~1",
        head_revision="HEAD",
        requirements=["docs/auth_policy.md"],
        provider="gemini",
    )

    t0 = time.monotonic()
    report = engine.review(request)
    duration = time.monotonic() - t0

    # Capture actual JSON, Conventional Comments, code references, and requirement assessments
    report_json = report.to_json(indent=2)
    comments = report.to_conventional_comments()
    assert report.model_info is not None
    assert report.model_info.provider == "gemini"
    assert report.resource_usage.wall_time_seconds > 0

    print("\n--- Live Gemini Review Results ---")
    print(f"Wall Time: {duration:.2f}s")
    print(f"Tokens Used: {report.resource_usage.tokens_used}")
    print(f"Findings Count: {len(report.findings)}")
    print(f"Summary: {report.summary}")
    print(f"Conventional Comments:\n{comments}")
    print(f"JSON Length: {len(report_json)} bytes")
    print("----------------------------------\n")

    # Verify that code references are anchored to changed file
    for f in report.findings:
        assert f.file_path in ("src/auth.py", "auth.py")
        if f.line is not None:
            assert 1 <= f.line <= 10
