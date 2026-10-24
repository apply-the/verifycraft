"""Central ReviewEngine orchestrating git acquisition, requirement analysis, and semantic review."""

from __future__ import annotations

import time
from typing import Any

from verifycraft.contracts.review import (
    ConventionalLabel,
    EvidenceStatus,
    FindingSeverity,
    ModelInfo,
    RequirementAssessment,
    RequirementStatus,
    ReviewFinding,
    ReviewReport,
    ReviewRequest,
    ReviewResourceUsage,
    ReviewStatus,
)
from verifycraft.review.counterexamples import CounterexampleChecker
from verifycraft.review.git import GitContextCollector
from verifycraft.review.providers.base import (
    ProviderConfigurationError,
    ReviewerModelAdapter,
)
from verifycraft.review.providers.deterministic import DeterministicReviewerAdapter
from verifycraft.review.providers.gemini import GeminiReviewerAdapter
from verifycraft.review.requirements import ParsedRequirement, RequirementsParser


class ReviewEngine:
    """Core code review engine providing parity across Python SDK and CLI."""

    def __init__(self, provider_adapter: ReviewerModelAdapter | None = None):
        self._injected_adapter = provider_adapter
        self.requirements_parser = RequirementsParser()
        self.counterexample_checker = CounterexampleChecker()

    def _get_adapter(self, provider_name: str) -> ReviewerModelAdapter:
        """Resolve and initialize the model provider adapter."""
        if self._injected_adapter is not None:
            return self._injected_adapter

        name = provider_name.lower().strip()
        if name in ("deterministic", "fake"):
            return DeterministicReviewerAdapter()
        elif name == "gemini":
            return GeminiReviewerAdapter()
        else:
            raise ProviderConfigurationError(
                f"Unsupported provider: '{provider_name}'. Supported: 'gemini', 'deterministic'."
            )

    def review(self, request: ReviewRequest) -> ReviewReport:
        """Execute a requirements-aware semantic code review against a Git revision pair."""
        start_time = time.monotonic()

        # 1. Validate and resolve reviewer model adapter early
        adapter = self._get_adapter(request.provider)

        # 2. Collect Git context
        collector = GitContextCollector(request.repo_path)
        context = collector.collect(
            base_revision=request.base_revision,
            head_revision=request.head_revision,
            explicit_diff=request.diff,
            max_files=request.policy.max_files,
            max_diff_lines=request.policy.max_diff_lines,
            max_context_bytes=request.policy.max_context_bytes,
        )

        # 3. Parse requirements
        parsed_reqs: list[ParsedRequirement] = []
        if request.requirements:
            parsed_reqs = self.requirements_parser.parse_all(
                request.requirements,
                repo_root=request.repo_path,
            )

        # 4. Prepare Prompt
        prompt = self._build_prompt(context=context, requirements=parsed_reqs)

        # 5. Invoke Reviewer Model
        provider_resp = adapter.review(
            prompt=prompt,
            timeout_seconds=request.policy.timeout_seconds,
        )

        # 5. Process and Validate Candidate Findings
        findings: list[ReviewFinding] = []
        counter = 1

        # Check candidate findings from provider response
        for raw in provider_resp.candidate_findings:
            validated_line, fallback_reason = collector.validate_line_anchor(
                file_path=raw.file_path,
                line=raw.line,
                changed_files=context.changed_files,
            )

            # Skip findings in files that were never changed or present
            if (
                raw.file_path not in context.changed_files
                and raw.file_path not in context.related_files
            ):
                context.omitted_scope.append(
                    f"Candidate finding on unknown file '{raw.file_path}' was discarded."
                )
                continue

            desc = raw.description
            if fallback_reason:
                desc = f"{desc}\n\n*(Note: {fallback_reason})*"

            # Resolve label safely
            try:
                label = ConventionalLabel(raw.label.lower())
            except ValueError:
                label = ConventionalLabel.ISSUE

            # Resolve severity safely
            try:
                severity = FindingSeverity(raw.severity.lower())
            except ValueError:
                severity = FindingSeverity.MEDIUM

            findings.append(
                ReviewFinding(
                    id=f"rev-{counter:03d}",
                    file_path=raw.file_path,
                    line=validated_line,
                    end_line=raw.end_line if validated_line is not None else None,
                    label=label,
                    decorations=raw.decorations,
                    subject=raw.subject,
                    description=desc,
                    severity=severity,
                    blocking=raw.blocking,
                    requirement_ids=raw.requirement_ids,
                    evidence_status=EvidenceStatus.MODEL_INFERRED,
                    review_status=ReviewStatus.REPORTED,
                    suggested_action=raw.suggested_action,
                )
            )
            counter += 1

        # Incorporate any candidate findings provided in the request
        for cand in request.candidate_findings:
            v_line, f_reason = collector.validate_line_anchor(
                file_path=cand.file_path,
                line=cand.line,
                changed_files=context.changed_files,
            )
            desc = cand.description
            if f_reason:
                desc = f"{desc}\n\n*(Note: {f_reason})*"

            findings.append(
                cand.model_copy(
                    update={
                        "id": f"rev-{counter:03d}",
                        "line": v_line,
                        "description": desc,
                    }
                )
            )
            counter += 1

        # 6. Apply Counterexample and Compensating Logic Validation
        findings, dismissal_notes = self.counterexample_checker.check_compensating_controls(
            findings=findings,
            context=context,
        )
        context.omitted_scope.extend(dismissal_notes)

        # 7. Deduplicate Findings
        deduped_findings: list[ReviewFinding] = []
        seen_keys: set[str] = set()
        for f in findings:
            key = f"{f.file_path}:{f.line}:{f.label}:{f.subject}"
            if key not in seen_keys:
                seen_keys.add(key)
                deduped_findings.append(f)

        # 8. Build Requirement Assessments
        assessments: list[RequirementAssessment] = []
        if parsed_reqs:
            model_assessments_map = {
                a.requirement_id.upper(): a for a in provider_resp.requirement_assessments
            }
            for req in parsed_reqs:
                req_id_clean = req.id.upper()
                if req_id_clean in model_assessments_map:
                    raw_a = model_assessments_map[req_id_clean]
                    try:
                        status = RequirementStatus(raw_a.status.lower())
                    except ValueError:
                        status = RequirementStatus.UNCLEAR
                    explanation = raw_a.explanation
                else:
                    # If compensating control dismissed the issue, check if it was this requirement
                    if any(req.id in note for note in dismissal_notes):
                        status = RequirementStatus.APPEARS_SATISFIED
                        explanation = "Requirement satisfied via compensating controls in context."
                    elif any(req.id in f.requirement_ids for f in deduped_findings):
                        status = RequirementStatus.NOT_SATISFIED
                        explanation = (
                            "Defect or omission in changed code violates this requirement."
                        )
                    else:
                        status = RequirementStatus.APPEARS_SATISFIED
                        explanation = (
                            "No requirement violations identified in changed implementation."
                        )

                assessments.append(
                    RequirementAssessment(
                        requirement_id=req.id,
                        source_file=req.source_file,
                        text=req.text,
                        status=status,
                        explanation=explanation,
                        related_finding_ids=[
                            f.id for f in deduped_findings if req.id in f.requirement_ids
                        ],
                    )
                )
        else:
            # Requirements absent: mark coverage as not assessed
            assessments.append(
                RequirementAssessment(
                    requirement_id="NONE",
                    source_file=None,
                    text="No requirement document supplied.",
                    status=RequirementStatus.NOT_ASSESSED,
                    explanation=(
                        "No requirements document was supplied; "
                        "standard code quality review was performed."
                    ),
                )
            )

        # 9. Compute Resource Usage
        wall_time = time.monotonic() - start_time
        resource_usage = ReviewResourceUsage(
            files_reviewed=len(context.changed_files),
            diff_lines=context.diff_lines_count,
            context_bytes=context.total_context_bytes,
            wall_time_seconds=round(wall_time, 4),
            tokens_used=provider_resp.tokens_used,
        )

        model_info = ModelInfo(
            provider=request.provider,
            model_name=getattr(adapter, "model_name", "deterministic"),
        )

        return ReviewReport(
            repo_path=context.repo_path,
            base_revision=context.base_revision,
            head_revision=context.head_revision,
            base_commit=context.base_commit,
            head_commit=context.head_commit,
            findings=deduped_findings,
            requirement_assessments=assessments,
            summary=provider_resp.summary,
            omitted_scope=context.omitted_scope,
            resource_usage=resource_usage,
            model_info=model_info,
        )

    def _build_prompt(
        self,
        context: Any,
        requirements: list[ParsedRequirement],
    ) -> str:
        """Construct the prompt sent to the reviewer model adapter."""
        parts = [
            "# Code Review Request",
            "",
            f"Repository Base: {context.base_revision} ({context.base_commit[:8]})",
            f"Repository Head: {context.head_revision} ({context.head_commit[:8]})",
            "",
            "## Unified Diff",
            "```diff",
            context.raw_diff or "(No changes in diff)",
            "```",
            "",
        ]

        if context.related_files:
            parts.extend(
                [
                    "## Relevant Surrounding Code & Related Helpers",
                    "*(Compensating controls, imported helpers, and existing tests)*",
                    "",
                ]
            )
            for path, content in context.related_files.items():
                parts.extend(
                    [
                        f"### Context File: `{path}`",
                        "```python",
                        content,
                        "```",
                        "",
                    ]
                )

        if requirements:
            parts.extend(
                [
                    "## Explicit Requirements",
                    "",
                ]
            )
            for req in requirements:
                src = f" ({req.source_file}:{req.line_number})" if req.source_file else ""
                parts.append(f"- **[{req.id}]**{src}: {req.text}")
            parts.append("")

        return "\n".join(parts)
