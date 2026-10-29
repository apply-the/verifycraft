"""Adversarial evaluation suite testing downstream review pipeline quality.

Tests frozen realistic review artifacts inspired by actual LLM reviewer anomalies:
1. Wrong but syntactically valid line anchors.
2. Large multi-file diffs exceeding default line budget where critical files are at the end.
3. Repeated issues expressed as questions or thoughts.
4. Invalid local/remote provider classification and endpoint security.
5. Security findings with uncertain severity vs blocking policy.
6. Reviews without requirements.
7. Malformed provider responses.
"""

from __future__ import annotations

from typing import Any

import pytest

from verifycraft.contracts.review import (
    ConventionalLabel,
    EvidenceStatus,
    FindingSeverity,
    RequirementStatus,
    ReviewFinding,
    ReviewRequest,
)
from verifycraft.review.engine import ReviewEngine
from verifycraft.review.git import ChangedFile, DiffHunk, GitContextCollector, GitReviewContext
from verifycraft.review.providers.base import (
    ProviderCallError,
    ProviderConfigurationError,
    ProviderResponseParser,
    validate_endpoint_url,
)
from verifycraft.review.providers.registry import is_local_provider

# ==============================================================================
# Frozen Adversarial Raw Candidate Findings Fixture
# ==============================================================================

FROZEN_ADVERSARIAL_RAW_CANDIDATES: list[dict[str, Any]] = [
    # 1. Anchor drift: Model cites line 128 for symbol defined at line 91
    {
        "file_path": "src/pkg/network.py",
        "line": 128,
        "label": "issue",
        "subject": "`LOOPBACK_HOSTS` includes wildcard addresses",
        "description": "`LOOPBACK_HOSTS` should only include localhost and 127.0.0.1.",
        "severity": "high",
        "blocking": False,
        "suggested_action": "Remove 0.0.0.0 from LOOPBACK_HOSTS.",
    },
    # 2. Duplicate observation expressed as a question
    {
        "file_path": "src/pkg/network.py",
        "line": 128,
        "label": "question",
        "subject": "Should `LOOPBACK_HOSTS` allow wildcard binds?",
        "description": "Wildcard 0.0.0.0 is not a loopback address.",
        "severity": "medium",
        "blocking": False,
        "suggested_action": "Exclude wildcard binds from loopback checks.",
    },
    # 3. Completely unanchored hallucinated finding
    {
        "file_path": "src/pkg/network.py",
        "line": 128,
        "label": "issue",
        "subject": "SQL injection in database connection pool",
        "description": "Missing parameter binding in postgresql execute statement.",
        "severity": "critical",
        "blocking": False,
        "suggested_action": "Use parameterized queries.",
    },
]


# ==============================================================================
# Test 1: Anchor Drift Remapping vs Hallucination Downgrade
# ==============================================================================


def test_anchor_drift_remapped_and_hallucination_downgraded() -> None:
    """Findings citing real symbols at drifted lines are remapped;
    ungrounded ones become file-level.
    """
    source_lines = [
        "import os",  # line 1
        "",  # line 2
        "# Configuration",  # line 3
        "LOOPBACK_HOSTS = frozenset({'localhost', '127.0.0.1'})",  # line 4
        "",  # line 5
        "def is_valid():",  # line 6
        "    return True",  # line 7
    ] + [f"# line {i}" for i in range(8, 140)]

    cfile = ChangedFile(
        path="src/pkg/network.py",
        status="modified",
        changed_lines=set(range(1, 140)),
        hunks=[DiffHunk(old_start=1, old_lines=130, new_start=1, new_lines=139, content="...")],
        content="\n".join(source_lines),
    )
    changed_files = {"src/pkg/network.py": cfile}

    collector = GitContextCollector(".")

    # Anchor drift test: LOOPBACK_HOSTS cited at line 128 should remap to line 4
    raw_drift = FROZEN_ADVERSARIAL_RAW_CANDIDATES[0]
    line, reason = collector.validate_semantic_anchor(
        file_path=raw_drift["file_path"],
        line=raw_drift["line"],
        subject=raw_drift["subject"],
        description=raw_drift["description"],
        changed_files=changed_files,
        suggested_action=raw_drift["suggested_action"],
    )
    assert line == 4
    assert reason is not None and "remapped" in reason.lower()

    # Hallucination test: SQL injection on network file has no supporting code;
    # downgrade to file-level
    raw_hallucination = FROZEN_ADVERSARIAL_RAW_CANDIDATES[2]
    line_h, reason_h = collector.validate_semantic_anchor(
        file_path=raw_hallucination["file_path"],
        line=raw_hallucination["line"],
        subject=raw_hallucination["subject"],
        description=raw_hallucination["description"],
        changed_files=changed_files,
        suggested_action=raw_hallucination["suggested_action"],
    )
    assert line_h is None
    assert reason_h is not None and (
        "unanchored" in reason_h.lower() or "downgraded" in reason_h.lower()
    )


# ==============================================================================
# Test 2: Multi-file Diff Bounded Acquisition & Fair Representation
# ==============================================================================


def test_critical_file_at_end_of_diff_not_starved() -> None:
    """When a large file leads the diff, critical files at the end are still fairly included."""
    # Build a synthetic diff: file1 has 200 lines, file2 has 20 lines, file3 has 30 lines
    file1_hunk = "".join(f"+file1_line_{i}\n" for i in range(200))
    file2_hunk = "".join(f"+file2_line_{i}\n" for i in range(20))
    file3_hunk = "".join(f"+critical_security_line_{i}\n" for i in range(30))

    full_diff = (
        "diff --git a/pkg/large_file.py b/pkg/large_file.py\n"
        "--- a/pkg/large_file.py\n"
        "+++ b/pkg/large_file.py\n"
        "@@ -1,5 +1,200 @@\n"
        f"{file1_hunk}"
        "diff --git a/pkg/helper.py b/pkg/helper.py\n"
        "--- a/pkg/helper.py\n"
        "+++ b/pkg/helper.py\n"
        "@@ -1,5 +1,20 @@\n"
        f"{file2_hunk}"
        "diff --git a/pkg/security.py b/pkg/security.py\n"
        "--- a/pkg/security.py\n"
        "+++ b/pkg/security.py\n"
        "@@ -1,5 +1,30 @@\n"
        f"{file3_hunk}"
    )

    collector = GitContextCollector(".")
    omitted_scope: list[str] = []

    # Impose a budget of 120 lines total (less than file1 alone, which is 200+ lines)
    bounded_diff = collector._bounded_diff_acquisition(
        diff_text=full_diff,
        max_diff_lines=120,
        omitted_scope=omitted_scope,
    )

    # 1. Critical file at the end MUST be represented in the bounded diff
    assert "pkg/security.py" in bounded_diff
    assert "critical_security_line" in bounded_diff

    # 2. Hunk boundaries are preserved (no half-cut hunks)
    assert not bounded_diff.endswith("+critical_security_line_14")  # Not severed mid-hunk

    # 3. Scope limitations are explicitly recorded
    assert any("PARTIAL" in s for s in omitted_scope)
    assert any("Incomplete diff scope cannot be interpreted" in s for s in omitted_scope)


# ==============================================================================
# Test 3: Semantic Deduplication of Issues and Questions
# ==============================================================================


def test_downstream_deduplication_of_question_variants() -> None:
    """Candidate findings describing the same issue as both an issue and a question are unified."""
    engine = ReviewEngine()
    findings = [
        ReviewFinding(
            id="rev-001",
            file_path="src/pkg/network.py",
            line=4,
            label=ConventionalLabel.ISSUE,
            subject="`LOOPBACK_HOSTS` includes wildcard addresses",
            description="Wildcard 0.0.0.0 is not a loopback address.",
            severity=FindingSeverity.HIGH,
            blocking=False,
            evidence_status=EvidenceStatus.MODEL_INFERRED,
        ),
        ReviewFinding(
            id="rev-002",
            file_path="src/pkg/network.py",
            line=4,
            label=ConventionalLabel.QUESTION,
            subject="Should `LOOPBACK_HOSTS` allow wildcard binds?",
            description="Is 0.0.0.0 intended to bypass plain HTTP guards?",
            severity=FindingSeverity.MEDIUM,
            blocking=False,
            evidence_status=EvidenceStatus.MODEL_INFERRED,
        ),
    ]

    deduped = engine._deduplicate_findings(findings)
    assert len(deduped) == 1
    # Preserves actionable issue label and highest severity
    assert deduped[0].label == ConventionalLabel.ISSUE
    assert deduped[0].severity == FindingSeverity.HIGH
    # Merges question context into description
    assert "Related perspective [question]" in deduped[0].description


# ==============================================================================
# Test 4: Endpoint Security & Locality Invariant
# ==============================================================================


def test_endpoint_security_and_locality_invariants() -> None:
    """Locality depends on actual endpoint; plain HTTP is rejected for remote endpoints."""
    # Remote endpoints must NOT be classified as local
    assert not is_local_provider("openai-compatible", base_url="https://api.external.com/v1")
    assert not is_local_provider("openai-compatible", base_url="http://192.168.1.50:8000/v1")

    # Insecure HTTP to remote host must raise ProviderConfigurationError
    with pytest.raises(ProviderConfigurationError):
        validate_endpoint_url("http://api.external.com/v1", allow_insecure_http=False)

    # Local loopback HTTP is permitted
    validate_endpoint_url("http://127.0.0.1:8000/v1", allow_insecure_http=False)


# ==============================================================================
# Test 5: Review Without Requirements Assessment
# ==============================================================================


def test_review_without_requirements_produces_transparent_report() -> None:
    """Reviews without explicit requirements assess NONE as not_assessed with inline note."""
    engine = ReviewEngine()
    req = ReviewRequest(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        provider="none",
        candidate_findings=[],
    )
    context = GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        base_commit="11111111",
        head_commit="22222222",
        changed_files={},
        raw_diff="",
        related_files={},
    )
    report = engine.review(req, context_override=context)
    assert len(report.requirement_assessments) == 1
    assessment = report.requirement_assessments[0]
    assert assessment.requirement_id == "NONE"
    assert assessment.status == RequirementStatus.NOT_ASSESSED
    assert "No requirements document was supplied" in assessment.explanation


# ==============================================================================
# Test 6: Malformed Provider Output Handling
# ==============================================================================


def test_malformed_provider_output_handled_defensively() -> None:
    """Malformed or empty output raises typed ProviderCallError with clear explanation."""
    # Empty string
    with pytest.raises(ProviderCallError, match="empty response"):
        ProviderResponseParser.parse_json_response("")

    # Truncated JSON
    with pytest.raises(ProviderCallError, match="invalid JSON"):
        ProviderResponseParser.parse_json_response('{"summary": "incomplete')

    # Non-dictionary root
    with pytest.raises(ProviderCallError):
        ProviderResponseParser.parse_json_response("[\"not\", \"a\", \"dict\"]")
