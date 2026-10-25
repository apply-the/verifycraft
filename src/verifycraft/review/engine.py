"""Central ReviewEngine orchestrating git acquisition, requirement analysis, and semantic review."""

from __future__ import annotations

import re
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
from verifycraft.review.git import GitContextCollector, GitReviewContext
from verifycraft.review.providers.base import (
    ProviderConfigurationError,
    ProviderResponse,
    RawCandidateFinding,
    RawRequirementAssessment,
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

    def _get_adapter(
        self,
        provider_name: str,
        model: str | None = None,
    ) -> ReviewerModelAdapter | None:
        name = provider_name.lower().strip()
        if name in ("none", "model-free", "agent"):
            return None

        if self._injected_adapter is not None:
            return self._injected_adapter
        elif name in ("deterministic", "fake"):
            return DeterministicReviewerAdapter()
        elif name == "gemini":
            return GeminiReviewerAdapter(model_name=model)
        else:
            raise ProviderConfigurationError(
                f"Unsupported provider: '{provider_name}'. "
                "Supported: 'gemini', 'deterministic', 'none'."
            )

    def review(self, request: ReviewRequest) -> ReviewReport:
        """Execute a requirements-aware semantic code review against a Git revision pair."""
        start_time = time.monotonic()

        # 1. Validate and resolve reviewer model adapter early
        adapter = self._get_adapter(request.provider, model=request.model)

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

        # 4. Invoke Reviewer Model or Model-Free Ingestion
        if adapter is None:
            provider_resp = ProviderResponse(
                summary=(
                    f"Model-free review processed {len(request.candidate_findings)} "
                    "candidate finding(s)."
                ),
                candidate_findings=[],
                requirement_assessments=[],
                tokens_used=0,
            )
        else:
            prompt = self._build_prompt(context=context, requirements=parsed_reqs)
            provider_resp = adapter.review(
                prompt=prompt,
                timeout_seconds=request.policy.timeout_seconds,
            )

        # 5. Process and Validate Candidate Findings
        findings = self._process_candidate_findings(
            provider_resp=provider_resp,
            request=request,
            context=context,
            collector=collector,
        )

        # 6. Apply Counterexample and Compensating Logic Validation
        findings, dismissal_notes = self.counterexample_checker.check_compensating_controls(
            findings=findings,
            context=context,
        )
        context.omitted_scope.extend(dismissal_notes)

        # 7. Deduplicate Findings
        deduped_findings = self._deduplicate_findings(findings)

        # 8. Build Requirement Assessments
        assessments = self._build_requirement_assessments(
            parsed_reqs=parsed_reqs,
            provider_resp=provider_resp,
            deduped_findings=deduped_findings,
            dismissal_notes=dismissal_notes,
            context=context,
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

        resolved_model_name = getattr(
            adapter,
            "model_name",
            request.model or ("model-free" if adapter is None else "deterministic"),
        )
        model_info = ModelInfo(
            provider=request.provider,
            model_name=resolved_model_name,
        )

        return ReviewReport(
            repo_path=context.repo_path,
            base_revision=context.base_revision,
            head_revision=context.head_revision,
            base_commit=context.base_commit,
            head_commit=context.head_commit,
            is_working_tree=context.is_working_tree,
            findings=deduped_findings,
            requirement_assessments=assessments,
            summary=provider_resp.summary,
            omitted_scope=context.omitted_scope,
            resource_usage=resource_usage,
            model_info=model_info,
        )

    def _create_finding_from_raw(
        self,
        raw: RawCandidateFinding,
        counter: int,
        context: GitReviewContext,
        collector: GitContextCollector,
    ) -> ReviewFinding | None:
        validated_line, fallback_reason = collector.validate_line_anchor(
            file_path=raw.file_path,
            line=raw.line,
            changed_files=context.changed_files,
        )

        if (
            raw.file_path not in context.changed_files
            and raw.file_path not in context.related_files
        ):
            context.omitted_scope.append(
                f"Candidate finding on unknown file '{raw.file_path}' was discarded."
            )
            return None

        desc = raw.description
        if fallback_reason:
            desc = f"{desc}\n\n*(Note: {fallback_reason})*"

        try:
            label = ConventionalLabel(raw.label.lower())
        except ValueError:
            label = ConventionalLabel.ISSUE

        try:
            severity = FindingSeverity(raw.severity.lower())
        except ValueError:
            severity = FindingSeverity.MEDIUM

        return ReviewFinding(
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

    def _process_candidate_findings(
        self,
        provider_resp: ProviderResponse,
        request: ReviewRequest,
        context: GitReviewContext,
        collector: GitContextCollector,
    ) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []
        counter = 1

        for raw in provider_resp.candidate_findings:
            f = self._create_finding_from_raw(raw, counter, context, collector)
            if f is not None:
                findings.append(f)
                counter += 1

        for cand in request.candidate_findings:
            if (
                cand.file_path not in context.changed_files
                and cand.file_path not in context.related_files
            ):
                context.omitted_scope.append(
                    f"Candidate finding on untouched file '{cand.file_path}' was discarded."
                )
                continue

            v_line, f_reason = collector.validate_line_anchor(
                file_path=cand.file_path,
                line=cand.line,
                changed_files=context.changed_files,
            )
            orig_evidence = cand.evidence_status
            evidence_status = orig_evidence
            notes: list[str] = []
            if f_reason:
                notes.append(f"*(Note: {f_reason})*")

            if orig_evidence in (
                EvidenceStatus.EMPIRICALLY_VERIFIED,
                EvidenceStatus.STATIC_ANALYZED,
            ):
                evidence_status = EvidenceStatus.UNVERIFIED
                notes.append(
                    f"*(Evidence status downgraded: claimed '{orig_evidence.value}' without "
                    f"authenticated proof; normalized to 'unverified')*"
                )

            notes.append(f"*(Provenance: candidate ID '{cand.id}')*")
            desc = cand.description
            if notes:
                desc = f"{desc}\n\n" + "\n\n".join(notes)

            v_end_line = cand.end_line if v_line is not None else None

            findings.append(
                cand.model_copy(
                    update={
                        "id": f"rev-{counter:03d}",
                        "line": v_line,
                        "end_line": v_end_line,
                        "description": desc,
                        "evidence_status": evidence_status,
                    }
                )
            )
            counter += 1

        return findings

    def _deduplicate_findings(self, findings: list[ReviewFinding]) -> list[ReviewFinding]:
        deduped: list[ReviewFinding] = []
        seen_keys: set[str] = set()
        for f in findings:
            key = f"{f.file_path}:{f.line}:{f.label}:{f.subject}"
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(f)
        return deduped

    STOP_WORDS = {
        "this",
        "that",
        "with",
        "from",
        "have",
        "must",
        "should",
        "shall",
        "will",
        "would",
        "could",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "and",
        "any",
        "are",
        "aren't",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "can",
        "cannot",
        "couldn't",
        "did",
        "didn't",
        "does",
        "doesn't",
        "doing",
        "don't",
        "down",
        "during",
        "each",
        "few",
        "for",
        "further",
        "had",
        "hadn't",
        "has",
        "hasn't",
        "haven't",
        "having",
        "here",
        "how",
        "into",
        "more",
        "most",
        "need",
        "only",
        "other",
        "ought",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "shan't",
        "she",
        "shouldn't",
        "some",
        "such",
        "than",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "these",
        "they",
        "those",
        "through",
        "under",
        "until",
        "very",
        "was",
        "wasn't",
        "were",
        "weren't",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "won't",
        "wouldn't",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
        "code",
        "file",
        "test",
        "function",
        "system",
        "implementation",
        "require",
        "required",
    }

    def _is_requirement_relevant_to_context(
        self,
        req: ParsedRequirement,
        context: GitReviewContext,
    ) -> bool:
        """Check whether changes in unified diff touch the requirement's domain or keywords."""
        if not context.raw_diff or not context.raw_diff.strip():
            return False

        # Extract tokens from requirement text and ID
        tokens = re.findall(r"\w{3,}", f"{req.id} {req.text}".lower())
        meaningful_tokens = [t for t in tokens if t not in self.STOP_WORDS]
        if not meaningful_tokens:
            return False

        diff_lower = context.raw_diff.lower()
        changed_files_str = " ".join(context.changed_files.keys()).lower()

        return any(tok in diff_lower or tok in changed_files_str for tok in meaningful_tokens)

    def _has_valid_source_location(self, locations: list[str], context: GitReviewContext) -> bool:
        """Check if at least one location cites a valid file in the review context."""
        if not locations:
            return False
        for loc in locations:
            file_part = loc.split(":")[0].strip()
            if (
                file_part in context.changed_files
                or file_part in context.related_files
                or any(
                    k.endswith(f"/{file_part}") or file_part.endswith(f"/{k}")
                    for k in context.changed_files
                )
            ):
                return True
        return False

    def _assess_from_provider(
        self,
        raw_a: RawRequirementAssessment,
        req_id: str,
        is_relevant: bool,
        context: GitReviewContext,
        has_violating_findings: bool = False,
    ) -> tuple[RequirementStatus, str]:
        """Validate and normalize provider-supplied requirement assessment."""
        try:
            raw_status = RequirementStatus(raw_a.status.lower())
        except ValueError:
            raw_status = RequirementStatus.UNCLEAR

        if raw_status in (
            RequirementStatus.APPEARS_SATISFIED,
            RequirementStatus.PARTIALLY_SATISFIED,
        ):
            if not is_relevant:
                return (
                    RequirementStatus.NOT_ASSESSED,
                    f"Provider claimed '{raw_a.status}', but no relevant changes or "
                    f"positive evidence exist in diff context for '{req_id}'.",
                )
            if not self._has_valid_source_location(raw_a.related_locations, context):
                return (
                    RequirementStatus.UNCLEAR,
                    f"Provider claimed '{raw_a.status}', but provided no verifiable source "
                    f"locations or concrete implementation evidence in diff context "
                    f"for '{req_id}'.",
                )
            if not raw_a.explanation or not raw_a.explanation.strip():
                return (
                    RequirementStatus.UNCLEAR,
                    f"Provider claimed '{raw_a.status}', but provided no concrete explanation "
                    f"of how '{req_id}' is satisfied.",
                )

        if raw_status == RequirementStatus.NOT_SATISFIED and not has_violating_findings:
            return (
                RequirementStatus.UNCLEAR,
                f"Provider suggested non-satisfaction without concrete violating issues "
                f"for '{req_id}'; marked unclear for clarification.",
            )

        return raw_status, raw_a.explanation

    def _assess_unexamined_fallback(
        self,
        req_id: str,
        is_relevant: bool,
    ) -> tuple[RequirementStatus, str]:
        """Produce safe fallback for requirements not explicitly assessed."""
        if is_relevant:
            return (
                RequirementStatus.UNCLEAR,
                f"Changes touch components related to '{req_id}', but positive "
                "evidence demonstrating requirement satisfaction was not established.",
            )
        return (
            RequirementStatus.NOT_ASSESSED,
            f"Requirement '{req_id}' not assessed: no relevant changes in diff context.",
        )

    def _assess_single_requirement(
        self,
        req: ParsedRequirement,
        model_assessments_map: dict[str, RawRequirementAssessment],
        deduped_findings: list[ReviewFinding],
        dismissal_notes: list[str],
        context: GitReviewContext,
    ) -> RequirementAssessment:
        req_id_clean = req.id.upper()
        all_related_findings = [f for f in deduped_findings if req.id in f.requirement_ids]
        violating_findings = [
            f for f in all_related_findings if f.label == ConventionalLabel.ISSUE or f.blocking
        ]
        clarifying_findings = [f for f in all_related_findings if f not in violating_findings]
        is_relevant = self._is_requirement_relevant_to_context(req, context)

        # 1. Unresolved violating issues (blocking or label=issue)
        if violating_findings:
            status = RequirementStatus.NOT_SATISFIED
            finding_ids = ", ".join(f.id for f in violating_findings)
            explanation = (
                f"Requirement not satisfied: unresolved issue(s) identified ({finding_ids})."
            )
        # 2. Positive evidence via verified compensating controls
        elif any(req.id in note for note in dismissal_notes):
            status = RequirementStatus.APPEARS_SATISFIED
            explanation = "Requirement verified satisfied via compensating controls in context."
        # 3. Provider-supplied assessment validated against context
        elif req_id_clean in model_assessments_map:
            status, explanation = self._assess_from_provider(
                model_assessments_map[req_id_clean],
                req.id,
                is_relevant,
                context,
                has_violating_findings=bool(violating_findings),
            )
        # 4. Clarification questions or suggestions without explicit provider assessment
        elif clarifying_findings:
            status = RequirementStatus.UNCLEAR
            q_ids = ", ".join(f.id for f in clarifying_findings)
            explanation = (
                f"Requirement '{req.id}' has unresolved questions or suggestions ({q_ids})."
            )
        # 5. Fallback: never silently consider unexamined requirement satisfied
        else:
            status, explanation = self._assess_unexamined_fallback(req.id, is_relevant)

        return RequirementAssessment(
            requirement_id=req.id,
            source_file=req.source_file,
            text=req.text,
            status=status,
            explanation=explanation,
            related_finding_ids=[f.id for f in all_related_findings],
        )

    def _build_requirement_assessments(
        self,
        parsed_reqs: list[ParsedRequirement],
        provider_resp: ProviderResponse,
        deduped_findings: list[ReviewFinding],
        dismissal_notes: list[str],
        context: GitReviewContext,
    ) -> list[RequirementAssessment]:
        if not parsed_reqs:
            return [
                RequirementAssessment(
                    requirement_id="NONE",
                    source_file=None,
                    text="No requirement document supplied.",
                    status=RequirementStatus.NOT_ASSESSED,
                    explanation=(
                        "No requirements document was supplied; "
                        "standard code quality review was performed."
                    ),
                    related_finding_ids=[],
                )
            ]

        model_assessments_map = {
            a.requirement_id.upper(): a for a in provider_resp.requirement_assessments
        }
        return [
            self._assess_single_requirement(
                req=req,
                model_assessments_map=model_assessments_map,
                deduped_findings=deduped_findings,
                dismissal_notes=dismissal_notes,
                context=context,
            )
            for req in parsed_reqs
        ]

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
