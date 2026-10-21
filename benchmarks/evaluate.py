"""Benchmark evaluation runner.

Compares VerifyCraft verification outcomes against an LLM-only prompt baseline
across canonical controlled Python/pytest benchmark fixtures.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from benchmarks.baseline import BaselineReviewer
from benchmarks.fixtures import load_benchmark_cases
from verifycraft import VerificationEngine


def run_benchmark(verbose: bool = True) -> dict[str, Any]:
    cases = load_benchmark_cases()
    engine = VerificationEngine()
    baseline = BaselineReviewer()

    results: list[dict[str, Any]] = []
    vc_correct = 0
    base_correct = 0
    vc_false_positives = 0
    base_false_positives = 0
    total_time = 0.0

    start_all = time.perf_counter()

    for case in cases:
        # Run VerifyCraft
        vc_res = engine.verify(
            case.verification_input,
            policy=case.policy,
            cached_evidence=case.cached_evidence,
        )
        total_time += vc_res.resource_usage.wall_time_seconds

        # Run Baseline
        base_verdict = baseline.predict(case.verification_input)

        vc_match = vc_res.verdict == case.ground_truth_verdict
        base_match = base_verdict == case.ground_truth_verdict

        if vc_match:
            vc_correct += 1
        if base_match:
            base_correct += 1

        # False positive: predicted SUPPORTED when ground truth is NOT supported
        if vc_res.verdict.value == "supported" and case.ground_truth_verdict.value != "supported":
            vc_false_positives += 1

        if base_verdict.value == "supported" and case.ground_truth_verdict.value != "supported":
            base_false_positives += 1

        results.append(
            {
                "case_id": case.case_id,
                "name": case.name,
                "ground_truth": case.ground_truth_verdict.value,
                "verifycraft_verdict": vc_res.verdict.value,
                "verifycraft_correct": vc_match,
                "baseline_verdict": base_verdict.value,
                "baseline_correct": base_match,
                "wall_time_seconds": vc_res.resource_usage.wall_time_seconds,
                "blocked_reason": vc_res.blocked_reason,
            }
        )

    elapsed_all = time.perf_counter() - start_all
    total = len(cases)
    vc_accuracy = round(vc_correct / total, 4) if total > 0 else 0.0
    base_accuracy = round(base_correct / total, 4) if total > 0 else 0.0

    summary = {
        "total_cases": total,
        "verifycraft": {
            "correct": vc_correct,
            "accuracy": vc_accuracy,
            "false_positives": vc_false_positives,
            "execution_time_seconds": round(total_time, 4),
        },
        "baseline": {
            "correct": base_correct,
            "accuracy": base_accuracy,
            "false_positives": base_false_positives,
        },
        "wall_time_seconds": round(elapsed_all, 4),
        "cases": results,
    }

    if verbose:
        print("=" * 80)
        print(" VerifyCraft Benchmark Evaluation (Stage 0 / Stage 1)")
        header = (
            f"{'Case ID':<32} {'Ground Truth':<14} {'VerifyCraft':<14} "
            f"{'Baseline':<12} {'Time (s)'}"
        )
        print(header)
        print("-" * 80)
        for r in results:
            vc_mark = "✓" if r["verifycraft_correct"] else "✗"
            base_mark = "✓" if r["baseline_correct"] else "✗"
            print(
                f"{r['case_id']:<32} "
                f"{r['ground_truth']:<14} "
                f"{r['verifycraft_verdict']} {vc_mark:<11} "
                f"{r['baseline_verdict']} {base_mark:<9} "
                f"{r['wall_time_seconds']:.4f}"
            )
        print("=" * 80)
        print(" Summary Metrics:")
        print(f"  Canonical Fixtures:           {total}")
        print(f"  Contract Invariant Matches:   {vc_correct}/{total}")
        print(f"  Baseline Matches:             {base_correct}/{total}")
        print(f"  VerifyCraft False Positives:  {vc_false_positives}")
        print(f"  Baseline False Positives:     {base_false_positives}")
        print(f"  Total Execution Time:         {elapsed_all:.4f}s")
        print("=" * 80)
        print("  NOTE: These canonical fixtures test deterministic contract invariants")
        print("  (regression support, refutation, signature matching, safety gates,")
        print("  and staleness detection), not empirical accuracy over unknown code.")
        print("=" * 80)

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run VerifyCraft canonical contract fixtures evaluation."
    )
    parser.add_argument("--json", action="store_true", help="Output JSON report.")
    args = parser.parse_args()

    report = run_benchmark(verbose=not args.json)

    if args.json:
        print(json.dumps(report, indent=2))

    # Exit 0 if VerifyCraft satisfied all canonical contract invariants
    sys.exit(0 if report["verifycraft"]["correct"] == report["total_cases"] else 1)


if __name__ == "__main__":
    main()
