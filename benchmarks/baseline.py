"""Prompt-only baseline reviewer for benchmark comparisons.

Simulates an LLM code reviewer that only evaluates diffs and hypothesis text
without test execution, environment isolation, or fingerprint verification.
"""

from __future__ import annotations

from verifycraft.contracts import (
    Verdict,
    VerificationInput,
)


class BaselineReviewer:
    """An ungrounded, prompt-based LLM reviewer baseline.

    This baseline models an LLM reviewing a pull request diff and hypothesis without
    an execution engine. Without executing pytest or checking environment policies,
    it relies purely on textual plausibility:
    - If the diff and hypothesis mention common error patterns (e.g. "TypeError",
      "regression", "error"), it assumes the hypothesis is true (verdict: supported).
    - It cannot execute checks, so it cannot refute claims contradicted by tests.
    - It is blind to execution safety policies and container requirements.
    - It cannot detect stale evidence fingerprints.
    """

    def __init__(self, name: str = "LLM-PromptOnly-Reviewer") -> None:
        self.name = name

    def predict(self, verification_input: VerificationInput) -> Verdict:
        """Predict a verdict based purely on text heuristics without execution."""
        claim = verification_input.hypothesis.claim.lower()
        diff = (verification_input.diff or "").lower()

        # Heuristic 1: If hypothesis mentions regression/error and diff is non-empty,
        # an ungrounded LLM typically confirms the hypothesis (confirmation bias).
        keywords = ["error", "regression", "typeerror", "zerodivision", "fail", "null"]
        if any(kw in claim for kw in keywords) and diff:
            return Verdict.SUPPORTED

        if "timeout" in claim:
            return Verdict.SUPPORTED

        return Verdict.INCONCLUSIVE
