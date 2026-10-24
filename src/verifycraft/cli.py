"""Command line interface for VerifyCraft.

Provides CLI entry points for both verification and semantic code review.
Does not duplicate any verification or review business logic.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from verifycraft import __version__
from verifycraft.contracts.models import (
    CheckSpec,
    CheckType,
    ExecutionMode,
    ExecutionPolicy,
    ExpectedObservation,
    Hypothesis,
    Verdict,
    VerificationInput,
    VerificationResult,
)
from verifycraft.contracts.review import (
    ReviewFinding,
    ReviewPolicy,
    ReviewRequest,
)
from verifycraft.engine import VerificationEngine
from verifycraft.review.engine import ReviewEngine
from verifycraft.review.git import GitError
from verifycraft.review.providers.base import ProviderError


def _format_verification_summary(result: VerificationResult) -> str:
    lines = [
        "=" * 60,
        f" VerifyCraft Verification Report ({result.schema_version})",
        "=" * 60,
        f" Verdict:       {result.verdict.value.upper()}",
        f" Fingerprint:   {result.fingerprint}",
        f" Wall Time:     {result.resource_usage.wall_time_seconds:.4f}s",
    ]
    if result.blocked_reason:
        lines.append(f" Blocked Reason: {result.blocked_reason}")
    if result.inconclusive_reason:
        lines.append(f" Inconclusive:  {result.inconclusive_reason}")

    if result.findings:
        lines.append("-" * 60)
        lines.append(" Findings:")
        for f in result.findings:
            lines.append(f"  [{f.verdict.value}] Hypothesis: {f.hypothesis_id}")
            lines.append(f"    Reason: {f.reason}")

    if result.evidence:
        lines.append("-" * 60)
        lines.append(f" Evidence Collected ({len(result.evidence)} items):")
        for evi in result.evidence:
            lines.append(f"  Evidence ID: {evi.evidence_id} (exit code: {evi.exit_code})")
            if evi.output_truncated:
                lines.append("    [Output truncated due to size limits]")
            if evi.stdout.strip():
                sample = evi.stdout.strip()[:300]
                lines.append(f"    Output sample:\n{sample}")
    lines.append("=" * 60)
    return "\n".join(lines)


# Backward-compatible alias
_format_summary = _format_verification_summary


VERDICT_EXIT_CODES: dict[Verdict, int] = {
    Verdict.SUPPORTED: 0,
    Verdict.REFUTED: 2,
    Verdict.INCONCLUSIVE: 3,
    Verdict.BLOCKED: 4,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verifycraft",
        description="VerifyCraft: Evidence-aware code review and verification engine.",
        epilog=(
            "Verification exit codes:\n"
            "  0  Hypothesis SUPPORTED by observable evidence\n"
            "  1  Operational / CLI usage error\n"
            "  2  Hypothesis REFUTED by evidence\n"
            "  3  Verification INCONCLUSIVE\n"
            "  4  Verification BLOCKED (policy or safety boundary)\n\n"
            "Review exit codes:\n"
            "  0  Review completed successfully\n"
            "  1  Operational / provider / Git error\n"
            "  2  Review completed with blocking issues (when --strict is set)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--version", action="version", version=f"verifycraft {__version__}")

    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # verifycraft verify
    verify_parser = subparsers.add_parser(
        "verify", help="Verify a hypothesis against an approved check."
    )
    verify_parser.add_argument(
        "--input",
        type=Path,
        help="Path to JSON file containing VerificationInput specification.",
    )
    verify_parser.add_argument(
        "--repo",
        default=".",
        help="Repository path (default: current directory).",
    )
    verify_parser.add_argument(
        "--target",
        help="Target test file (e.g. tests/test_regression.py).",
    )
    verify_parser.add_argument(
        "--claim",
        help="Hypothesis claim description.",
    )
    verify_parser.add_argument(
        "--match-pattern",
        action="append",
        default=[],
        help="Expected pattern in check output to support the hypothesis.",
    )
    verify_parser.add_argument(
        "--mode",
        choices=["dry_run", "trusted_local", "fake"],
        default="dry_run",
        help="Execution policy mode (default: dry_run).",
    )
    verify_parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Execution timeout ceiling in seconds (default: 30.0).",
    )
    verify_parser.add_argument(
        "--untrusted",
        action="store_true",
        help="Flag that the code under test is from an untrusted source.",
    )
    verify_parser.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="Output format (summary or json).",
    )

    # verifycraft review
    review_parser = subparsers.add_parser(
        "review",
        help="Review code changes against context and optional requirements.",
    )
    review_parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Target repository path (default: current directory).",
    )
    review_parser.add_argument(
        "--base",
        default="main",
        help="Base git reference or branch (default: main).",
    )
    review_parser.add_argument(
        "--head",
        default="HEAD",
        help="Head git reference or branch (default: HEAD).",
    )
    review_parser.add_argument(
        "--requirements",
        action="append",
        default=[],
        help="Path to requirement document or text (can be specified multiple times).",
    )
    review_parser.add_argument(
        "--candidates",
        "--candidate-findings",
        type=Path,
        dest="candidates",
        default=None,
        help="Path to JSON file containing structured candidate findings for model-free review.",
    )
    review_parser.add_argument(
        "--provider",
        default=None,
        help=(
            "Reviewer model provider: 'gemini', 'deterministic', 'fake', 'none'. "
            "Required unless --candidates is provided."
        ),
    )
    review_parser.add_argument(
        "--model",
        default=None,
        help="Model name override (e.g. 'gemini-2.5-pro').",
    )
    review_parser.add_argument(
        "--format",
        choices=["markdown", "json", "comments"],
        default="markdown",
        help="Output format: markdown, json, or comments (default: markdown).",
    )
    review_parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 2 if any blocking issues are reported.",
    )
    review_parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Review timeout in seconds (default: 60.0).",
    )

    return parser


def _load_input_from_file(input_path_arg: Path) -> VerificationInput:
    input_path = input_path_arg.resolve()
    try:
        if not input_path.is_file():
            raise FileNotFoundError(f"File not found or not a regular file: {input_path_arg}")
        raw_json = input_path.read_text(encoding="utf-8")
        return VerificationInput.model_validate_json(raw_json)
    except Exception as exc:
        print(f"Error reading input JSON: {exc}", file=sys.stderr)
        sys.exit(1)


def _build_input_from_args(args: argparse.Namespace) -> VerificationInput:
    if not args.target or not args.claim:
        print(
            "Error: Either --input <file> or both --target and --claim must be specified.",
            file=sys.stderr,
        )
        sys.exit(1)

    hyp = Hypothesis(
        id="hyp-cli",
        claim=args.claim,
        expected_observation=ExpectedObservation(
            expected_outcome="fail",
            expected_exit_codes=[1],
            match_patterns=args.match_pattern,
        ),
    )
    spec = CheckSpec(
        check_id="chk-cli",
        check_type=CheckType.PYTEST,
        target_path=args.target,
    )
    return VerificationInput(
        repo_path=args.repo,
        hypothesis=hyp,
        check_spec=spec,
    )


def _execute_verify(args: argparse.Namespace) -> None:
    verification_input = (
        _load_input_from_file(args.input) if args.input else _build_input_from_args(args)
    )

    policy = ExecutionPolicy(
        mode=ExecutionMode(args.mode),
        timeout_seconds=args.timeout,
        untrusted_code=args.untrusted,
    )

    engine = VerificationEngine()
    result = engine.verify(verification_input, policy=policy)

    if args.format == "json":
        print(result.to_json(indent=2))
    else:
        print(_format_verification_summary(result))

    sys.exit(VERDICT_EXIT_CODES.get(result.verdict, 1))


def _load_candidates_from_file(candidates_path_arg: Path) -> list[ReviewFinding]:
    path = candidates_path_arg.resolve()
    if not path.is_file():
        print(f"Error: Candidate findings file not found: {candidates_path_arg}", file=sys.stderr)
        sys.exit(1)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        items: list[Any] = []
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, dict):
            if "candidate_findings" in raw and isinstance(raw["candidate_findings"], list):
                items = raw["candidate_findings"]
            elif "findings" in raw and isinstance(raw["findings"], list):
                items = raw["findings"]
            else:
                items = [raw]
        else:
            raise ValueError("JSON content must be a list or object containing findings")

        findings: list[ReviewFinding] = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if "id" not in item:
                item["id"] = f"cand-{idx + 1:03d}"
            findings.append(ReviewFinding.model_validate(item))
        return findings
    except Exception as e:
        print(f"Error parsing candidate findings JSON: {e}", file=sys.stderr)
        sys.exit(1)


def _execute_review(args: argparse.Namespace) -> None:
    candidates: list[ReviewFinding] = []
    if args.candidates:
        candidates = _load_candidates_from_file(args.candidates)

    provider = args.provider
    if not provider:
        if candidates:
            provider = "none"
        else:
            print(
                "Error: Explicit provider selection is required (e.g. '--provider gemini', "
                "'--provider deterministic') or provide candidate findings via "
                "'--candidates <file>'.",
                file=sys.stderr,
            )
            sys.exit(1)

    if provider.lower() == "gemini":
        print(
            "VerifyCraft: Remote provider 'gemini' selected. "
            "Note: git diff and relevant surrounding code will be transmitted to the Gemini API.",
            file=sys.stderr,
        )

    policy = ReviewPolicy(
        timeout_seconds=args.timeout,
        fail_on_blocking=args.strict,
    )

    request = ReviewRequest(
        repo_path=args.path,
        base_revision=args.base,
        head_revision=args.head,
        requirements=args.requirements,
        candidate_findings=candidates,
        provider=provider,
        model=args.model,
        policy=policy,
    )

    engine = ReviewEngine()
    try:
        report = engine.review(request)
    except GitError as e:
        print(f"VerifyCraft Git Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ProviderError as e:
        print(f"VerifyCraft Provider Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"VerifyCraft Review Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        print(report.to_json(indent=2))
    elif args.format == "comments":
        print(report.to_conventional_comments())
    else:
        print(report.to_markdown())

    if args.strict and report.has_blocking_issues:
        sys.exit(2)
    sys.exit(0)


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if not args.subcommand:
        parser.print_help()
        sys.exit(0)

    if args.subcommand == "verify":
        _execute_verify(args)
    elif args.subcommand == "review":
        _execute_review(args)


if __name__ == "__main__":
    main()
