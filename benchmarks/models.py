"""Data models for VerifyCraft benchmarks."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from verifycraft.contracts import (
    Evidence,
    ExecutionPolicy,
    Verdict,
    VerificationInput,
)


class BenchmarkCase(BaseModel):
    """A benchmark case with ground truth."""

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(description="Unique benchmark fixture identifier.")
    name: str = Field(description="Human-readable title.")
    description: str = Field(description="Fixture narrative and scenario.")
    ground_truth_verdict: Verdict = Field(description="Authoritative expected verdict.")
    verification_input: VerificationInput = Field(description="Input payload provided to engine.")
    policy: ExecutionPolicy = Field(description="Execution policy used for this case.")
    cached_evidence: Sequence[Evidence] | None = Field(
        default=None,
        description="Optional cached evidence (e.g. for testing staleness).",
    )
