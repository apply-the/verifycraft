"""Regression tests for source coordinate correctness and bounded context."""

from __future__ import annotations

import ast
from unittest.mock import patch

from verifycraft.review.engine import ReviewEngine
from verifycraft.review.git import (
    ChangedFile,
    DiffHunk,
    GitContextCollector,
)


def _generate_1000_line_file_content() -> str:
    """Generate a syntactically valid 1,000-line Python source file with exact line indices."""
    lines: list[str] = [f"val_{i} = {i}" for i in range(1, 1001)]
    lines[0] = "import os"
    lines[1] = "import sys"
    lines[2] = "import logging"
    lines[4] = "logger = logging.getLogger(__name__)"

    # At line 400 (0-indexed 399)
    lines[399] = "def process_batch(items):"
    lines[400] = "    # Coordinate check at line 401"
    lines[401] = "    batch_counter = len(items)"
    lines[402] = "    return batch_counter"

    # At line 900 (0-indexed 899)
    lines[899] = "def finalize_batch(result):"
    lines[900] = "    # Coordinate check at line 901"
    lines[901] = "    batch_counter = result * 2"
    lines[902] = "    return batch_counter"

    return "\n".join(lines)


def test_large_file_disjoint_windows_preserve_real_coordinates() -> None:
    """A 1,000-line file with hunks at lines 400 and 900 preserves exact source line numbers."""
    content = _generate_1000_line_file_content()
    collector = GitContextCollector(repo_path=".")

    hunk1 = DiffHunk(old_start=400, old_lines=10, new_start=400, new_lines=10, content="...")
    hunk2 = DiffHunk(old_start=900, old_lines=10, new_start=900, new_lines=10, content="...")
    cfile = ChangedFile(
        path="src/large_service.py",
        status="modified",
        changed_lines=set(range(400, 411)) | set(range(900, 911)),
        hunks=[hunk1, hunk2],
    )

    omitted: list[str] = []
    with patch.object(collector, "read_file_at_ref", return_value=content):
        total_bytes = collector._populate_file_contents(
            changed_files={"src/large_service.py": cfile},
            head_revision="HEAD",
            max_context_bytes=4096,
            omitted_scope=omitted,
        )

    # 1. Full content preserved on cfile.content
    assert cfile.content is not None
    assert len(cfile.content.splitlines()) == 1000
    assert total_bytes > 0

    # 2. Prompt context exists and preserves original 1-indexed line numbers
    assert cfile.prompt_context is not None
    prompt = cfile.prompt_context

    # Both windows are present with real coordinates
    assert "400: def process_batch(items):" in prompt
    assert "900: def finalize_batch(result):" in prompt

    # Omission marker is present between disjoint windows
    assert "surrounding lines omitted for brevity" in prompt

    # Disjoint windows do NOT reset line numbers to 1..50
    assert "1: def finalize_batch" not in prompt
    assert "50: def finalize_batch" not in prompt


def test_finding_at_line_900_not_remapped_to_small_artificial_number() -> None:
    """A finding at line 900 is retained and never remapped to small window indexes."""
    content = _generate_1000_line_file_content()
    collector = GitContextCollector(repo_path=".")

    hunk1 = DiffHunk(old_start=400, old_lines=10, new_start=400, new_lines=10, content="...")
    hunk2 = DiffHunk(old_start=900, old_lines=10, new_start=900, new_lines=10, content="...")
    cfile = ChangedFile(
        path="src/large_service.py",
        status="modified",
        changed_lines=set(range(400, 411)) | set(range(900, 911)),
        hunks=[hunk1, hunk2],
        content=content,
    )
    changed_files = {"src/large_service.py": cfile}

    # Finding cites line 900 and symbol `finalize_batch`
    line, reason = collector.validate_semantic_anchor(
        file_path="src/large_service.py",
        line=900,
        subject="Validate return type in `finalize_batch`",
        description="Check finalize_batch logic",
        changed_files=changed_files,
    )
    assert line == 900
    assert reason is None


def test_symbol_in_multiple_windows_not_misattributed() -> None:
    """A symbol appearing in multiple windows is anchored to its window and not misattributed."""
    content = _generate_1000_line_file_content()
    collector = GitContextCollector(repo_path=".")

    hunk1 = DiffHunk(old_start=400, old_lines=10, new_start=400, new_lines=10, content="...")
    hunk2 = DiffHunk(old_start=900, old_lines=10, new_start=900, new_lines=10, content="...")
    cfile = ChangedFile(
        path="src/large_service.py",
        status="modified",
        changed_lines=set(range(400, 411)) | set(range(900, 911)),
        hunks=[hunk1, hunk2],
        content=content,
    )
    changed_files = {"src/large_service.py": cfile}

    # `batch_counter` appears in both window 1 (line 402) and window 2 (line 902)
    # Finding at line 402 remains anchored to line 402
    line_402, reason_402 = collector.validate_semantic_anchor(
        file_path="src/large_service.py",
        line=402,
        subject="Check `batch_counter` in window 1",
        description="Invalid counter calculation",
        changed_files=changed_files,
    )
    assert line_402 == 402
    assert reason_402 is None

    # Finding at line 902 remains anchored to line 902 (never jumped to window 1)
    line_902, reason_902 = collector.validate_semantic_anchor(
        file_path="src/large_service.py",
        line=902,
        subject="Check `batch_counter` in window 2",
        description="Invalid counter calculation",
        changed_files=changed_files,
    )
    assert line_902 == 902
    assert reason_902 is None

    # Finding citing line 50 (outside both hunks) with symbol `batch_counter`:
    # Cannot be ambiguously attributed to line 402 vs line 902; must downgrade to file-level
    line_50, reason_50 = collector.validate_semantic_anchor(
        file_path="src/large_service.py",
        line=50,
        subject="Ambiguous check on `batch_counter`",
        description="Issue with counter",
        changed_files=changed_files,
    )
    assert line_50 is None
    assert reason_50 is not None
    assert "outside changed hunks" in reason_50.lower()


def test_valid_original_anchor_retained_when_supported() -> None:
    """Original line within hunk adjacent to symbol is preserved."""
    content = _generate_1000_line_file_content()
    collector = GitContextCollector(repo_path=".")

    hunk2 = DiffHunk(old_start=900, old_lines=10, new_start=900, new_lines=10, content="...")
    cfile = ChangedFile(
        path="src/large_service.py",
        status="modified",
        changed_lines=set(range(900, 911)),
        hunks=[hunk2],
        content=content,
    )
    changed_files = {"src/large_service.py": cfile}

    # Line 901 is inside the hunk, 1 line away from `finalize_batch` (line 900)
    line, reason = collector.validate_semantic_anchor(
        file_path="src/large_service.py",
        line=901,
        subject="Incorrect comment inside `finalize_batch`",
        description="Misleading coordinate comment",
        changed_files=changed_files,
    )
    assert line == 901
    assert reason is None


def test_unsupported_anchor_becomes_file_level_observation() -> None:
    """An unsupported anchor that cannot be grounded becomes a file-level observation."""
    content = _generate_1000_line_file_content()
    collector = GitContextCollector(repo_path=".")

    hunk1 = DiffHunk(old_start=400, old_lines=10, new_start=400, new_lines=10, content="...")
    cfile = ChangedFile(
        path="src/large_service.py",
        status="modified",
        changed_lines=set(range(400, 411)),
        hunks=[hunk1],
        content=content,
    )
    changed_files = {"src/large_service.py": cfile}

    # Line 405 cites a completely non-existent symbol `missing_kafka_producer`
    line, reason = collector.validate_semantic_anchor(
        file_path="src/large_service.py",
        line=405,
        subject="Missing flush on `missing_kafka_producer`",
        description="Kafka producer connection lost",
        changed_files=changed_files,
    )
    assert line is None
    assert reason is not None
    assert (
        "unanchored observation" in reason.lower()
        or "could not be semantically grounded" in reason.lower()
    )


def test_unavailable_source_content_downgraded_gracefully() -> None:
    """When source content is unavailable, findings are explicitly downgraded."""
    collector = GitContextCollector(repo_path=".")
    cfile = ChangedFile(
        path="src/deleted_or_unreadable.py",
        status="modified",
        changed_lines={10, 11},
        content=None,
    )
    changed_files = {"src/deleted_or_unreadable.py": cfile}

    line, reason = collector.validate_semantic_anchor(
        file_path="src/deleted_or_unreadable.py",
        line=10,
        subject="Some issue",
        description="Some details",
        changed_files=changed_files,
    )
    assert line is None
    assert reason is not None
    assert "source content unavailable" in reason.lower()


def test_context_budget_strictly_enforced() -> None:
    """Context acquisition enforces budget and avoids exceeding max_context_bytes."""
    content = _generate_1000_line_file_content()
    collector = GitContextCollector(repo_path=".")

    hunk1 = DiffHunk(old_start=400, old_lines=10, new_start=400, new_lines=10, content="...")
    hunk2 = DiffHunk(old_start=900, old_lines=10, new_start=900, new_lines=10, content="...")
    cfile = ChangedFile(
        path="src/large_service.py",
        status="modified",
        changed_lines=set(range(400, 411)) | set(range(900, 911)),
        hunks=[hunk1, hunk2],
    )

    omitted: list[str] = []
    # Set a tiny budget of 800 bytes
    with patch.object(collector, "read_file_at_ref", return_value=content):
        total_bytes = collector._populate_file_contents(
            changed_files={"src/large_service.py": cfile},
            head_revision="HEAD",
            max_context_bytes=800,
            omitted_scope=omitted,
        )

    # Budget is respected
    assert total_bytes <= 800
    if cfile.prompt_context is not None:
        assert len(cfile.prompt_context.encode("utf-8")) <= 800
    else:
        assert any("omitted due to context size limits" in note for note in omitted)


def test_ast_validation_receives_complete_source() -> None:
    """AST validation parses complete source with correct line coordinates."""
    content = _generate_1000_line_file_content()
    tree = ast.parse(content)
    assert tree is not None

    # Find process_batch and finalize_batch AST nodes
    func_names: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            func_names[node.name] = node.lineno

    assert func_names["process_batch"] == 400
    assert func_names["finalize_batch"] == 900


def test_review_engine_formats_multiple_separated_hunks() -> None:
    """ReviewEngine formats multiple separated hunks with original coordinates."""
    engine = ReviewEngine()
    lines = [f"def func_{i}(): pass" for i in range(1, 1001)]
    content = "\n".join(lines)

    hunk1 = DiffHunk(old_start=150, old_lines=5, new_start=150, new_lines=5, content="")
    hunk2 = DiffHunk(old_start=550, old_lines=5, new_start=550, new_lines=5, content="")
    hunk3 = DiffHunk(old_start=950, old_lines=5, new_start=950, new_lines=5, content="")

    cfile = ChangedFile(
        path="src/multi_hunk.py",
        status="modified",
        content=content,
        hunks=[hunk1, hunk2, hunk3],
    )

    formatted = engine._format_changed_file_context(cfile)
    assert "1: def func_1(): pass" in formatted
    assert "150: def func_150(): pass" in formatted
    assert "550: def func_550(): pass" in formatted
    assert "950: def func_950(): pass" in formatted
    assert "surrounding lines omitted for brevity" in formatted


def test_end_to_end_review_preserves_separated_hunk_coordinates() -> None:
    """End-to-end review engine preserves original coordinates for multiple separated hunks."""
    from unittest.mock import MagicMock

    from verifycraft.contracts.review import ReviewPolicy, ReviewRequest
    from verifycraft.review.providers.base import ProviderResponse, RawCandidateFinding

    lines = [f"val_{i} = {i}" for i in range(1, 1001)]
    lines[149] = "def func_150():"
    lines[150] = "    return 'a'"
    lines[549] = "def func_550():"
    lines[550] = "    return 'b'"
    lines[949] = "def func_950():"
    lines[950] = "    return 'c'"
    content = "\n".join(lines)

    hunk1 = DiffHunk(old_start=150, old_lines=5, new_start=150, new_lines=5, content="...")
    hunk2 = DiffHunk(old_start=550, old_lines=5, new_start=550, new_lines=5, content="...")
    hunk3 = DiffHunk(old_start=950, old_lines=5, new_start=950, new_lines=5, content="...")

    cfile = ChangedFile(
        path="src/multi_hunk.py",
        status="modified",
        changed_lines={150, 550, 950},
        hunks=[hunk1, hunk2, hunk3],
        content=content,
    )

    mock_collector = MagicMock()
    from verifycraft.review.git import GitReviewContext

    mock_collector.collect.return_value = GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        base_commit="abc12345",
        head_commit="def67890",
        changed_files={"src/multi_hunk.py": cfile},
        raw_diff="diff --git a/src/multi_hunk.py b/src/multi_hunk.py\n...",
        related_files={},
        omitted_scope=[],
        diff_lines_count=15,
        total_context_bytes=2048,
    )
    # Forward validate_line_anchor to real GitContextCollector logic
    real_collector = GitContextCollector(repo_path=".")
    mock_collector.validate_line_anchor.side_effect = real_collector.validate_line_anchor

    mock_adapter = MagicMock()
    mock_adapter.model_name = "test-model"
    mock_adapter.review.return_value = ProviderResponse(
        summary="Review complete",
        candidate_findings=[
            RawCandidateFinding(
                file_path="src/multi_hunk.py",
                line=150,
                label="issue",
                subject="Issue at `func_150`",
                description="Hunk 1 issue",
                severity="medium",
            ),
            RawCandidateFinding(
                file_path="src/multi_hunk.py",
                line=550,
                label="issue",
                subject="Issue at `func_550`",
                description="Hunk 2 issue",
                severity="medium",
            ),
            RawCandidateFinding(
                file_path="src/multi_hunk.py",
                line=950,
                label="issue",
                subject="Issue at `func_950`",
                description="Hunk 3 issue",
                severity="high",
            ),
        ],
        requirement_assessments=[],
        tokens_used=100,
    )

    engine = ReviewEngine(provider_adapter=mock_adapter)
    report = engine.review(
        ReviewRequest(
            repo_path=".",
            base_revision="main",
            head_revision="HEAD",
            provider="mock",
            policy=ReviewPolicy(timeout_seconds=30.0),
        ),
        context_override=mock_collector.collect.return_value,
    )

    # Verify that all 3 findings preserved their exact original line coordinates
    finding_lines = {f.subject: f.line for f in report.findings}
    assert finding_lines["Issue at `func_150`"] == 150
    assert finding_lines["Issue at `func_550`"] == 550
    assert finding_lines["Issue at `func_950`"] == 950


def test_numbered_source_content_cannot_exceed_allocated_budget() -> None:
    """Validate that actual numbered prompt representation bytes are accounted for against budget.

    If raw source bytes <= budget but numbered representation > budget,
    the complete source is rejected and windowed or omitted, never exceeding budget.
    """
    collector = GitContextCollector(repo_path=".")
    # Create 100 lines: raw is ~800 bytes, but numbered format adds ~400 bytes (~1200 bytes)
    lines = [f"x_{i} = {i}" for i in range(1, 101)]
    raw_content = "\n".join(lines)
    raw_bytes = len(raw_content.encode("utf-8"))
    assert raw_bytes < 1000

    numbered = "\n".join(f"{i}: {line}" for i, line in enumerate(lines, start=1))
    numbered_bytes = len(numbered.encode("utf-8"))
    assert numbered_bytes > 1000

    # Allocate budget = 950 bytes (between raw_bytes and numbered_bytes)
    budget = 950
    hunk = DiffHunk(old_start=50, old_lines=5, new_start=50, new_lines=5, content="...")
    cfile = ChangedFile(
        path="src/service.py",
        status="modified",
        changed_lines={50},
        hunks=[hunk],
    )
    omitted: list[str] = []

    with patch.object(collector, "read_file_at_ref", return_value=raw_content):
        total_bytes = collector._populate_file_contents(
            changed_files={"src/service.py": cfile},
            head_revision="HEAD",
            max_context_bytes=budget,
            omitted_scope=omitted,
        )

    # Invariant: total_bytes must NOT exceed allocated budget
    assert total_bytes <= budget
    if cfile.prompt_context is not None:
        assert len(cfile.prompt_context.encode("utf-8")) <= budget
        # It must not have accepted the full numbered context since numbered_bytes > budget
        assert cfile.prompt_context != numbered
    else:
        assert cfile.is_context_omitted is True


def test_zero_byte_source_budget_prevents_source_content_leak_in_prompt() -> None:
    """A zero-byte source budget marks context omitted and prevents prompt leak."""
    from verifycraft.review.git import GitReviewContext

    collector = GitContextCollector(repo_path=".")
    content = "SECRET_PASSWORD = 'do-not-leak-into-prompt'\n" * 20
    cfile = ChangedFile(
        path="src/secret.py",
        status="modified",
        changed_lines={5},
        hunks=[DiffHunk(old_start=5, old_lines=2, new_start=5, new_lines=2, content="...")],
    )
    omitted: list[str] = []

    with patch.object(collector, "read_file_at_ref", return_value=content):
        total_bytes = collector._populate_file_contents(
            changed_files={"src/secret.py": cfile},
            head_revision="HEAD",
            max_context_bytes=0,
            omitted_scope=omitted,
        )

    assert total_bytes == 0
    assert cfile.is_context_omitted is True
    assert cfile.content == content  # full content retained for deterministic validation

    engine = ReviewEngine()
    formatted = engine._format_changed_file_context(cfile)
    assert formatted == "(Content omitted due to context size limits)"
    assert "SECRET_PASSWORD" not in formatted

    # Verify ReviewEngine._build_prompt also does not leak source content
    ctx = GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        base_commit="abc12345",
        head_commit="def67890",
        changed_files={"src/secret.py": cfile},
        raw_diff="diff --git a/src/secret.py b/src/secret.py\n+dummy diff",
        related_files={},
        omitted_scope=omitted,
    )
    prompt = engine._build_prompt(ctx, requirements=[])
    assert "SECRET_PASSWORD" not in prompt
    assert "(Content omitted due to context size limits)" in prompt


def test_omitted_files_explicitly_reported_as_omitted() -> None:
    """Omitted files remain explicitly reported in omitted_scope and prompt notice."""
    from verifycraft.review.git import GitReviewContext

    collector = GitContextCollector(repo_path=".")
    content = "def test():\n    pass\n" * 50
    cfile = ChangedFile(
        path="src/omitted_file.py",
        status="modified",
        changed_lines={10},
    )
    omitted: list[str] = []

    with patch.object(collector, "read_file_at_ref", return_value=content):
        collector._populate_file_contents(
            changed_files={"src/omitted_file.py": cfile},
            head_revision="HEAD",
            max_context_bytes=10,
            omitted_scope=omitted,
        )

    assert cfile.is_context_omitted is True
    assert any(
        "Content for 'src/omitted_file.py' omitted due to context size limits." in note
        for note in omitted
    )

    engine = ReviewEngine()
    ctx = GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        base_commit="abc12345",
        head_commit="def67890",
        changed_files={"src/omitted_file.py": cfile},
        raw_diff="",
        related_files={},
        omitted_scope=omitted,
    )
    prompt = engine._build_prompt(ctx, requirements=[])
    assert "## Context Scope and Omissions Notice" in prompt
    assert "Content for 'src/omitted_file.py' omitted due to context size limits." in prompt


def test_full_original_line_coordinates_remain_correct_when_context_omitted() -> None:
    """Full original line coordinates and AST remain correct even when context is omitted."""
    from verifycraft.review.git import GitReviewContext

    collector = GitContextCollector(repo_path=".")
    content = _generate_1000_line_file_content()
    cfile = ChangedFile(
        path="src/large_service.py",
        status="modified",
        changed_lines=set(range(400, 411)),
        hunks=[DiffHunk(old_start=400, old_lines=10, new_start=400, new_lines=10, content="...")],
    )
    omitted: list[str] = []

    with patch.object(collector, "read_file_at_ref", return_value=content):
        collector._populate_file_contents(
            changed_files={"src/large_service.py": cfile},
            head_revision="HEAD",
            max_context_bytes=0,  # Force full context omission
            omitted_scope=omitted,
        )

    assert cfile.is_context_omitted is True
    assert cfile.content == content  # complete original content preserved

    # Deterministic semantic anchor validator can still validate line 400 with AST
    line, reason = collector.validate_semantic_anchor(
        file_path="src/large_service.py",
        line=400,
        subject="Validate `process_batch` definition",
        description="Check function signature",
        changed_files={"src/large_service.py": cfile},
    )
    assert line == 400
    assert reason is None

    # Line bounds checking in ReviewEngine also succeeds against cfile.content
    engine = ReviewEngine()
    ctx = GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        base_commit="abc12345",
        head_commit="def67890",
        changed_files={"src/large_service.py": cfile},
        raw_diff="",
        related_files={},
        omitted_scope=omitted,
    )
    assert engine._is_valid_file_and_line("src/large_service.py", 400, ctx) is True
    assert engine._is_valid_file_and_line("src/large_service.py", 1000, ctx) is True
    assert engine._is_valid_file_and_line("src/large_service.py", 1001, ctx) is False


def test_legacy_and_synthetic_changed_file_compatibility() -> None:
    """Synthetic ChangedFile with prompt_context=None formats correctly."""
    engine = ReviewEngine()
    synthetic_cfile = ChangedFile(
        path="src/legacy.py",
        status="modified",
        content="def greet():\n    return 'hello'\n",
    )
    assert synthetic_cfile.is_context_omitted is False
    assert synthetic_cfile.prompt_context is None

    formatted = engine._format_changed_file_context(synthetic_cfile)
    assert "1: def greet():" in formatted
    assert "2:     return 'hello'" in formatted

    empty_cfile = ChangedFile(
        path="src/empty.py",
        status="added",
        content="",
    )
    assert engine._format_changed_file_context(empty_cfile) == "(Empty file)"
