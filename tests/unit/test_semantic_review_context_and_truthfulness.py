"""Unit tests verifying semantic review context enrichment and truthfulness hardening.

PR #6 Requirements:
1. Surrounding code outside diff hunks is included in prompt with line numbers.
2. Regressions in unchanged surrounding code are visible and reviewable.
3. Working tree content is never attributed to a committed revision.
4. Context omissions are rendered prominently in prompt.
5. CounterexampleChecker preserves uncertain findings (questions, suggestions, thoughts, notes).
6. Line number bounds are validated (out-of-bounds line citations are rejected).
"""

from __future__ import annotations

from verifycraft.contracts.review import (
    ConventionalLabel,
    FindingSeverity,
    RequirementStatus,
    ReviewFinding,
)
from verifycraft.review.counterexamples import CounterexampleChecker
from verifycraft.review.engine import ReviewEngine
from verifycraft.review.git import ChangedFile, DiffHunk, GitReviewContext
from verifycraft.review.providers.base import RawRequirementAssessment


def test_build_prompt_includes_surrounding_code_with_line_numbers() -> None:
    """Verifies that changed files include surrounding methods and 1-indexed lines."""
    engine = ReviewEngine()
    content = (
        "class Calculator:\n"
        "    def __init__(self, factor=1):\n"
        "        self.factor = factor\n"
        "\n"
        "    def calculate(self, x):\n"
        "        return x * self.factor\n"
        "\n"
        "    def unchanged_surrounding_method(self):\n"
        "        # This method is outside the diff hunk but critical to review\n"
        "        return self.factor * 100\n"
    )
    cfile = ChangedFile(
        path="src/calc.py",
        status="modified",
        content=content,
        hunks=[
            DiffHunk(
                old_start=2,
                old_lines=1,
                new_start=2,
                new_lines=1,
                content="@@ -2,1 +2,1 @@",
            )
        ],
        changed_lines={2},
    )
    context = GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        base_commit="1111111111111111111111111111111111111111",
        head_commit="2222222222222222222222222222222222222222",
        raw_diff="diff --git a/src/calc.py b/src/calc.py\n@@ -2,1 +2,1 @@",
        changed_files={"src/calc.py": cfile},
        related_files={},
        omitted_scope=[],
    )

    prompt = engine._build_prompt(context, [])

    assert "## Changed Files (Surrounding Implementation Context)" in prompt
    assert "### Changed File: `src/calc.py`" in prompt
    assert "8:     def unchanged_surrounding_method(self):" in prompt
    assert "Repository Head: HEAD (22222222)" in prompt


def test_build_prompt_never_attributes_worktree_to_commit_sha() -> None:
    """Verifies that uncommitted working-tree reviews are explicitly labeled as WORKTREE."""
    engine = ReviewEngine()
    context = GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision="WORKTREE",
        base_commit="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        head_commit="WORKTREE (uncommitted)",
        raw_diff="diff --git a/app.py b/app.py",
        changed_files={},
        related_files={},
        is_working_tree=True,
    )

    prompt = engine._build_prompt(context, [])

    expected_head = (
        "Repository Head: WORKTREE (Uncommitted working-tree modifications relative to base main "
        "[aaaaaaaa])"
    )
    assert expected_head in prompt
    assert "Repository Head: WORKTREE (WORKTREE" not in prompt


def test_build_prompt_prominently_renders_omissions_notice() -> None:
    """Verifies that context omissions and truncation markers are prominently displayed."""
    engine = ReviewEngine()
    context = GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        base_commit="a" * 40,
        head_commit="b" * 40,
        raw_diff="diff --git a/app.py b/app.py",
        changed_files={},
        related_files={},
        omitted_scope=[
            "Diff truncated: 3000 lines exceeded limit of 2500 lines",
            "WORKTREE mode limitation: 3 untracked file(s) omitted from review (foo.py).",
        ],
    )

    prompt = engine._build_prompt(context, [])

    assert "## Context Scope and Omissions Notice" in prompt
    assert "- Diff truncated: 3000 lines exceeded limit of 2500 lines" in prompt
    assert "- WORKTREE mode limitation: 3 untracked file(s) omitted" in prompt


def test_has_valid_source_location_rejects_out_of_bounds_lines() -> None:
    """Verifies that line numbers exceeding total lines in a file are rejected as invalid."""
    engine = ReviewEngine()
    cfile = ChangedFile(
        path="src/service.py",
        status="modified",
        content="line1\nline2\nline3\n",
    )
    context = GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        base_commit="a" * 40,
        head_commit="b" * 40,
        raw_diff="",
        changed_files={"src/service.py": cfile},
        related_files={},
    )

    # Valid line in range [1, 3]
    assert engine._has_valid_source_location(["src/service.py:2"], context) is True
    # Valid file-level
    assert engine._has_valid_source_location(["src/service.py"], context) is True
    # Out of bounds line 9999 in 3-line file
    assert engine._has_valid_source_location(["src/service.py:9999"], context) is False
    # Line 0 (0-indexed error)
    assert engine._has_valid_source_location(["src/service.py:0"], context) is False


def test_counterexample_checker_preserves_uncertain_findings() -> None:
    """Verifies that CounterexampleChecker preserves uncertain questions, suggestions, or notes."""
    checker = CounterexampleChecker()
    valid_content = (
        "def login(token):\n    if token.is_expired():\n        return False\n    return True\n"
    )
    context = GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        base_commit="a" * 40,
        head_commit="b" * 40,
        raw_diff="",
        changed_files={
            "src/auth.py": ChangedFile(
                path="src/auth.py",
                status="modified",
                content=valid_content,
            )
        },
        related_files={},
    )

    # 1. Defect claim (issue): challenged and suppressed by compensating control
    defect = ReviewFinding(
        id="f-01",
        file_path="src/auth.py",
        line=4,
        label=ConventionalLabel.ISSUE,
        subject="Missing token expiration check",
        description="Omits token expiration check",
        severity=FindingSeverity.HIGH,
        blocking=True,
    )
    # 2. Uncertain exploratory question: MUST NOT BE SUPPRESSED!
    question = ReviewFinding(
        id="q-01",
        file_path="src/auth.py",
        line=4,
        label=ConventionalLabel.QUESTION,
        subject="Does expiration handling handle clock skew?",
        description="Could unhandled clock skew cause expired tokens to pass?",
        severity=FindingSeverity.INFO,
        blocking=False,
    )
    # 3. Non-blocking suggestion: MUST NOT BE SUPPRESSED!
    suggestion = ReviewFinding(
        id="s-01",
        file_path="src/auth.py",
        line=2,
        label=ConventionalLabel.SUGGESTION,
        subject="Consider adding expiration metrics",
        description="Suggested tracking expired token rejections",
        severity=FindingSeverity.LOW,
        blocking=False,
    )

    validated, notes = checker.check_compensating_controls([defect, question, suggestion], context)

    # Defect was challenged
    assert defect not in validated
    assert len(notes) == 1
    assert "was challenged" in notes[0]
    assert "[Grounding: Statically detected compensating control]" in notes[0]
    assert "static detection is not runtime proof of execution" in notes[0]

    # Question and suggestion must remain in validated findings
    assert question in validated
    assert suggestion in validated
    assert len(validated) == 2


def test_provider_appears_satisfied_grounding_tag() -> None:
    """Verifies that provider appears_satisfied explanations are tagged with model inference."""
    engine = ReviewEngine()
    cfile = ChangedFile(
        path="src/auth.py",
        status="modified",
        content="def login(token):\n    token.validate()\n    return True\n",
    )
    context = GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        base_commit="a" * 40,
        head_commit="b" * 40,
        raw_diff="diff --git a/src/auth.py b/src/auth.py\n+    token.validate()",
        changed_files={"src/auth.py": cfile},
        related_files={},
    )
    raw_a = RawRequirementAssessment(
        requirement_id="REQ-01",
        status="appears_satisfied",
        explanation="Token validation is called at line 2.",
        related_locations=["src/auth.py:2"],
    )

    status, explanation = engine._assess_from_provider(
        raw_a=raw_a,
        req_id="REQ-01",
        is_relevant=True,
        context=context,
    )

    assert status == RequirementStatus.APPEARS_SATISFIED
    assert explanation.startswith(
        "[Grounding: Model semantic inference | Citations: src/auth.py:2]"
    )

    # When explanation already starts with [Grounding:, prefix is preserved without duplication
    raw_a_grounded = RawRequirementAssessment(
        requirement_id="REQ-01",
        status="appears_satisfied",
        explanation="[Grounding: Pre-grounded by pipeline] Verified.",
        related_locations=["src/auth.py:2"],
    )
    _, explanation_grounded = engine._assess_from_provider(
        raw_a=raw_a_grounded,
        req_id="REQ-01",
        is_relevant=True,
        context=context,
    )
    assert explanation_grounded == "[Grounding: Pre-grounded by pipeline] Verified."


def test_format_changed_file_context_empty_and_large_file_truncation() -> None:
    """Verifies that empty files and large files with multiple hunks are formatted properly."""
    engine = ReviewEngine()

    # Empty file
    empty_cfile = ChangedFile(
        path="src/empty.py",
        status="added",
        content="",
    )
    assert engine._format_changed_file_context(empty_cfile) == "(Empty file)"

    # Large file (>250 lines) with hunks and omissions
    lines = [f"def line_{i}(): pass" for i in range(1, 401)]
    large_content = "\n".join(lines)
    hunk1 = DiffHunk(old_start=100, old_lines=5, new_start=100, new_lines=5, content="")
    hunk2 = DiffHunk(old_start=250, old_lines=5, new_start=250, new_lines=5, content="")
    large_cfile = ChangedFile(
        path="src/large.py",
        status="modified",
        content=large_content,
        hunks=[hunk1, hunk2],
    )

    formatted = engine._format_changed_file_context(large_cfile)
    assert "1: def line_1(): pass" in formatted
    assert "30: def line_30(): pass" in formatted
    assert "surrounding lines omitted for brevity" in formatted
    assert "100: def line_100(): pass" in formatted
    assert "250: def line_250(): pass" in formatted
    assert "trailing lines omitted for brevity" in formatted


def test_build_prompt_with_deleted_and_related_files() -> None:
    """Verifies that deleted files and related files sections are correctly rendered."""
    engine = ReviewEngine()
    deleted_cfile = ChangedFile(
        path="src/deprecated.py",
        status="deleted",
        content=None,
    )
    context = GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        base_commit="a" * 40,
        head_commit="b" * 40,
        raw_diff="diff --git a/src/deprecated.py b/src/deprecated.py",
        changed_files={"src/deprecated.py": deleted_cfile},
        related_files={"src/helper.py": "def do_helper(): return 42\n"},
    )

    prompt = engine._build_prompt(context, [])
    assert "### Changed File: `src/deprecated.py` *(deleted)*" in prompt
    assert "## Relevant Surrounding Code & Related Helpers" in prompt
    assert "### Context File: `src/helper.py`" in prompt
    assert "def do_helper(): return 42" in prompt


def test_git_context_collector_parse_line_imports() -> None:
    """Verifies _parse_line_imports handles relative, dotted, and standard module imports."""
    from verifycraft.review.git import GitContextCollector

    # Relative import without subpath
    mods1 = GitContextCollector._parse_line_imports("from . import utils", parent_dir="src/pkg")
    assert "src/pkg" in mods1

    # Relative import with subpath
    mods2 = GitContextCollector._parse_line_imports("from .sub import utils", parent_dir="src/pkg")
    assert "src/pkg/sub" in mods2
    assert "sub" in mods2

    # Relative import with parent_dir = "."
    mods3 = GitContextCollector._parse_line_imports("from .sub import utils", parent_dir=".")
    assert "sub" in mods3
    assert "./sub" not in mods3

    mods3b = GitContextCollector._parse_line_imports("from . import utils", parent_dir=".")
    assert mods3b == set()

    # From dotted import
    mods4 = GitContextCollector._parse_line_imports("from foo.bar import baz", parent_dir="src")
    assert "foo.bar" in mods4
    assert "foo" in mods4
    assert "foo/bar" in mods4

    # Import statement
    mods5 = GitContextCollector._parse_line_imports("import mymod.core", parent_dir="src")
    assert "mymod.core" in mods5
    assert "mymod" in mods5
    assert "mymod/core" in mods5

    # Empty or non-import line
    assert GitContextCollector._parse_line_imports("   ", parent_dir="src") == set()
    assert GitContextCollector._parse_line_imports("x = 42", parent_dir="src") == set()


def test_git_context_collector_build_candidate_paths() -> None:
    """Verifies _build_candidate_paths generates sibling helpers, types, and test paths."""
    from verifycraft.review.git import GitContextCollector

    collector = GitContextCollector(".")
    candidates = collector._build_candidate_paths(
        imported_modules={"mymod"},
        changed_paths=["src/api/views.py"],
    )

    assert "mymod.py" in candidates
    assert "src/mymod.py" in candidates
    assert "src/api/helpers.py" in candidates
    assert "src/api/types.py" in candidates
    assert "tests/test_views.py" in candidates
    assert "tests/src/api/test_views.py" in candidates


def test_is_valid_file_and_line_related_files_and_suffixes() -> None:
    """Verifies _is_valid_file_and_line checks related files bounds and suffix matching."""
    engine = ReviewEngine()
    context = GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        base_commit="a" * 40,
        head_commit="b" * 40,
        raw_diff="",
        changed_files={
            "src/pkg/service.py": ChangedFile(
                path="src/pkg/service.py",
                status="modified",
                content="line 1\nline 2\nline 3\n",
            )
        },
        related_files={"src/shared/helpers.py": "def helper1(): pass\ndef helper2(): pass\n"},
    )

    # Valid line in related files
    assert engine._is_valid_file_and_line("src/shared/helpers.py", 2, context) is True
    # Line out of bounds in related files
    assert engine._is_valid_file_and_line("src/shared/helpers.py", 50, context) is False
    # None line in related files
    assert engine._is_valid_file_and_line("src/shared/helpers.py", None, context) is True

    # Suffix matching for changed file
    assert engine._is_valid_file_and_line("service.py", 2, context) is True
    assert engine._is_valid_file_and_line("service.py", 99, context) is False
    assert engine._is_valid_file_and_line("service.py", None, context) is True

    # Invalid non-integer line string in _has_valid_source_location
    assert engine._has_valid_source_location(["src/shared/helpers.py:not_a_num"], context) is True
    assert engine._has_valid_source_location(["unknown.py:10"], context) is False


def test_review_engine_review_context_override() -> None:
    """Verifies that ReviewEngine.review accepts and uses a context_override."""
    from verifycraft.contracts.review import ReviewRequest

    engine = ReviewEngine()
    custom_context = GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        base_commit="a" * 40,
        head_commit="b" * 40,
        raw_diff="diff --git a/test.py b/test.py",
        changed_files={
            "test.py": ChangedFile(
                path="test.py",
                status="modified",
                content="def run(): pass\n",
            )
        },
        related_files={},
    )

    req = ReviewRequest(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        provider="none",
    )

    report = engine.review(req, context_override=custom_context)
    assert report.model_info is not None
    assert report.model_info.provider == "none"
