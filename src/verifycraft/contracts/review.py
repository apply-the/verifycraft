"""Public typed contracts for VerifyCraft code review.

All models are versioned, JSON-serializable Pydantic v2 models.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConventionalLabel(StrEnum):
    """Canonical and supported Conventional Comments labels.

    Per https://conventionalcomments.org/:
    Canonical standard: praise, nitpick, suggestion, issue, todo, question, thought.
    Optional extensions: chore, note.
    """

    PRAISE = "praise"
    NITPICK = "nitpick"
    SUGGESTION = "suggestion"
    ISSUE = "issue"
    TODO = "todo"
    QUESTION = "question"
    THOUGHT = "thought"
    CHORE = "chore"
    NOTE = "note"


class FindingSeverity(StrEnum):
    """Severity of a review finding (distinct from whether it is merge-blocking)."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReviewStatus(StrEnum):
    """Current triage/assessment status of a candidate finding."""

    CANDIDATE = "candidate"
    REPORTED = "reported"
    DISMISSED = "dismissed"
    NEEDS_CLARIFICATION = "needs_clarification"


class EvidenceStatus(StrEnum):
    """Grounding status of the finding (distinguishes model inference from verified execution)."""

    UNVERIFIED = "unverified"
    STATIC_ANALYZED = "static_analyzed"
    MODEL_INFERRED = "model_inferred"
    EMPIRICALLY_VERIFIED = "empirically_verified"


class RequirementStatus(StrEnum):
    """Assessment status of a requirement evaluated against implementation and context."""

    APPEARS_SATISFIED = "appears_satisfied"
    PARTIALLY_SATISFIED = "partially_satisfied"
    NOT_SATISFIED = "not_satisfied"
    UNCLEAR = "unclear"
    NOT_ASSESSED = "not_assessed"


class ReviewFinding(BaseModel):
    """A structured code review finding anchored to a changed location."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Unique finding identifier (e.g. 'rev-auth-01').")
    file_path: str = Field(description="Relative path of the reviewed file.")
    line: int | None = Field(
        default=None,
        ge=1,
        description="1-indexed line number in head revision. None if file-level.",
    )
    end_line: int | None = Field(
        default=None,
        ge=1,
        description="Optional ending line number for multi-line ranges.",
    )
    label: ConventionalLabel = Field(
        default=ConventionalLabel.ISSUE,
        description="Conventional Comment label.",
    )
    decorations: list[str] = Field(
        default_factory=list,
        description="Decorations such as ['blocking'] or ['non-blocking'].",
    )
    subject: str = Field(
        description="Concise, actionable subject line for the conventional comment.",
    )
    description: str = Field(
        description="Detailed explanation of observed behavior, risk, or context.",
    )
    severity: FindingSeverity = Field(
        default=FindingSeverity.MEDIUM,
        description="Severity level of the finding.",
    )
    blocking: bool = Field(
        default=False,
        description="Whether this finding should block merge/progression.",
    )
    requirement_ids: list[str] = Field(
        default_factory=list,
        description="Associated requirement identifiers (e.g. ['REQ-AUTH-01']).",
    )
    evidence_status: EvidenceStatus = Field(
        default=EvidenceStatus.MODEL_INFERRED,
        description="How this finding was grounded.",
    )
    review_status: ReviewStatus = Field(
        default=ReviewStatus.REPORTED,
        description="Review triage status.",
    )
    suggested_action: str | None = Field(
        default=None,
        description="Concrete suggestion or fix to address the finding.",
    )
    dedup_key: str | None = Field(
        default=None,
        description="Deduplication key to avoid redundant findings.",
    )

    @field_validator("file_path")
    @classmethod
    def validate_file_path(cls, path_str: str) -> str:
        """Ensure file_path is relative and does not use traversal."""
        clean = path_str.strip()
        if not clean:
            raise ValueError("file_path must not be empty")
        is_abs = (
            Path(clean).is_absolute()
            or PureWindowsPath(clean).is_absolute()
            or PurePosixPath(clean).is_absolute()
            or clean.startswith(("/", "\\"))
            or bool(PureWindowsPath(clean).drive)
        )
        if is_abs:
            raise ValueError(f"file_path must be relative: {path_str}")
        has_traversal = (
            ".." in Path(clean).parts
            or ".." in PureWindowsPath(clean).parts
            or ".." in PurePosixPath(clean).parts
        )
        if has_traversal:
            raise ValueError(f"file_path must not contain '..' traversal: {path_str}")
        return clean

    def format_conventional_comment(self) -> str:
        """Format finding conforming to https://conventionalcomments.org/."""
        decorations_str = ""
        if self.decorations:
            decorations_str = " (" + ", ".join(self.decorations) + ")"
        elif self.blocking:
            decorations_str = " (blocking)"
        elif self.label in (
            ConventionalLabel.NITPICK,
            ConventionalLabel.PRAISE,
            ConventionalLabel.THOUGHT,
        ):
            decorations_str = " (non-blocking)"

        header = f"{self.label.value}{decorations_str}: {self.subject}"

        lines = [header, ""]
        if self.description:
            lines.append(self.description)

        details = []
        if self.requirement_ids:
            reqs = ", ".join(self.requirement_ids)
            details.append(f"- **Requirement:** {reqs}")
        if self.suggested_action:
            details.append(f"- **Suggested Action:** {self.suggested_action}")
        if self.evidence_status != EvidenceStatus.UNVERIFIED:
            details.append(f"- **Grounding:** {self.evidence_status.value}")

        if details:
            lines.append("")
            lines.extend(details)

        return "\n".join(lines)


class RequirementAssessment(BaseModel):
    """Assessment of an explicit requirement against code changes and context."""

    model_config = ConfigDict(frozen=True)

    requirement_id: str = Field(description="Identifier of the requirement (e.g. 'REQ-AUTH-01').")
    source_file: str | None = Field(
        default=None,
        description="Path to requirements source document.",
    )
    text: str = Field(description="Exact statement or summary of the requirement.")
    status: RequirementStatus = Field(
        description="Assessment outcome status enum.",
    )
    explanation: str = Field(
        description="Explanation referencing code context or identifying missing compliance.",
    )
    related_finding_ids: list[str] = Field(
        default_factory=list,
        description="IDs of ReviewFindings associated with this requirement.",
    )
    related_locations: list[str] = Field(
        default_factory=list,
        description="Code locations relevant to this requirement assessment.",
    )


class ReviewPolicy(BaseModel):
    """Resource bounds and policy for semantic code review."""

    model_config = ConfigDict(frozen=True)

    max_files: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of changed/context files to inspect.",
    )
    max_diff_lines: int = Field(
        default=2500,
        ge=10,
        le=20000,
        description="Maximum total diff lines to analyze.",
    )
    max_context_bytes: int = Field(
        default=128 * 1024,
        ge=1024,
        le=2 * 1024 * 1024,
        description="Maximum surrounding context in bytes.",
    )
    timeout_seconds: float = Field(
        default=60.0,
        ge=1.0,
        le=300.0,
        description="Maximum wall-clock execution time in seconds.",
    )
    fail_on_blocking: bool = Field(
        default=False,
        description="Return non-zero exit code if blocking issues exist.",
    )


class ReviewResourceUsage(BaseModel):
    """Resource usage tracking for a review run."""

    model_config = ConfigDict(frozen=True)

    files_reviewed: int = Field(default=0, ge=0)
    diff_lines: int = Field(default=0, ge=0)
    context_bytes: int = Field(default=0, ge=0)
    wall_time_seconds: float = Field(default=0.0, ge=0.0)
    tokens_used: int | None = Field(default=None, ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class ModelInfo(BaseModel):
    """Metadata about the model provider and configuration used for review."""

    model_config = ConfigDict(frozen=True)

    provider: str = Field(description="Provider identifier (e.g. 'gemini', 'deterministic').")
    model_name: str = Field(default="default", description="Model identifier.")


class ReviewRequest(BaseModel):
    """Input payload requesting a code review."""

    model_config = ConfigDict(frozen=True)

    repo_path: str = Field(
        default=".",
        description="Path to repository root.",
    )
    base_revision: str = Field(
        default="main",
        description="Base git reference or commit SHA.",
    )
    head_revision: str = Field(
        default="HEAD",
        description="Head git reference or commit SHA.",
    )
    diff: str | None = Field(
        default=None,
        description="Optional explicitly supplied unified diff string.",
    )
    requirements: list[str] = Field(
        default_factory=list,
        description="Paths to requirement documents or explicit requirement strings.",
    )
    provider: str = Field(
        default="gemini",
        description=(
            "Reviewer provider name ('gemini', 'deterministic', 'fake', 'none', 'model-free')."
        ),
    )
    model: str | None = Field(
        default=None,
        description="Optional model identifier override (e.g. 'gemini-2.5-pro').",
    )
    policy: ReviewPolicy = Field(
        default_factory=ReviewPolicy,
        description="Execution bounds and review policy.",
    )
    candidate_findings: list[ReviewFinding] = Field(
        default_factory=list,
        description="Externally supplied candidate findings (for host agent candidate ingestion).",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary caller metadata.",
    )

    @field_validator("repo_path")
    @classmethod
    def validate_repo_path(cls, path_str: str) -> str:
        """Ensure repo_path is not empty."""
        if not path_str or not path_str.strip():
            raise ValueError("repo_path must not be empty")
        return path_str


class ReviewReport(BaseModel):
    """Top-level report produced by ReviewEngine."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(
        default="verifycraft.review.v0",
        description="Review contract schema version.",
    )
    repo_path: str = Field(description="Target repository path.")
    base_revision: str = Field(description="Base revision name.")
    head_revision: str = Field(description="Head revision name.")
    base_commit: str | None = Field(default=None, description="Resolved base commit hash.")
    head_commit: str | None = Field(default=None, description="Resolved head commit hash.")
    is_working_tree: bool = Field(
        default=False,
        description="Whether the head revision inspected uncommitted working tree state.",
    )
    findings: list[ReviewFinding] = Field(
        default_factory=list,
        description="Actionable review findings.",
    )
    requirement_assessments: list[RequirementAssessment] = Field(
        default_factory=list,
        description="Per-requirement compliance assessments.",
    )
    summary: str = Field(description="Concise executive summary of the review.")
    omitted_scope: list[str] = Field(
        default_factory=list,
        description="Files or sections omitted due to size, type, or budget limits.",
    )
    resource_usage: ReviewResourceUsage = Field(
        default_factory=ReviewResourceUsage,
        description="Resource usage statistics.",
    )
    model_info: ModelInfo | None = Field(
        default=None,
        description="Model provider metadata if a provider was used.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when review was completed.",
    )

    @property
    def has_blocking_issues(self) -> bool:
        """Whether any finding has blocking=True."""
        return any(f.blocking for f in self.findings)

    def to_conventional_comments(self) -> str:
        """Format all findings as a sequence of Conventional Comments."""
        if not self.findings:
            return (
                f"### Review Summary\n\n{self.summary}\n\n"
                "*(No issues or actionable findings identified.)*"
            )

        blocks = [f"### Review Summary\n\n{self.summary}\n"]
        for finding in self.findings:
            loc = finding.file_path
            if finding.line is not None:
                if finding.end_line and finding.end_line != finding.line:
                    loc = f"{loc}:{finding.line}-{finding.end_line}"
                else:
                    loc = f"{loc}:{finding.line}"
            else:
                loc = f"{loc} (file-level)"

            blocks.append(f"### {loc}\n\n{finding.format_conventional_comment()}\n")

        return "\n".join(blocks).strip()

    def to_markdown(self) -> str:
        """Render complete Markdown report with summary, requirements, and comments."""
        base_str = self.base_commit[:8] if self.base_commit else "unresolved"
        head_str = self.head_commit[:8] if self.head_commit else "unresolved"
        blocking_cnt = sum(1 for f in self.findings if f.blocking)
        sections = [
            "# VerifyCraft Code Review Report",
            "",
            f"- **Base:** `{self.base_revision}` ({base_str})",
            f"- **Head:** `{self.head_revision}` ({head_str})",
            f"- **Files Reviewed:** {self.resource_usage.files_reviewed}",
            f"- **Findings:** {len(self.findings)} ({blocking_cnt} blocking)",
            "",
            "## Summary",
            "",
            self.summary,
            "",
        ]

        if self.requirement_assessments:
            sections.extend(
                [
                    "## Requirements Assessment",
                    "",
                    "| Requirement ID | Status | Explanation | Source |",
                    "| :--- | :--- | :--- | :--- |",
                ]
            )
            for req in self.requirement_assessments:
                src = req.source_file or "inline"
                sections.append(
                    f"| `{req.requirement_id}` | `{req.status.value}` | "
                    f"{req.explanation} | `{src}` |"
                )
            sections.append("")

        if self.omitted_scope:
            sections.extend(
                [
                    "## Omitted Scope",
                    (
                        "*(Notice: Review scope is partial/incomplete due to budget limits, "
                        "untracked files, or context boundaries; this review is not exhaustive.)*"
                    ),
                    "",
                    "Contexts omitted or challenged during review:",
                    "",
                ]
            )
            for item in self.omitted_scope:
                sections.append(f"- {item}")
            sections.append("")

        sections.extend(
            [
                "## Conventional Comments",
                "",
                self.to_conventional_comments(),
            ]
        )

        return "\n".join(sections).strip()

    def to_json(self, indent: int | None = 2) -> str:
        """Serialize report to a versioned JSON string."""
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> ReviewReport:
        """Deserialize report from a JSON string."""
        return cls.model_validate_json(json_str)
