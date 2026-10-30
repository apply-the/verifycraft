"""Unit tests for benchmark accuracy, ground truth adjudication, and CLI observability."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from benchmarks.adjudication import (
    AdjudicationCatalog,
    AdjudicationDecision,
    AdjudicationRecord,
    compute_finding_fingerprint,
)
from benchmarks.evaluate_review import (
    BenchmarkProgressReporter,
    _is_finding_match,
    _match_findings_to_expected,
    _normalize_requirement_status,
    rescore_report,
)
from benchmarks.review_dataset import (
    ExpectedIssue,
    GroundTruth,
    ReviewEvalCase,
)

from verifycraft.contracts import RequirementStatus
from verifycraft.review.engine import ReviewEngine
from verifycraft.review.providers.base import (
    RawCandidateFinding,
    RawRequirementAssessment,
)


@pytest.fixture
def sample_case() -> ReviewEvalCase:
    gt = GroundTruth(
        expected_verdict="has_defects",
        expected_issues=[
            ExpectedIssue(
                defect_id="DEFECT-AUTH-EXPIRY",
                file_path="src/auth/service.py",
                line_range=[7, 10],
                subject_contains="expiration",
                aliases=["expired", "expires_at", "expiry"],
                evidence_anchors=["expires_at", "authenticate"],
                label="issue",
                severity="high",
            )
        ],
        forbidden_false_positives=["token valid"],
    )
    return ReviewEvalCase(
        case_id="dev_01_req_violation_auth_expiry",
        name="Auth Expiry Bug",
        split="dev",
        category="auth",
        version="1.1.0",
        provenance="test",
        base_commit="1" * 40,
        head_commit="2" * 40,
        changed_files={
            "src/auth/service.py": "\n".join(
                [
                    "def authenticate(token):",
                    "    if not token:",
                    "        return False",
                    "    user = get_user(token.user_id)",
                    "    if not user.is_active:",
                    "        return False",
                    "    # Bug: token expiration check is missing",
                    "    # token.expires_at should be validated",
                    "    if token.revoked:",
                    "        return False",
                    "    return True",
                ]
            )
        },
        raw_diff="diff",
        ground_truth=gt,
    )


def test_dev_01_semantic_equivalence_matching(sample_case: ReviewEvalCase) -> None:
    """Verifies that findings using valid synonyms ('expired', 'expires_at') match ground truth."""
    issue = sample_case.ground_truth.expected_issues[0]

    # Finding with alias 'expired' and anchor 'token.expires_at'
    f1 = RawCandidateFinding(
        file_path="src/auth/service.py",
        line=8,
        label="issue",
        subject="Expired tokens are accepted as valid",
        description="The method fails to check token.expires_at against current timestamp.",
    )
    assert _is_finding_match(f1, issue, sample_case) is True

    # Finding with alias 'expiry' and anchor 'expires_at'
    f2 = RawCandidateFinding(
        file_path="src/auth/service.py",
        line=7,
        label="issue",
        subject="Missing token expiry validation",
        description="Token could be expired; token.expires_at is never checked.",
    )
    assert _is_finding_match(f2, issue, sample_case) is True

    # Negative: wrong file path
    f_wrong_file = RawCandidateFinding(
        file_path="src/auth/token.py",
        line=8,
        label="issue",
        subject="Expired tokens are accepted as valid",
        description="Missing token.expires_at check.",
    )
    assert _is_finding_match(f_wrong_file, issue, sample_case) is False

    # Negative: out of range line
    f_out_of_range = RawCandidateFinding(
        file_path="src/auth/service.py",
        line=1,
        label="issue",
        subject="Expired tokens are accepted as valid",
        description="Missing token.expires_at check.",
    )
    assert _is_finding_match(f_out_of_range, issue, sample_case) is False

    # Negative: unrelated subject
    f_unrelated = RawCandidateFinding(
        file_path="src/auth/service.py",
        line=8,
        label="issue",
        subject="Unused local variable",
        description="Code style issue.",
    )
    assert _is_finding_match(f_unrelated, issue, sample_case) is False


def test_bipartite_matching_and_duplicate_handling(sample_case: ReviewEvalCase) -> None:
    """Verifies 1-to-1 matching: multiple findings for 1 defect cannot inflate TP count."""
    issue = sample_case.ground_truth.expected_issues[0]

    f1 = RawCandidateFinding(
        file_path="src/auth/service.py",
        line=8,
        label="issue",
        subject="Expired tokens accepted",
        description="Missing expires_at check in authenticate.",
    )
    f2 = RawCandidateFinding(
        file_path="src/auth/service.py",
        line=8,
        label="issue",
        subject="Missing token expiration",
        description="The expiry date is ignored in authenticate.",
    )

    findings = [f1, f2]
    matched_count, matched_findings, duplicate_findings = _match_findings_to_expected(
        findings, [issue], sample_case
    )

    # Exactly 1 defect matched
    assert matched_count == 1
    # Exactly 1 finding consumes the defect
    assert len(matched_findings) == 1
    assert matched_findings[0] == f1
    # Second finding is recorded as duplicate
    assert len(duplicate_findings) == 1
    assert duplicate_findings[0] == f2


def test_single_finding_cannot_satisfy_multiple_defects() -> None:
    """Verifies that 1 candidate finding cannot be counted across multiple defects."""
    issue1 = ExpectedIssue(
        defect_id="DEFECT-01",
        file_path="src/app.py",
        line_range=[10, 15],
        subject_contains="validation",
        label="issue",
    )
    issue2 = ExpectedIssue(
        defect_id="DEFECT-02",
        file_path="src/app.py",
        line_range=[12, 16],
        subject_contains="validation",
        label="issue",
    )
    gt = GroundTruth(
        expected_verdict="has_defects",
        expected_issues=[issue1, issue2],
    )
    case = ReviewEvalCase(
        case_id="multi_defect_case",
        name="Multi Defect",
        split="dev",
        category="general",
        version="1.0.0",
        provenance="test",
        base_commit="1" * 40,
        head_commit="2" * 40,
        changed_files={"src/app.py": "content\n" * 20},
        raw_diff="diff",
        ground_truth=gt,
    )

    single_finding = RawCandidateFinding(
        file_path="src/app.py",
        line=13,
        label="issue",
        subject="Missing validation check",
        description="Validation is omitted.",
    )

    matched_count, matched_findings, duplicate_findings = _match_findings_to_expected(
        [single_finding], [issue1, issue2], case
    )

    # Matches only 1 defect, not both
    assert matched_count == 1
    assert len(matched_findings) == 1
    assert len(duplicate_findings) == 0


def test_adjudication_catalog_lookup_and_fingerprint() -> None:
    """Verifies adjudication fingerprinting and catalog lookups."""
    catalog = AdjudicationCatalog(version="1.0.0")
    record = AdjudicationRecord(
        case_id="dev_09_missing_edge_case_empty_collection",
        file_path="src/handlers/users.py",
        line=4,
        subject="Missing 'id' key in primary dict",
        decision=AdjudicationDecision.UNSUPPORTED_CLAIM,
        rationale="Assuming callers omit id key without schema proof is unsupported.",
        evidence="src/handlers/users.py:4",
        reviewer="Security Reviewer",
        adjudicated_at="2026-10-30T00:00:00Z",
    )
    catalog.add_record(record)

    # Direct fingerprint lookup
    found = catalog.find_adjudication(
        case_id="dev_09_missing_edge_case_empty_collection",
        file_path="src/handlers/users.py",
        line=4,
        subject="Missing 'id' key in primary dict",
    )
    assert found is not None
    assert found.decision == AdjudicationDecision.UNSUPPORTED_CLAIM
    assert found.counts_as_false_positive is True
    assert found.counts_as_true_positive is False

    # Heuristic lookup with matching file, close line, and word overlap
    found_heuristic = catalog.find_adjudication(
        case_id="dev_09_missing_edge_case_empty_collection",
        file_path="src/handlers/users.py",
        line=5,
        subject="Missing 'id' key in primary user dict raises KeyError",
    )
    assert found_heuristic is not None
    assert found_heuristic.decision == AdjudicationDecision.UNSUPPORTED_CLAIM

    # Fingerprint determinism
    fp1 = compute_finding_fingerprint("case1", "src/a.py", 10, "Subject text")
    fp2 = compute_finding_fingerprint("case1", "src/a.py", 10, "Subject text")
    fp3 = compute_finding_fingerprint("case1", "src/a.py", 10, "Different text")
    assert fp1 == fp2
    assert fp1 != fp3


def test_live_adjudications_catalog_file() -> None:
    """Verifies that checked-in adjudications.json is loaded and resolves dev_09 adjudication."""
    catalog_path = Path("benchmarks/data/review_eval/adjudications.json")
    assert catalog_path.exists(), "Adjudications catalog file must exist"

    catalog = AdjudicationCatalog.from_json_file(catalog_path)
    assert catalog.version == "1.0.0"

    adj = catalog.find_adjudication(
        case_id="dev_09_missing_edge_case_empty_collection",
        file_path="src/handlers/users.py",
        line=4,
        subject="Missing 'id' key raises KeyError",
    )
    assert adj is not None
    assert adj.decision == AdjudicationDecision.UNSUPPORTED_CLAIM
    assert adj.counts_as_false_positive is True


def test_requirement_status_normalization() -> None:
    """Verifies that 'satisfied' and other synonymous provider statuses are normalized."""
    assert _normalize_requirement_status("satisfied") == RequirementStatus.APPEARS_SATISFIED.value
    assert _normalize_requirement_status("SATISFIED") == RequirementStatus.APPEARS_SATISFIED.value
    sat_val = RequirementStatus.APPEARS_SATISFIED.value
    assert _normalize_requirement_status("appears_satisfied") == sat_val
    assert _normalize_requirement_status("not_satisfied") == RequirementStatus.NOT_SATISFIED.value
    assert _normalize_requirement_status("unknown") == "unknown"


def test_engine_requirement_assessment_parsing_satisfied() -> None:
    """Verifies that ReviewEngine._assess_from_provider parses provider status 'satisfied'."""
    engine = ReviewEngine()
    raw_a = RawRequirementAssessment(
        requirement_id="REQ-01",
        status="satisfied",
        explanation="Requirement is fully met with proper error handling.",
        related_locations=["src/service.py:10"],
    )
    mock_cfile = MagicMock()
    mock_cfile.content = "line\n" * 20
    mock_context = MagicMock()
    mock_context.changed_files = {"src/service.py": mock_cfile}
    mock_context.related_files = {}

    status, explanation = engine._assess_from_provider(
        raw_a=raw_a,
        req_id="REQ-01",
        is_relevant=True,
        context=mock_context,
        has_violating_findings=False,
    )
    assert status == RequirementStatus.APPEARS_SATISFIED
    assert "fully met" in explanation


def test_offline_rescore_live_deepseek_evaluation() -> None:
    """Verifies offline rescoring on the immutable live DeepSeek benchmark evidence."""
    evidence_path = Path("benchmarks/data/review_eval/reports/live_deepseek_evaluation_raw.json")
    assert evidence_path.exists(), "Immutable live DeepSeek evaluation raw report must exist"

    rescore_data = rescore_report(evidence_path, verbose=False)

    assert rescore_data["execution_mode"] == "live"
    assert rescore_data["provider"] == "deepseek"
    assert rescore_data["cases_count"] == 16
    assert rescore_data["total_expected_defects"] == 10

    # Old status vs new status
    assert rescore_data["old_gate_status"] == "FAILED_QUALITY_GATE"
    assert rescore_data["new_gate_status"] == "PENDING_MAINTAINER_APPROVAL"

    # Variant C metrics
    var_c = rescore_data["variants"]["variant_c"]
    assert var_c["correct_findings"] == 10  # 10/10 defect yield
    assert var_c["defect_recall"] == 1.0  # 100% recall
    assert var_c["false_positives"] == 1  # only 1 FP (the adjudicated dev_09 KeyError claim)
    assert var_c["actionable_precision"] > 0.90  # 90.91% >= 85% threshold
    assert var_c["invalid_locations"] == 0

    # Deltas audit
    deltas = rescore_data["decision_deltas"]
    assert len(deltas) >= 3

    # Check dev_01 delta
    dev_01_deltas = [d for d in deltas if d["case_id"] == "dev_01_req_violation_auth_expiry"]
    assert any(
        d["metric"] == "correct_findings" and d["old_value"] == 0 and d["new_value"] == 1
        for d in dev_01_deltas
    )
    assert any(
        d["metric"] == "false_positives" and d["old_value"] == 1 and d["new_value"] == 0
        for d in dev_01_deltas
    )


def test_cli_progress_reporter_quiet_suppression(capsys: pytest.CaptureFixture[str]) -> None:
    """Verifies BenchmarkProgressReporter writes to stderr and is silenced by quiet=True."""
    reporter_verbose = BenchmarkProgressReporter(quiet=False)
    reporter_verbose.case_start(1, 10, "dev_01_test")
    reporter_verbose.log("All done")

    captured_verbose = capsys.readouterr()
    assert "dev_01_test" in captured_verbose.err
    assert "All done" in captured_verbose.err
    assert captured_verbose.out == ""

    reporter_quiet = BenchmarkProgressReporter(quiet=True)
    reporter_quiet.case_start(1, 10, "dev_01_test")
    reporter_quiet.variant_start("Variant A")
    reporter_quiet.log("All done")

    captured_quiet = capsys.readouterr()
    assert captured_quiet.err == ""
    assert captured_quiet.out == ""


def test_cli_rescore_stream_separation_and_json(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies that running evaluate_review --rescore --json outputs valid JSON to stdout only."""
    import json
    import sys

    from benchmarks.evaluate_review import main

    evidence_path = "benchmarks/data/review_eval/reports/live_deepseek_evaluation_raw.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate_review",
            "--rescore",
            evidence_path,
            "--json",
            "--quiet",
        ],
    )

    main()
    captured = capsys.readouterr()
    assert captured.err == ""
    assert len(captured.out) > 0

    # Ensure stdout is parseable JSON
    data = json.loads(captured.out)
    assert data["provider"] == "deepseek"
    assert data["new_gate_status"] == "PENDING_MAINTAINER_APPROVAL"


def test_cli_live_missing_deepseek_key_fails_closed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verifies that live mode for deepseek fails closed when DEEPSEEK_API_KEY is not set."""
    import sys

    from benchmarks.evaluate_review import main

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate_review",
            "--mode",
            "live",
            "--provider",
            "deepseek",
            "--split",
            "dev",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "DEEPSEEK_API_KEY" in captured.err
    assert "Fails closed" in captured.err
