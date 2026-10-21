"""Verdict evaluation logic for VerifyCraft.

Derives explicit verdicts (supported, refuted, inconclusive, blocked) by comparing
observable evidence against explicit expected observations.
"""

from __future__ import annotations

import re

from verifycraft.contracts.models import (
    Evidence,
    Finding,
    Hypothesis,
    Verdict,
)


def _pattern_matches(pattern: str, text: str) -> bool:
    """Check if pattern matches text either as regex or as literal substring."""
    try:
        return bool(re.search(pattern, text))
    except re.error:
        return pattern in text


def evaluate_hypothesis(hypothesis: Hypothesis, evidence: Evidence) -> Finding:
    """Evaluate whether collected evidence supports, refutes, or is inconclusive for a hypothesis.

    INVARIANTS:
    1. A command exiting 0 is NOT automatically evidence supporting a hypothesis.
    2. A passing test contradicts a hypothesis that predicted a regression failure.
    3. An unexpected or missing error signature yields inconclusive rather than false confirmation.
       Exit code 1 alone without a matching diagnostic signature cannot support a regression claim.
    4. Timeouts and incomplete executions yield inconclusive with honest representation.
    """
    expected = hypothesis.expected_observation
    combined_output = f"{evidence.stdout}\n{evidence.stderr}"

    # Handle process timeout
    if evidence.exit_code == 124:
        return Finding(
            hypothesis_id=hypothesis.id,
            verdict=Verdict.INCONCLUSIVE,
            reason=(
                f"Execution timed out ({evidence.resource_usage.wall_time_seconds:.2f}s); "
                f"insufficient evidence to evaluate hypothesis."
            ),
            evidence_ids=[evidence.evidence_id],
        )

    # 1. Check for explicit contradict patterns first
    for pattern in expected.contradict_patterns:
        if _pattern_matches(pattern, combined_output):
            return Finding(
                hypothesis_id=hypothesis.id,
                verdict=Verdict.REFUTED,
                reason=f"Evidence matches explicit contradict pattern '{pattern}'.",
                evidence_ids=[evidence.evidence_id],
            )

    # 2. Check exit code
    exit_code_matched = evidence.exit_code in expected.expected_exit_codes

    # 3. Derive required diagnostic signature
    required_patterns = list(expected.match_patterns)
    if not required_patterns and hypothesis.failure_mechanism:
        required_patterns.append(hypothesis.failure_mechanism)

    missing_patterns: list[str] = [
        p for p in required_patterns if not _pattern_matches(p, combined_output)
    ]
    all_patterns_matched = len(missing_patterns) == 0

    # Case A: Regression hypothesis expected a failure or error
    if expected.expected_outcome in ("fail", "error"):
        if evidence.exit_code == 0:
            # Check passed cleanly, contradicting the predicted failure
            return Finding(
                hypothesis_id=hypothesis.id,
                verdict=Verdict.REFUTED,
                reason="Check passed (exit code 0), contradicting predicted failure.",
                evidence_ids=[evidence.evidence_id],
            )

        if not exit_code_matched:
            return Finding(
                hypothesis_id=hypothesis.id,
                verdict=Verdict.INCONCLUSIVE,
                reason=(
                    f"Check exited with unexpected code {evidence.exit_code} "
                    f"(expected {expected.expected_exit_codes})."
                ),
                evidence_ids=[evidence.evidence_id],
            )

        # Invariant: exit code alone without diagnostic pattern cannot prove specific regression
        if not required_patterns:
            return Finding(
                hypothesis_id=hypothesis.id,
                verdict=Verdict.INCONCLUSIVE,
                reason=(
                    f"Check exited with code {evidence.exit_code}, but no diagnostic signature "
                    f"or failure mechanism was specified to confirm this specific hypothesis."
                ),
                evidence_ids=[evidence.evidence_id],
            )

        outcome_label = "failure" if expected.expected_outcome == "fail" else "error"
        if all_patterns_matched:
            return Finding(
                hypothesis_id=hypothesis.id,
                verdict=Verdict.SUPPORTED,
                reason=(
                    f"Observed expected {outcome_label} (exit code {evidence.exit_code}) "
                    f"and verified expected diagnostic signature."
                ),
                evidence_ids=[evidence.evidence_id],
            )

        # Exit code matched, but expected diagnostic signature was missing
        if evidence.output_truncated:
            return Finding(
                hypothesis_id=hypothesis.id,
                verdict=Verdict.INCONCLUSIVE,
                reason=(
                    f"Check failed (exit code {evidence.exit_code}), but output was truncated "
                    f"and missing expected signature {missing_patterns}."
                ),
                evidence_ids=[evidence.evidence_id],
            )
        return Finding(
            hypothesis_id=hypothesis.id,
            verdict=Verdict.INCONCLUSIVE,
            reason=(
                f"Check failed (exit code {evidence.exit_code}), but did not produce "
                f"expected diagnostic signature {missing_patterns}."
            ),
            evidence_ids=[evidence.evidence_id],
        )

    # Case B: Hypothesis expected a passing outcome (e.g. exit code 0)
    elif expected.expected_outcome == "pass":
        if exit_code_matched and all_patterns_matched:
            return Finding(
                hypothesis_id=hypothesis.id,
                verdict=Verdict.SUPPORTED,
                reason="Check passed cleanly as expected.",
                evidence_ids=[evidence.evidence_id],
            )

        if evidence.exit_code != 0:
            return Finding(
                hypothesis_id=hypothesis.id,
                verdict=Verdict.REFUTED,
                reason=f"Check failed with exit {evidence.exit_code}, contradicting expected pass.",
                evidence_ids=[evidence.evidence_id],
            )

        return Finding(
            hypothesis_id=hypothesis.id,
            verdict=Verdict.INCONCLUSIVE,
            reason=f"Missing expected output patterns: {missing_patterns}.",
            evidence_ids=[evidence.evidence_id],
        )

    # Fallback for unhandled configuration
    return Finding(
        hypothesis_id=hypothesis.id,
        verdict=Verdict.INCONCLUSIVE,
        reason="Evidence could not be decisively categorized against expected observation.",
        evidence_ids=[evidence.evidence_id],
    )
