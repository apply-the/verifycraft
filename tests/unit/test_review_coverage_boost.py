"""Targeted coverage tests for review subsystem components and edge cases."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from verifycraft.cli import main
from verifycraft.contracts.review import (
    ConventionalLabel,
    EvidenceStatus,
    FindingSeverity,
    RequirementStatus,
    ReviewFinding,
    ReviewReport,
    ReviewRequest,
)
from verifycraft.review.counterexamples import CounterexampleChecker
from verifycraft.review.engine import ReviewEngine
from verifycraft.review.git import ChangedFile, GitContextCollector, GitError, GitReviewContext
from verifycraft.review.providers.base import (
    ProviderConfigurationError,
    ProviderResponse,
    RawCandidateFinding,
    RawRequirementAssessment,
)
from verifycraft.review.providers.deterministic import DeterministicReviewerAdapter
from verifycraft.review.providers.gemini import GeminiReviewerAdapter
from verifycraft.review.requirements import RequirementsParser


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Initialize a clean Git repository for tests."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    f = tmp_path / "init.txt"
    f.write_text("initial", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


# ==============================================================================
# 1. Git Context & Reader Edge Cases
# ==============================================================================


def test_git_run_timeout(git_repo: Path) -> None:
    collector = GitContextCollector(str(git_repo))
    with (
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=5.0),
        ),
        pytest.raises(GitError, match="Git command timed out"),
    ):
        collector._run_git(["status"])


def test_git_run_generic_exception(git_repo: Path) -> None:
    collector = GitContextCollector(str(git_repo))
    with (
        patch("subprocess.run", side_effect=OSError("Process spawn failure")),
        pytest.raises(GitError, match="Failed to execute git command"),
    ):
        collector._run_git(["status"])


def test_git_repo_path_not_exists() -> None:
    with pytest.raises(GitError, match="Repository path does not exist"):
        GitContextCollector("/nonexistent/directory/path/xyz")


def test_git_not_inside_work_tree(git_repo: Path) -> None:
    collector = GitContextCollector(str(git_repo))
    with (
        patch.object(collector, "_run_git", return_value="false"),
        pytest.raises(GitError, match="Path is not inside a git work tree"),
    ):
        collector._validate_git_repo()


def test_git_resolve_commit_failure(git_repo: Path) -> None:
    collector = GitContextCollector(str(git_repo))
    with (
        patch.object(collector, "_run_git", side_effect=GitError("Bad revision")),
        pytest.raises(GitError, match="Could not resolve git reference"),
    ):
        collector.resolve_commit("invalid-ref")


def test_git_get_diff_fallback_and_failure(git_repo: Path) -> None:
    collector = GitContextCollector(str(git_repo))
    with (
        patch.object(collector, "_run_git", side_effect=GitError("Diff error")),
        pytest.raises(GitError, match="Failed to diff"),
    ):
        collector.get_diff("main", "feature")


def test_git_parse_diff_added_deleted_renamed(git_repo: Path) -> None:
    collector = GitContextCollector(str(git_repo))
    diff = """diff --git a/new_file.py b/new_file.py
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/new_file.py
@@ -0,0 +1,2 @@
+def foo():
+    pass
diff --git a/deleted_file.py b/deleted_file.py
deleted file mode 100644
index 1111111..0000000
--- a/deleted_file.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def bar():
-    pass
diff --git a/old_name.py b/new_name.py
similarity index 95%
rename from old_name.py
rename to new_name.py
index 1111111..2222222 100644
--- a/old_name.py
+++ b/new_name.py
@@ -1,2 +1,2 @@
-def baz():
+def baz_renamed():
     pass
"""
    parsed = collector.parse_diff(diff)
    assert len(parsed) == 3
    assert parsed["new_file.py"].status == "added"
    assert parsed["deleted_file.py"].status == "deleted"
    assert parsed["new_name.py"].status == "renamed"
    assert parsed["new_name.py"].old_path == "old_name.py"


def test_git_read_file_at_ref_fallback(git_repo: Path) -> None:
    collector = GitContextCollector(str(git_repo))
    local_file = git_repo / "local.py"
    local_file.write_text("content on disk", encoding="utf-8")

    # When ref is a commit and git show fails, must NOT fall back to disk
    with patch.object(collector, "_run_git", side_effect=GitError("show failed")):
        content = collector.read_file_at_ref("local.py", "HEAD")
        assert content is None

    # Only when ref is WORKTREE does it read from disk
    content_worktree = collector.read_file_at_ref("local.py", "WORKTREE")
    assert content_worktree == "content on disk"


def test_git_read_file_at_ref_local_oserror(git_repo: Path) -> None:
    collector = GitContextCollector(str(git_repo))
    local_file = git_repo / "unreadable.py"
    local_file.write_text("data", encoding="utf-8")

    with patch("builtins.open", side_effect=OSError("Read permission denied")):
        assert collector.read_file_at_ref("unreadable.py", "WORKTREE") is None


def test_git_find_related_context_standard_import(git_repo: Path) -> None:
    collector = GitContextCollector(str(git_repo))
    cfile = ChangedFile(
        path="src/main.py",
        status="modified",
        content="import utils\nfrom auth_helper import check_auth\n",
    )
    with patch.object(collector, "read_file_at_ref") as mock_read:
        mock_read.side_effect = lambda p, ref: "helper code" if "utils" in p else None
        related = collector.find_related_context({"src/main.py": cfile}, "HEAD")
        assert any("utils" in k for k in related)


def test_git_collect_budget_limits(git_repo: Path) -> None:
    collector = GitContextCollector(str(git_repo))
    with (
        patch.object(collector, "resolve_commit", return_value="abcdef12"),
        patch.object(collector, "get_diff", return_value="line1\nline2\nline3\nline4"),
        patch.object(
            collector,
            "parse_diff",
            return_value={
                "file1.py": ChangedFile(path="file1.py", status="modified"),
                "file2.py": ChangedFile(path="file2.py", status="deleted"),
                "file3.py": ChangedFile(path="file3.py", status="modified"),
            },
        ),
        patch.object(collector, "read_file_at_ref", return_value="print('large content')"),
    ):
        ctx = collector.collect(
            base_revision="main",
            head_revision="HEAD",
            max_files=1,  # Truncates files
            max_diff_lines=2,  # Truncates diff
            max_context_bytes=5,  # Truncates file content
        )
        assert len(ctx.changed_files) == 1
        assert any("Diff truncated" in s for s in ctx.omitted_scope)
        assert any("Files truncated" in s for s in ctx.omitted_scope)
        assert any("omitted due to context size limits" in s for s in ctx.omitted_scope)


# ==============================================================================
# 2. Counterexample Checker Edge Cases
# ==============================================================================


def test_counterexample_zero_division_guard() -> None:
    checker = CounterexampleChecker()
    finding = ReviewFinding(
        id="f-01",
        file_path="src/math_ops.py",
        line=4,
        label=ConventionalLabel.ISSUE,
        subject="Zero division vulnerability in denominator",
        description="Division by zero if denominator is 0",
        severity=FindingSeverity.HIGH,
        blocking=True,
    )
    context = GitReviewContext(
        repo_path=str(Path(".")),
        base_revision="main",
        head_revision="HEAD",
        base_commit="a" * 40,
        head_commit="b" * 40,
        raw_diff="diff ...",
        changed_files={
            "src/math_ops.py": ChangedFile(
                path="src/math_ops.py",
                status="modified",
                content="def divide(a, b):\n    if b == 0:\n        return 0\n    return a / b\n",
            )
        },
        related_files={},
    )
    validated, notes = checker.check_compensating_controls([finding], context)
    assert len(validated) == 0
    assert len(notes) == 1
    assert "protecting division in scope" in notes[0]


def test_counterexample_token_expiration_dismissal() -> None:
    checker = CounterexampleChecker()
    finding = ReviewFinding(
        id="f-01",
        file_path="src/auth.py",
        line=2,
        label=ConventionalLabel.ISSUE,
        subject="Missing token expiration check",
        description="The handler omits token expiry verification",
        severity=FindingSeverity.HIGH,
        blocking=True,
    )
    context = GitReviewContext(
        repo_path=str(Path(".")),
        base_revision="main",
        head_revision="HEAD",
        base_commit="a" * 40,
        head_commit="b" * 40,
        raw_diff="diff ...",
        changed_files={
            "src/auth.py": ChangedFile(
                path="src/auth.py",
                status="modified",
                content="def login(token):\n    check_token_expiration(token)\n",
            )
        },
        related_files={
            "src/auth_helper.py": (
                "def check_token_expiration(token):\n    if token.is_expired(): raise ValueError()"
            ),
        },
    )
    validated, notes = checker.check_compensating_controls([finding], context)
    assert len(validated) == 0
    assert len(notes) == 1
    assert "potential compensating check" in notes[0]


# ==============================================================================
# 3. Review Engine Edge Cases
# ==============================================================================


def test_review_engine_adapters_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = ReviewEngine()
    assert isinstance(engine._get_adapter("deterministic"), DeterministicReviewerAdapter)
    assert isinstance(engine._get_adapter("fake"), DeterministicReviewerAdapter)

    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key")
    assert isinstance(engine._get_adapter("gemini"), GeminiReviewerAdapter)

    with pytest.raises(ProviderConfigurationError, match="Unsupported provider"):
        engine._get_adapter("unsupported_provider_xyz")


def test_review_engine_unassessed_requirement_fallback(git_repo: Path) -> None:
    fake_adapter = MagicMock()
    fake_adapter.model_name = "fake-model"
    # Model returns findings citing REQ-01, but does NOT return explicit assessments
    fake_adapter.review.return_value = ProviderResponse(
        summary="Review completed.",
        candidate_findings=[
            RawCandidateFinding(
                file_path="src/calc.py",
                line=2,
                label="issue",
                subject="Missing check",
                description="Failed requirement",
                requirement_ids=["REQ-01"],
            )
        ],
        requirement_assessments=[],  # Empty!
    )
    engine = ReviewEngine(provider_adapter=fake_adapter)

    f = git_repo / "src" / "calc.py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("def add(a, b): return a + b\n")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "add calc"], cwd=git_repo, check=True)

    # Make change
    f.write_text("def add(a, b): return a + b + 1\n")
    subprocess.run(["git", "commit", "-am", "change"], cwd=git_repo, check=True)

    req_file = git_repo / "reqs.md"
    req_file.write_text("# Requirements\n\n[REQ-01] Add must return a + b.\n[REQ-02] Subtract.\n")

    report = engine.review(
        ReviewRequest(
            repo_path=str(git_repo),
            base_revision="HEAD~1",
            head_revision="HEAD",
            requirements=[str(req_file)],
            provider="fake",
        )
    )

    req_map = {a.requirement_id: a for a in report.requirement_assessments}
    assert req_map["REQ-01"].status == RequirementStatus.NOT_SATISFIED
    assert req_map["REQ-02"].status == RequirementStatus.NOT_ASSESSED
    assert "not assessed" in req_map["REQ-02"].explanation.lower()


def test_review_engine_invalid_model_requirement_status(git_repo: Path) -> None:
    fake_adapter = MagicMock()
    fake_adapter.model_name = "fake-model"
    fake_adapter.review.return_value = ProviderResponse(
        summary="Review.",
        candidate_findings=[],
        requirement_assessments=[
            RawRequirementAssessment(
                requirement_id="REQ-01",
                status="completely_unknown_status_xyz",
                explanation="Weird model response",
            )
        ],
    )
    engine = ReviewEngine(provider_adapter=fake_adapter)

    req_file = git_repo / "reqs.md"
    req_file.write_text("[REQ-01] x must be 1.\n")

    report = engine.review(
        ReviewRequest(
            repo_path=str(git_repo),
            base_revision="HEAD",
            head_revision="HEAD",
            requirements=[str(req_file)],
            provider="fake",
        )
    )
    assert report.requirement_assessments[0].status == RequirementStatus.UNCLEAR


# ==============================================================================
# 4. CLI Subcommand & Error Handling
# ==============================================================================


def test_cli_review_json_format(git_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "review",
                str(git_repo),
                "--base",
                "HEAD",
                "--head",
                "HEAD",
                "--provider",
                "deterministic",
                "--format",
                "json",
            ]
        )
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert '"schema_version": "verifycraft.review.v0"' in captured.out


def test_cli_review_provider_error(git_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "review",
                str(git_repo),
                "--base",
                "HEAD",
                "--head",
                "HEAD",
                "--provider",
                "invalid_provider",
            ]
        )
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "VerifyCraft Provider Error:" in captured.err


def test_cli_review_generic_error(git_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(ReviewEngine, "review", side_effect=RuntimeError("Unexpected crash")):
        with pytest.raises(SystemExit) as exc:
            main(
                [
                    "review",
                    str(git_repo),
                    "--base",
                    "HEAD",
                    "--head",
                    "HEAD",
                    "--provider",
                    "deterministic",
                ]
            )
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "VerifyCraft Review Error: Unexpected crash" in captured.err


def test_cli_review_strict_blocking_exit(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_report = ReviewReport(
        repo_path=str(git_repo),
        base_revision="HEAD",
        head_revision="HEAD",
        summary="Found blocking issue",
        findings=[
            ReviewFinding(
                id="f1",
                file_path="code.py",
                line=1,
                label=ConventionalLabel.ISSUE,
                subject="Critical flaw",
                description="Must be fixed",
                severity=FindingSeverity.CRITICAL,
                blocking=True,
            )
        ],
    )
    with patch.object(ReviewEngine, "review", return_value=mock_report):
        with pytest.raises(SystemExit) as exc:
            main(
                [
                    "review",
                    str(git_repo),
                    "--base",
                    "HEAD",
                    "--head",
                    "HEAD",
                    "--provider",
                    "deterministic",
                    "--strict",
                ]
            )
        assert exc.value.code == 2


# ==============================================================================
# 5. Contract Rendering Edge Cases
# ==============================================================================


def test_finding_decorations_and_ranges() -> None:
    # Finding with line and end_line range
    f = ReviewFinding(
        id="f-01",
        file_path="src/calc.py",
        line=10,
        end_line=20,
        label=ConventionalLabel.SUGGESTION,
        subject="Refactor loop",
        description="Use comprehension instead",
        severity=FindingSeverity.LOW,
        blocking=False,
        requirement_ids=["REQ-01", "REQ-02"],
        suggested_action="Use list comp",
        evidence_status=EvidenceStatus.MODEL_INFERRED,
    )
    formatted = f.format_conventional_comment()
    assert "suggestion: Refactor loop" in formatted
    assert "- **Requirement:** REQ-01, REQ-02" in formatted
    assert "- **Suggested Action:** Use list comp" in formatted
    assert "- **Grounding:** model_inferred" in formatted

    # Blocking with no decorations explicitly sets (blocking)
    f_blocking = ReviewFinding(
        id="f-02",
        file_path="src/calc.py",
        line=5,
        label=ConventionalLabel.ISSUE,
        subject="Bug",
        description="Fix me",
        severity=FindingSeverity.HIGH,
        blocking=True,
        decorations=[],
    )
    assert "issue (blocking): Bug" in f_blocking.format_conventional_comment()

    # Report conventional comments format with range
    rep = ReviewReport(
        repo_path=str(Path(".")),
        base_revision="main",
        head_revision="HEAD",
        summary="Summary",
        findings=[f],
        omitted_scope=["Omitted item 1"],
    )
    comments = rep.to_conventional_comments()
    assert "### src/calc.py:10-20" in comments

    # Markdown without requirements renders omitted scope
    md = rep.to_markdown()
    assert "## Omitted Scope" in md
    assert "- Omitted item 1" in md


# ==============================================================================
# 6. Requirements Parser Edge Cases
# ==============================================================================


def test_requirements_parser_edge_cases(tmp_path: Path) -> None:
    parser = RequirementsParser()

    # ID without REQ- prefix gets normalized
    reqs = parser.parse_text("[AUTH-01] User must authenticate.")
    assert len(reqs) == 1
    assert reqs[0].id == "REQ-AUTH-01"

    # Reading file that triggers OSError
    f = tmp_path / "bad.md"
    f.write_text("[REQ-01] Sample requirement.")
    with (
        patch("builtins.open", side_effect=OSError("Read error")),
        pytest.raises(OSError, match="Could not read requirements file"),
    ):
        parser.parse_file(str(f))

    # Path outside repo_root falls back to rel_source = file_path
    with patch("pathlib.Path.relative_to", side_effect=ValueError("Outside")):
        parsed = parser.parse_file(str(f), repo_root=str(tmp_path))
        assert parsed[0].source_file == str(f)

    # parse_all with duplicate requirements deduplicates
    all_reqs = parser.parse_all(
        ["[REQ-01] First requirement.", "[REQ-01] Duplicate requirement."],
        repo_root=str(tmp_path),
    )
    assert len(all_reqs) == 1
    assert all_reqs[0].id == "REQ-01"


def test_review_request_empty_repo_path() -> None:
    with pytest.raises(ValueError, match="repo_path must not be empty"):
        ReviewRequest(repo_path="   ")


def test_git_parse_diff_multiple_hunks(git_repo: Path) -> None:
    collector = GitContextCollector(str(git_repo))
    diff = """diff --git a/multi.py b/multi.py
--- a/multi.py
+++ b/multi.py
@@ -1,3 +1,3 @@
 line1
-line2
+line2_mod
 line3
@@ -10,3 +10,3 @@
 line10
-line11
+line11_mod
 line12
"""
    parsed = collector.parse_diff(diff)
    assert len(parsed["multi.py"].hunks) == 2


def test_git_parse_diff_invalid_chunk_header(git_repo: Path) -> None:
    collector = GitContextCollector(str(git_repo))
    diff = "diff --git invalid format\nSome random content\n"
    parsed = collector.parse_diff(diff)
    assert len(parsed) == 0


def test_review_engine_finding_fallback_line_note(git_repo: Path) -> None:
    fake_adapter = MagicMock()
    fake_adapter.model_name = "fake-model"
    fake_adapter.review.return_value = ProviderResponse(
        summary="Review.",
        candidate_findings=[
            RawCandidateFinding(
                file_path="src/calc.py",
                line=999,
                label="issue",
                subject="Bad line",
                description="Line is invalid",
            )
        ],
        requirement_assessments=[],
    )
    engine = ReviewEngine(provider_adapter=fake_adapter)
    f = git_repo / "src" / "calc.py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("def add(): pass\n")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "calc"], cwd=git_repo, check=True)
    f.write_text("def add(): return 1\n")
    subprocess.run(["git", "commit", "-am", "mod"], cwd=git_repo, check=True)

    report = engine.review(
        ReviewRequest(
            repo_path=str(git_repo),
            base_revision="HEAD~1",
            head_revision="HEAD",
            provider="fake",
        )
    )
    assert len(report.findings) == 1
    assert report.findings[0].line is None
    assert "Line 999 is outside changed hunks" in report.findings[0].description


def test_review_engine_requirement_satisfied_via_compensating_control(git_repo: Path) -> None:
    fake_adapter = MagicMock()
    fake_adapter.model_name = "fake-model"
    fake_adapter.review.return_value = ProviderResponse(
        summary="Review.",
        candidate_findings=[
            RawCandidateFinding(
                file_path="src/auth.py",
                line=2,
                label="issue",
                subject="Missing token expiration",
                description="Token expiry missing",
                requirement_ids=["REQ-EXP-01"],
            )
        ],
        requirement_assessments=[],
    )
    engine = ReviewEngine(provider_adapter=fake_adapter)
    f = git_repo / "src" / "auth.py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("def login(): pass\n")
    h = git_repo / "src" / "auth_helper.py"
    h.write_text(
        "def check_token_expiration(token):\n    if token.is_expired(): raise ValueError()\n"
    )
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init auth"], cwd=git_repo, check=True)

    f.write_text(
        "from auth_helper import check_token_expiration\n"
        "def login(token):\n"
        "    check_token_expiration(token)\n"
    )
    subprocess.run(["git", "commit", "-am", "import and call helper"], cwd=git_repo, check=True)

    req_file = git_repo / "reqs.md"
    req_file.write_text("[REQ-EXP-01] Token must expire.\n")

    report = engine.review(
        ReviewRequest(
            repo_path=str(git_repo),
            base_revision="HEAD~1",
            head_revision="HEAD",
            requirements=[str(req_file)],
            provider="fake",
        )
    )
    assert report.requirement_assessments[0].requirement_id == "REQ-EXP-01"
    assert report.requirement_assessments[0].status == RequirementStatus.APPEARS_SATISFIED


# ==============================================================================
# 9. Additional Coverage for Review Engine, Git Context & Anchoring
# ==============================================================================


def test_dotenv_oserror_handling(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from verifycraft.review.providers.base import load_dotenv_if_present

    env_file = tmp_path / ".env"
    env_file.write_text("SOME_KEY=val\n")
    with patch.object(Path, "read_text", side_effect=OSError("Read error")):
        loaded = load_dotenv_if_present(env_path=env_file, opt_in=True)
        assert loaded is False


def test_deduplicate_findings_distance_and_unanchored_coverage() -> None:
    engine = ReviewEngine()

    # Case A: Different files -> not clustered
    f1 = ReviewFinding(
        id="f1",
        file_path="src/a.py",
        line=10,
        label=ConventionalLabel.ISSUE,
        subject="Check `LOOPBACK` configuration",
        description="Invalid config",
        severity=FindingSeverity.HIGH,
        evidence_status=EvidenceStatus.MODEL_INFERRED,
    )
    f2 = ReviewFinding(
        id="f2",
        file_path="src/b.py",
        line=10,
        label=ConventionalLabel.ISSUE,
        subject="Check `LOOPBACK` configuration",
        description="Invalid config",
        severity=FindingSeverity.HIGH,
        evidence_status=EvidenceStatus.MODEL_INFERRED,
    )
    res = engine._deduplicate_findings([f1, f2])
    assert len(res) == 2

    # Case B: Distance between 11 and 30 with shared symbol -> clustered
    f3 = ReviewFinding(
        id="f3",
        file_path="src/a.py",
        line=30,
        label=ConventionalLabel.QUESTION,
        subject="Should `LOOPBACK` be used here?",
        description="Question about loopback",
        severity=FindingSeverity.MEDIUM,
        evidence_status=EvidenceStatus.MODEL_INFERRED,
    )
    res_b = engine._deduplicate_findings([f1, f3])
    assert len(res_b) == 1

    # Case C: One anchored, one unanchored with shared symbol -> clustered
    f4 = ReviewFinding(
        id="f4",
        file_path="src/a.py",
        line=None,
        label=ConventionalLabel.SUGGESTION,
        subject="Refactor `LOOPBACK` logic",
        description="Suggestion for loopback",
        severity=FindingSeverity.LOW,
        evidence_status=EvidenceStatus.MODEL_INFERRED,
    )
    res_c = engine._deduplicate_findings([f1, f4])
    assert len(res_c) == 1


def test_semantic_anchor_def_and_context_slice() -> None:
    from verifycraft.review.git import DiffHunk

    collector = GitContextCollector(repo_path=".")
    content = (
        "import os\n"
        "# comment\n"
        "def helper_func():\n"
        "    return 1\n"
        "def main():\n"
        "    return helper_func()\n"
        "val = 42\n"
    )
    cfile = ChangedFile(
        path="src/example.py",
        status="modified",
        changed_lines={3, 4},
        hunks=[DiffHunk(old_start=1, old_lines=7, new_start=1, new_lines=7, content="...")],
        content=content,
    )
    changed = {"src/example.py": cfile}

    # Def match in file
    line, reason = collector.validate_semantic_anchor(
        file_path="src/example.py",
        line=100,
        subject="Issue in `helper_func`",
        description="Details about `helper_func`",
        changed_files=changed,
    )
    assert line == 3

    # Slice match: line 4 is within 2 lines of helper_func (line 3)
    # Valid anchor line 4 is retained!
    line_slice, reason_slice = collector.validate_semantic_anchor(
        file_path="src/example.py",
        line=4,
        subject="Issue in `helper_func`",
        description="Details about `helper_func`",
        changed_files=changed,
    )
    assert line_slice == 4


def test_bounded_diff_acquisition_coverage_branches() -> None:
    collector = GitContextCollector(repo_path=".")
    omitted: list[str] = []

    # Diff with binary file (no hunks) and many files to trigger summary truncation (> 5 files)
    parts: list[str] = []
    # 1. Binary file with no @@ hunks
    parts.append("diff --git a/bin.dat b/bin.dat\nBinary files differ\n")
    # 2. 7 files with hunks to trigger > 5 partially reviewed and unreviewed
    for i in range(1, 9):
        parts.append(
            f"diff --git a/file{i}.py b/file{i}.py\n"
            f"--- a/file{i}.py\n"
            f"+++ b/file{i}.py\n"
            f"@@ -1,5 +1,5 @@\n"
            f"+ line {i} a\n"
            f"+ line {i} b\n"
            f"@@ -10,5 +10,5 @@\n"
            f"+ line {i} c\n"
            f"+ line {i} d\n"
        )
    diff_text = "".join(parts)
    res = collector._bounded_diff_acquisition(diff_text, max_diff_lines=25, omitted_scope=omitted)
    assert res is not None
    assert any("Diff exceeded budget" in note for note in omitted)
    assert any("more" in note for note in omitted)


def test_populate_file_contents_windowing_and_overflow() -> None:
    from verifycraft.review.git import DiffHunk

    collector = GitContextCollector(repo_path=".")
    # Make big_content exceed per_file_bytes (e.g. 500 lines = ~15,000 bytes)
    big_content = "\n".join(
        f"line {i} with lots of padding content for size" for i in range(1, 500)
    )

    cfile = ChangedFile(
        path="src/big.py",
        status="modified",
        changed_lines={50},
        hunks=[DiffHunk(old_start=45, old_lines=10, new_start=45, new_lines=10, content="...")],
    )
    omitted: list[str] = []
    with patch.object(collector, "read_file_at_ref", return_value=big_content):
        # Windowing triggered because big_content exceeds per_file_bytes (per_file_bytes=2048)
        total = collector._populate_file_contents(
            changed_files={"src/big.py": cfile},
            head_revision="HEAD",
            max_context_bytes=6000,
            omitted_scope=omitted,
        )
        assert total > 0
        assert any("bounded to changed hunk windows" in note for note in omitted)

        # Tight window fallback when budget is tighter
        omitted_tight: list[str] = []
        cfile_tight = ChangedFile(
            path="src/big.py",
            status="modified",
            changed_lines={50},
            hunks=[DiffHunk(old_start=45, old_lines=10, new_start=45, new_lines=10, content="...")],
        )
        total_tight = collector._populate_file_contents(
            changed_files={"src/big.py": cfile_tight},
            head_revision="HEAD",
            max_context_bytes=2048,
            omitted_scope=omitted_tight,
        )
        assert total_tight > 0
        assert any("bounded to tight hunk windows" in note for note in omitted_tight)

        # Context limit overflow
        omitted_overflow: list[str] = []
        cfile_overflow = ChangedFile(path="src/big.py", status="modified", changed_lines={50})
        total_zero = collector._populate_file_contents(
            changed_files={"src/big.py": cfile_overflow},
            head_revision="HEAD",
            max_context_bytes=10,
            omitted_scope=omitted_overflow,
        )
        assert total_zero == 0
        assert any("omitted due to context size limits" in note for note in omitted_overflow)


def test_cli_load_dotenv_invocation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / "custom.env"
    env_file.write_text("CUSTOM_TEST_VAR=hello\n")
    # Test opt-in loading via --env-file
    with pytest.raises(SystemExit) as exc:
        main(["--env-file", str(env_file)])
    assert exc.value.code == 0
    import os

    assert os.environ.get("CUSTOM_TEST_VAR") == "hello"
