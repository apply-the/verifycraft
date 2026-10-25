"""Data models and loader for the realistic review evaluation dataset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExpectedIssue(BaseModel):
    """Ground truth specification for a known defect."""

    model_config = ConfigDict(frozen=True)

    file_path: str
    line_range: list[int] = Field(default_factory=list)
    subject_contains: str
    label: str = "issue"
    blocking: bool = True
    severity: str = "high"
    rationale: str = ""


class GroundTruth(BaseModel):
    """Human-reviewed ground truth for an evaluation case."""

    model_config = ConfigDict(frozen=True)

    expected_verdict: Literal["has_defects", "clean", "unclear"]
    expected_issues: list[ExpectedIssue] = Field(default_factory=list)
    forbidden_false_positives: list[str] = Field(default_factory=list)
    expected_requirements: dict[str, str] = Field(default_factory=dict)
    acceptable_uncertainties: list[str] = Field(default_factory=list)
    multi_file_context_needed: bool = False
    rationale: str = ""


class ReviewRequirement(BaseModel):
    """An explicit requirement in the evaluation dataset."""

    model_config = ConfigDict(frozen=True)

    id: str
    source_file: str
    line_number: int
    text: str


class ReviewEvalCase(BaseModel):
    """A realistic, versioned code review evaluation case."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    name: str
    split: Literal["dev", "held_out"]
    category: str
    version: str = "1.0.0"
    provenance: str
    base_commit: str
    head_commit: str
    is_working_tree: bool = False
    changed_files: dict[str, str]
    raw_diff: str
    related_files: dict[str, str] = Field(default_factory=dict)
    requirements: list[ReviewRequirement] = Field(default_factory=list)
    ground_truth: GroundTruth


def load_review_eval_dataset(
    dataset_path: Path | str | None = None,
    split: Literal["dev", "held_out", "all"] = "all",
) -> list[ReviewEvalCase]:
    """Load and validate the review evaluation dataset."""
    if dataset_path is None:
        dataset_path = Path(__file__).parent / "data" / "review_eval" / "cases.json"
    else:
        dataset_path = Path(dataset_path)

    raw_data = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases = [ReviewEvalCase.model_validate(c) for c in raw_data]

    if split != "all":
        cases = [c for c in cases if c.split == split]

    return cases
