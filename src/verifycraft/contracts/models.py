"""Public typed contracts for VerifyCraft.

All models are versioned, JSON-serializable Pydantic v2 models.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Verdict(StrEnum):
    """Evidence status of a hypothesis.

    Verdicts represent verifiable evidence outcomes, not probabilistic model confidence.
    """

    SUPPORTED = "supported"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"
    BLOCKED = "blocked"


class ExecutionMode(StrEnum):
    """Permitted execution mode for verification."""

    DRY_RUN = "dry_run"
    FAKE = "fake"
    TRUSTED_LOCAL = "trusted_local"
    ISOLATED_CONTAINER = "isolated_container"


class CheckType(StrEnum):
    """Approved verification check types."""

    PYTEST = "pytest"
    STATIC = "static"
    FAKE = "fake"


class ExpectedObservation(BaseModel):
    """Explicit expected observation used to evaluate whether evidence supports or refutes a claim.

    A command exiting successfully is NOT automatically evidence supporting a hypothesis.
    Verdicts must be derived from matching actual observations against these explicit expectations.
    """

    model_config = ConfigDict(frozen=True)

    expected_exit_codes: list[int] = Field(
        default_factory=lambda: [1],
        description="Exit codes indicating expected behavior (e.g. [1] for expected test failure).",
    )
    match_patterns: list[str] = Field(
        default_factory=list,
        description="Patterns that must appear in stdout/stderr to support the claim.",
    )
    contradict_patterns: list[str] = Field(
        default_factory=list,
        description="Substrings or regex patterns that, if found, refute the claim.",
    )
    expected_outcome: Literal["fail", "pass", "error"] = Field(
        default="fail",
        description="Expected check outcome (e.g. 'fail' when verifying a regression).",
    )


class Hypothesis(BaseModel):
    """An explicit, testable claim regarding a code change or regression."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Unique identifier for this hypothesis.")
    claim: str = Field(description="Human-readable description of the claim.")
    failure_mechanism: str | None = Field(
        default=None,
        description="Proposed mechanism of failure (e.g. 'ZeroDivisionError on empty cart').",
    )
    affected_locations: list[str] = Field(
        default_factory=list,
        description="Source code or test locations implicated (e.g. ['src/pricing.py:42']).",
    )
    expected_observation: ExpectedObservation = Field(
        default_factory=ExpectedObservation,
        description="Explicit observation required to confirm or refute this hypothesis.",
    )


class CheckSpec(BaseModel):
    """Approved verification check specification.

    Arbitrary shell strings are strictly prohibited. Every check must be an approved type
    with bounded, validated arguments.
    """

    model_config = ConfigDict(frozen=True)

    check_id: str = Field(description="Unique identifier for this check specification.")
    check_type: CheckType = Field(
        default=CheckType.PYTEST,
        description="Approved verification runner type.",
    )
    target_path: str = Field(
        description="Relative path to test or target file (e.g. 'tests/test_regression.py').",
    )
    args: list[str] = Field(
        default_factory=list,
        description="Allowlisted arguments passed to runner (e.g. ['-k', 'test_negative']).",
    )
    env_overrides: dict[str, str] = Field(
        default_factory=dict,
        description="Approved environment variable overrides.",
    )

    @field_validator("target_path")
    @classmethod
    def validate_target_path(cls, path_str: str) -> str:
        """Ensure target_path is relative and does not escape workspace via traversal."""
        clean = path_str.strip()
        if not clean:
            raise ValueError("target_path must not be empty")
        clean_file = clean.split("::")[0].strip()
        is_abs = (
            Path(clean_file).is_absolute()
            or PureWindowsPath(clean_file).is_absolute()
            or PurePosixPath(clean_file).is_absolute()
            or clean_file.startswith(("/", "\\"))
            or bool(PureWindowsPath(clean_file).drive)
        )
        if is_abs:
            raise ValueError(f"target_path must be relative, not absolute: {path_str}")
        has_traversal = (
            ".." in Path(clean_file).parts
            or ".." in PureWindowsPath(clean_file).parts
            or ".." in PurePosixPath(clean_file).parts
        )
        if has_traversal:
            raise ValueError(f"target_path must not contain '..' path traversal: {path_str}")
        return clean

    @field_validator("args")
    @classmethod
    def validate_args_security(cls, args: list[str]) -> list[str]:
        """Prohibit dangerous flags that might escape check execution safety."""
        forbidden_substrings = [
            ";",
            "&&",
            "||",
            "|",
            "`",
            "$",
            "\n",
            "\r",
            "--override-ini",
            "-o",
            "--import-mode",
        ]
        for arg in args:
            for forbidden in forbidden_substrings:
                if forbidden in arg:
                    raise ValueError(f"Prohibited pattern '{forbidden}' in check argument: {arg}")
        return args


class ExecutionPolicy(BaseModel):
    """Policy governing verification execution boundaries, budgets, and security."""

    model_config = ConfigDict(frozen=True)

    mode: ExecutionMode = Field(
        default=ExecutionMode.DRY_RUN,
        description="Execution mode. Defaults to dry_run (non-executing) for safety.",
    )
    timeout_seconds: float = Field(
        default=30.0,
        ge=0.1,
        le=300.0,
        description="Maximum execution wall-clock time in seconds.",
    )
    max_output_bytes: int = Field(
        default=64 * 1024,
        ge=1024,
        le=5 * 1024 * 1024,
        description="Maximum captured output bytes before truncation.",
    )
    untrusted_code: bool = Field(
        default=False,
        description="Whether code originates from an untrusted source (e.g. public PR).",
    )
    allow_network: bool = Field(
        default=False,
        description=(
            "Whether network access is permitted. NOTE: In 'trusted_local' mode, "
            "this is an advisory constraint and does not enforce kernel-level network "
            "isolation. Network isolation requires 'isolated_container' mode."
        ),
    )
    allowed_check_types: list[CheckType] = Field(
        default_factory=lambda: [CheckType.PYTEST, CheckType.FAKE],
        description="Allowlist of approved check types.",
    )
    env_allowlist: list[str] = Field(
        default_factory=lambda: [
            "PATH",
            "PYTHONPATH",
            "PYTHONHOME",
            "SYSTEMROOT",
            "TMPDIR",
            "TEMP",
            "TMP",
            "LANG",
            "LC_ALL",
        ],
        description="Environment variable names permitted to pass to the runner process.",
    )


class ResourceUsage(BaseModel):
    """Execution resource accounting."""

    model_config = ConfigDict(frozen=True)

    wall_time_seconds: float = Field(default=0.0, ge=0.0)
    memory_bytes: int | None = Field(default=None, ge=0)
    attempts: int = Field(default=1, ge=0)
    output_bytes: int = Field(default=0, ge=0)


class Evidence(BaseModel):
    """Observable, structured evidence collected from a check execution."""

    model_config = ConfigDict(frozen=True)

    evidence_id: str = Field(description="Unique evidence identifier.")
    check_id: str = Field(description="ID of the CheckSpec that produced this evidence.")
    evidence_type: str = Field(description="Evidence source type (e.g. 'pytest_execution').")
    exit_code: int = Field(description="Process exit code.")
    stdout: str = Field(default="", description="Captured standard output.")
    stderr: str = Field(default="", description="Captured standard error.")
    output_truncated: bool = Field(
        default=False, description="Whether output was truncated due to size limits."
    )
    fingerprint: str = Field(
        description="Repository revision and diff fingerprint at time of collection."
    )
    collected_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when evidence was captured.",
    )
    resource_usage: ResourceUsage = Field(
        default_factory=ResourceUsage,
        description="Resource usage during evidence collection.",
    )


class Finding(BaseModel):
    """A claim evaluated against collected evidence."""

    model_config = ConfigDict(frozen=True)

    hypothesis_id: str = Field(description="ID of the hypothesis.")
    verdict: Verdict = Field(
        description="Evidence verdict: supported, refuted, inconclusive, or blocked."
    )
    reason: str = Field(description="Explanation of how the verdict was derived from evidence.")
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="IDs of evidence supporting or refuting this finding.",
    )


class VerificationInput(BaseModel):
    """Input payload for a verification request."""

    model_config = ConfigDict(frozen=True)

    repo_path: str = Field(description="Path to repository root.")
    revision: str = Field(default="HEAD", description="Repository revision or commit hash.")
    diff: str | None = Field(
        default=None, description="Unified diff representing changes under review."
    )
    hypothesis: Hypothesis = Field(description="The regression hypothesis to verify.")
    check_spec: CheckSpec = Field(description="Approved check to execute.")
    target_files: list[str] = Field(
        default_factory=list,
        description="Specific files targeted by this verification.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary caller-supplied metadata.",
    )

    @field_validator("repo_path")
    @classmethod
    def validate_repo_path(cls, path_str: str) -> str:
        """Ensure repo_path is not empty."""
        if not path_str or not path_str.strip():
            raise ValueError("repo_path must not be empty")
        return path_str


class VerificationResult(BaseModel):
    """Top-level result produced by the verification engine."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default="verifycraft.v0", description="Contract schema version.")
    verdict: Verdict = Field(description="Overall verification verdict.")
    findings: list[Finding] = Field(
        default_factory=list, description="Findings evaluated by the engine."
    )
    evidence: list[Evidence] = Field(
        default_factory=list, description="Evidence gathered during verification."
    )
    resource_usage: ResourceUsage = Field(
        default_factory=ResourceUsage,
        description="Aggregate resource usage.",
    )
    blocked_reason: str | None = Field(
        default=None,
        description="Reason verification was blocked, if applicable.",
    )
    inconclusive_reason: str | None = Field(
        default=None,
        description="Reason verification was inconclusive, if applicable.",
    )
    fingerprint: str = Field(description="Change fingerprint for which this result is valid.")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when result was produced.",
    )

    def to_json(self, indent: int | None = 2) -> str:
        """Serialize result to a versioned JSON string."""
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> VerificationResult:
        """Deserialize result from a JSON string."""
        return cls.model_validate_json(json_str)
