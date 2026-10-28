"""Base protocol and models for code review LLM provider adapters."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ProviderError(Exception):
    """Base exception for model provider errors."""


class ProviderConfigurationError(ProviderError):
    """Raised when provider configuration or credentials are missing."""


class ProviderCallError(ProviderError):
    """Raised when an API call to a provider fails."""


class RawCandidateFinding(BaseModel):
    """A raw candidate finding returned by a reviewer model."""

    file_path: str = Field(description="Relative path to file.")
    line: int | None = Field(default=None, description="1-indexed line number in head.")
    end_line: int | None = Field(default=None, description="Optional ending line number.")
    label: str = Field(default="issue", description="Conventional comment label.")
    decorations: list[str] = Field(
        default_factory=list, description="Decorations like ['blocking']."
    )
    subject: str = Field(description="Concise subject line.")
    description: str = Field(description="Detailed explanation and impact.")
    severity: str = Field(
        default="medium", description="Severity: low, medium, high, critical, info."
    )
    blocking: bool = Field(default=False, description="Whether to recommend blocking merge.")
    requirement_ids: list[str] = Field(
        default_factory=list, description="IDs of referenced requirements."
    )
    suggested_action: str | None = Field(default=None, description="Suggested remedy.")


class RawRequirementAssessment(BaseModel):
    """A raw requirement assessment returned by a reviewer model."""

    requirement_id: str = Field(description="ID of the requirement.")
    status: str = Field(
        description="Assessment status enum value.",
    )
    explanation: str = Field(description="Explanation with implementation citations.")
    related_locations: list[str] = Field(
        default_factory=list, description="Implicated code locations."
    )


class ProviderResponse(BaseModel):
    """Structured response returned by a provider adapter."""

    summary: str = Field(description="Concise executive summary of changes.")
    candidate_findings: list[RawCandidateFinding] = Field(default_factory=list)
    requirement_assessments: list[RawRequirementAssessment] = Field(default_factory=list)
    tokens_used: int | None = Field(default=None)
    prompt_tokens: int | None = Field(default=None)
    output_tokens: int | None = Field(default=None)


@runtime_checkable
class ReviewerModelAdapter(Protocol):
    """Protocol for model provider adapters."""

    def review(
        self,
        prompt: str,
        system_instruction: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> ProviderResponse:
        """Execute a review prompt and return a structured ProviderResponse."""
        ...
