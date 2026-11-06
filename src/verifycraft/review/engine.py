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
from verifycraft.review.git import ChangedFile, GitContextCollector, GitReviewContext
from verifycraft.review.providers.base import (
    ProviderResponse,
    RawCandidateFinding,
    RawRequirementAssessment,
    ReviewerModelAdapter,
)
from verifycraft.review.providers.registry import resolve_provider_adapter
from verifycraft.review.requirements import ParsedRequirement, RequirementsParser


def _check_line_in_content(line_no: int | None, content: str | None) -> bool:
    """Validate that line_no falls within content line bounds, or is None."""
    if line_no is None or not content:
        return True
    return 1 <= line_no <= len(content.splitlines())


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sort and merge overlapping or adjacent (start, end) line intervals."""
    if not intervals:
        return []
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    merged: list[tuple[int, int]] = [sorted_intervals[0]]
    for start, end in sorted_intervals[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _render_windowed_chunks(
    lines: list[str], merged: list[tuple[int, int]], total_lines: int
) -> str:
    """Render 1-indexed lines with omission markers between intervals."""
    formatted_chunks: list[str] = []
    prev_end = 0
    for start, end in merged:
        if prev_end > 0 and start > prev_end + 1:
            omitted = start - prev_end - 1
            formatted_chunks.append(
                f"... [{omitted} surrounding lines omitted for brevity "
                f"(lines {prev_end + 1}-{start - 1}); see diff] ..."
            )
        for i in range(start, end + 1):
            formatted_chunks.append(f"{i}: {lines[i - 1]}")
        prev_end = end

    if prev_end < total_lines:
        omitted = total_lines - prev_end
        formatted_chunks.append(
            f"... [{omitted} trailing lines omitted for brevity "
            f"(lines {prev_end + 1}-{total_lines}); see diff] ..."
        )
    return "\n".join(formatted_chunks)


def _format_prompt_head(context: Any) -> str:
    """Format repository head revision or uncommitted worktree description."""
    if getattr(context, "is_working_tree", False) or getattr(
        context, "head_revision", ""
    ).upper() in ("WORKTREE", "WORKING_TREE"):
        return (
            f"WORKTREE (Uncommitted working-tree modifications relative to base "
            f"{context.base_revision} [{context.base_commit[:8]}])"
        )
    return f"{context.head_revision} ({context.head_commit[:8]})"


def _format_related_files_section(
    related_files: dict[str, str], changed_files: dict[str, Any]
) -> list[str]:
    """Format related helper files, excluding files already in changed_files."""
    unique = {p: c for p, c in related_files.items() if p not in changed_files}
    if not unique:
        return []
    parts = [
        "## Relevant Surrounding Code & Related Helpers",
        "*(Compensating controls, imported helpers, and existing tests)*",
        "",
    ]
    for path, content in unique.items():
        parts.extend([f"### Context File: `{path}`", "```python", content, "```", ""])
    return parts


def _format_requirements_section(requirements: list[ParsedRequirement]) -> list[str]:
    """Format explicit requirements into markdown list."""
    if not requirements:
        return []
    parts = ["## Explicit Requirements", ""]
    for req in requirements:
        src = f" ({req.source_file}:{req.line_number})" if req.source_file else ""
        parts.append(f"- **[{req.id}]**{src}: {req.text}")
    parts.append("")
    return parts


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
        base_url: str | None = None,
        allow_insecure_http: bool = False,
    ) -> ReviewerModelAdapter | None:
        name = provider_name.lower().strip()
        if name in ("none", "model-free", "agent"):
            return None
        if self._injected_adapter is not None:
            return self._injected_adapter
        return resolve_provider_adapter(
            provider_name=provider_name,
            model=model,
            base_url=base_url,
            allow_insecure_http=allow_insecure_http,
        )

    def review(
        self,
        request: ReviewRequest,
        context_override: GitReviewContext | None = None,
    ) -> ReviewReport:
        """Execute a requirements-aware semantic code review against a Git revision pair."""
        start_time = time.monotonic()

        # 1. Validate and resolve reviewer model adapter early
        adapter = self._get_adapter(
            request.provider,
            model=request.model,
            base_url=request.base_url,
            allow_insecure_http=request.allow_insecure_http,
        )

        # 2. Collect Git context
        collector = GitContextCollector(request.repo_path)
        if context_override is not None:
            context = context_override
        else:
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
            all_findings=findings,
        )

        # 9. Compute Resource Usage
        wall_time = time.monotonic() - start_time
        resource_usage = ReviewResourceUsage(
            files_reviewed=len(context.changed_files),
            diff_lines=context.diff_lines_count,
            context_bytes=context.total_context_bytes,
            wall_time_seconds=round(wall_time, 4),
            tokens_used=provider_resp.tokens_used,
            prompt_tokens=getattr(provider_resp, "prompt_tokens", None),
            output_tokens=getattr(provider_resp, "output_tokens", None),
        )

        model_name_attr = getattr(adapter, "model_name", None)
        if isinstance(model_name_attr, str):
            resolved_model_name = model_name_attr
        else:
            resolved_model_name = request.model or (
                "model-free" if adapter is None else "deterministic"
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
        validated_line, fallback_reason = collector.validate_semantic_anchor(
            file_path=raw.file_path,
            line=raw.line,
            subject=raw.subject,
            description=raw.description,
            changed_files=context.changed_files,
            suggested_action=raw.suggested_action,
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

        suggested_action = raw.suggested_action
        if suggested_action and not suggested_action.startswith("(Proposed remedy"):
            suggested_action = f"(Proposed remedy — model inferred): {suggested_action}"

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
            suggested_action=suggested_action,
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
            desc = f"{cand.description}\n\n" + "\n\n".join(notes)

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
        """Deduplicate findings semantically across different labels and formulations."""
        if not findings:
            return []

        label_rank = {
            ConventionalLabel.ISSUE: 6,
            ConventionalLabel.SUGGESTION: 5,
            ConventionalLabel.TODO: 4,
            ConventionalLabel.QUESTION: 3,
            ConventionalLabel.THOUGHT: 2,
            ConventionalLabel.NITPICK: 1,
            ConventionalLabel.PRAISE: 0,
            ConventionalLabel.CHORE: 1,
            ConventionalLabel.NOTE: 1,
        }
        severity_rank = {
            FindingSeverity.CRITICAL: 4,
            FindingSeverity.HIGH: 3,
            FindingSeverity.MEDIUM: 2,
            FindingSeverity.LOW: 1,
            FindingSeverity.INFO: 0,
        }

        def _extract_core_tokens(f: ReviewFinding) -> tuple[set[str], set[str]]:
            text = f"{f.subject}\n{f.description}"
            quoted_symbols = {
                s.strip().lower()
                for s in re.findall(r"[`'\"]([a-zA-Z0-9_\.\-:]+)[`'\"]", text)
                if len(s.strip()) >= 3
            }
            code_identifiers = {
                ident.lower()
                for ident in re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_\.]{2,}\b", f.subject)
                if "_" in ident or "." in ident or ident.isupper()
            }
            all_symbols = quoted_symbols | code_identifiers
            words = {
                w.lower()
                for w in re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]{3,}\b", f.subject)
                if w.lower() not in self.STOP_WORDS
            }
            return all_symbols, words

        clusters: list[list[ReviewFinding]] = []

        for f in findings:
            syms, words = _extract_core_tokens(f)
            placed = False
            for cluster in clusters:
                rep = cluster[0]
                if rep.file_path != f.file_path:
                    continue

                rep_syms, rep_words = _extract_core_tokens(rep)

                # Check line proximity
                lines_compatible = False
                if rep.line is None and f.line is None:
                    lines_compatible = True
                elif rep.line is not None and f.line is not None:
                    dist = abs(rep.line - f.line)
                    if dist <= 10:
                        lines_compatible = True
                    elif dist <= 30:
                        lines_compatible = bool(syms and rep_syms and (syms & rep_syms))
                    else:
                        lines_compatible = False
                else:
                    # One unanchored, one anchored: compatible if exact symbol matches
                    lines_compatible = bool(syms and rep_syms and (syms & rep_syms))

                if not lines_compatible:
                    continue

                # Check semantic overlap
                shared_syms = syms & rep_syms
                shared_words = words & rep_words
                word_union = words | rep_words
                jaccard = (len(shared_words) / len(word_union)) if word_union else 0.0

                if shared_syms or jaccard >= 0.5:
                    cluster.append(f)
                    placed = True
                    break

            if not placed:
                clusters.append([f])

        deduped: list[ReviewFinding] = []
        for cluster in clusters:
            if len(cluster) == 1:
                deduped.append(cluster[0])
                continue

            # Sort cluster by label priority then severity priority
            sorted_cluster = sorted(
                cluster,
                key=lambda item: (
                    label_rank.get(item.label, 1),
                    severity_rank.get(item.severity, 1),
                ),
                reverse=True,
            )
            primary = sorted_cluster[0]
            merged_desc = primary.description
            notes: list[str] = []

            for other in sorted_cluster[1:]:
                if other.subject != primary.subject:
                    desc_detail = (
                        f" — {other.description}"
                        if other.description and other.description.strip() != other.subject.strip()
                        else ""
                    )
                    tag = f"*(Related perspective [{other.label.value}]: "
                    notes.append(f"{tag}{other.subject}{desc_detail})*")

            if notes:
                merged_desc = f"{merged_desc}\n\n" + "\n\n".join(notes)

            # Preserve primary's blocking and severity decisions to avoid inflating
            # blocking status from unverified or subordinate cluster members.
            is_blocking = primary.blocking
            sev = primary.severity
            all_req_ids = sorted({req for item in cluster for req in item.requirement_ids})

            deduped.append(
                primary.model_copy(
                    update={
                        "description": merged_desc,
                        "blocking": is_blocking,
                        "severity": sev,
                        "requirement_ids": all_req_ids,
                    }
                )
            )

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

    def _is_valid_file_and_line(
        self, file_path: str, line_no: int | None, context: GitReviewContext
    ) -> bool:
        """Validate that file_path exists in context and line_no is within file bounds."""
        cfile = context.changed_files.get(file_path)
        if cfile is not None:
            return _check_line_in_content(line_no, cfile.content)

        if file_path in context.related_files:
            return _check_line_in_content(line_no, context.related_files[file_path])

        # Suffix matching for paths
        for k, cfile in context.changed_files.items():
            if k.endswith(f"/{file_path}") or file_path.endswith(f"/{k}"):
                return _check_line_in_content(line_no, cfile.content)

        return False

    def _has_valid_source_location(self, locations: list[str], context: GitReviewContext) -> bool:
        """Check if at least one location cites a valid file and line in the review context."""
        if not locations:
            return False
        for loc in locations:
            parts = loc.split(":")
            file_part = parts[0].strip()
            line_no = None
            if len(parts) > 1:
                try:
                    line_no = int(parts[1].strip())
                except ValueError:
                    line_no = None
            if self._is_valid_file_and_line(file_part, line_no, context):
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
        status_str = raw_a.status.lower().strip().replace("-", "_")
        if status_str == "satisfied":
            status_str = RequirementStatus.APPEARS_SATISFIED.value
        try:
            raw_status = RequirementStatus(status_str)
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

        explanation = raw_a.explanation
        if not explanation.startswith("[Grounding:"):
            cited_str = (
                ", ".join(raw_a.related_locations) if raw_a.related_locations else "diff context"
            )
            grounding_prefix = f"[Grounding: Model semantic inference | Citations: {cited_str}]"
            explanation = f"{grounding_prefix} {raw_a.explanation}"

        return raw_status, explanation

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

    MANDATORY_TERMS = {
        "must",
        "shall",
        "required",
        "mandatory",
        "strictly",
        "enforce",
        "prohibit",
    }

    GENERIC_DOMAIN_WORDS = {
        "account",
        "accounts",
        "auth",
        "authentication",
        "authorize",
        "authorization",
        "check",
        "checks",
        "class",
        "code",
        "component",
        "components",
        "data",
        "feature",
        "features",
        "file",
        "files",
        "function",
        "functions",
        "handler",
        "handlers",
        "helper",
        "helpers",
        "logic",
        "method",
        "methods",
        "model",
        "models",
        "module",
        "modules",
        "record",
        "records",
        "req",
        "service",
        "services",
        "system",
        "systems",
        "user",
        "users",
        "util",
        "utils",
        "value",
        "values",
    }

    @classmethod
    def _is_mandatory_requirement(cls, req: ParsedRequirement) -> bool:
        """Check if requirement expresses a mandatory constraint per RFC 2119 semantics."""
        words = set(re.findall(r"\b[a-zA-Z_]+\b", req.text.lower()))
        return bool(words & cls.MANDATORY_TERMS)

    @classmethod
    def _has_requirement_evidence(
        cls,
        f: ReviewFinding,
        req: ParsedRequirement,
    ) -> bool:
        """Check if finding provides concrete evidence connecting to the requirement's domain."""
        # 1. Finding must cite the requirement ID
        if req.id not in f.requirement_ids:
            return False

        f_subject_lower = f.subject.lower()
        f_text = f"{f.subject}\n{f.description}".lower()
        req_text_lower = f"{req.id} {req.text}".lower()

        # 2. Exclude cosmetic and documentation issues from functional code requirements
        cosmetic_pattern = (
            r"\b(?:typo|spelling|docstring|comment|comments|"
            r"formatting|whitespace|style|indentation|grammar)\b"
        )
        if re.search(cosmetic_pattern, f_subject_lower):
            req_is_doc = bool(
                re.search(
                    r"\b(?:docstring|comment|comments|documentation|formatting|spelling|typo)\b",
                    req_text_lower,
                )
            )
            if not req_is_doc:
                return False

        # 3. Explicit requirement noncompliance statements
        # (e.g. "failed requirement", "violates REQ-01")
        req_id_escaped = re.escape(req.id.lower())
        if re.search(
            rf"\b(?:failed|violat\w*|breach\w*|break\w*|miss\w*)\s+(?:requirement\b|{req_id_escaped}\b)",
            f_text,
        ):
            return True
        if re.search(
            r"\brequirement\s+(?:failed|violated|not\s+met|unfulfilled)\b",
            f_text,
        ):
            return True

        # 4. Extract meaningful constraint terms from requirement text
        raw_req_words = {
            w.lower()
            for w in re.findall(r"\b[a-zA-Z_]{3,}\b", req.text)
            if w.lower() not in cls.STOP_WORDS
        }
        constraint_words = {w for w in raw_req_words if w not in cls.GENERIC_DOMAIN_WORDS}
        if not constraint_words:
            return True

        # 5. Extract domain words from finding (excluding stopwords and generic component terms)
        finding_words = {
            w.lower()
            for w in re.findall(r"\b[a-zA-Z_]{3,}\b", f_text)
            if w.lower() not in cls.STOP_WORDS and w.lower() not in cls.GENERIC_DOMAIN_WORDS
        }
        if not finding_words:
            return False

        # 6. Check for direct constraint match or meaningful root/stem overlap (min 4 chars)
        if constraint_words & finding_words:
            return True

        for cw in constraint_words:
            for fw in finding_words:
                if (
                    len(cw) >= 4
                    and len(fw) >= 4
                    and (cw.startswith(fw[:4]) or fw.startswith(cw[:4]))
                ):
                    return True

        return False

    @staticmethod
    def _is_clarifying_finding(f: ReviewFinding) -> bool:
        """Check if finding represents a clarification question or specification ambiguity."""
        if f.label == ConventionalLabel.QUESTION:
            return True
        desc_lower = f.description.lower()
        subj_lower = f.subject.lower()
        ambiguity_markers = (
            "unspecified",
            "ambiguous",
            "missing precedence",
            "priority is unspecified",
            "precedence is unspecified",
            "unclear acceptance criteria",
            "missing acceptance criteria",
        )
        return any(marker in subj_lower or marker in desc_lower for marker in ambiguity_markers)

    @classmethod
    def _is_proven_violation(
        cls,
        f: ReviewFinding,
        req: ParsedRequirement | None = None,
        raw_assessment: RawRequirementAssessment | None = None,
    ) -> bool:
        """Determine whether a finding constitutes an established requirement violation."""
        # 1. Merge-blocking findings are by definition established violations
        if f.blocking:
            return True

        # 2. Only findings labeled 'issue' can be violations
        if f.label != ConventionalLabel.ISSUE:
            return False

        # 3. Finding must have substantive evidence connecting it to the requirement domain
        if req is not None and not cls._has_requirement_evidence(f, req):
            return False

        # 4. High or Critical severity findings connected to the requirement demonstrate
        # concrete noncompliance, regardless of requirement wording (must vs should)
        # or provider claim
        if f.severity in (FindingSeverity.HIGH, FindingSeverity.CRITICAL):
            return True

        # 5. If provider assessment confirms non-satisfaction, issue is corroborated
        if raw_assessment is not None:
            raw_status = raw_assessment.status.lower().strip().replace("-", "_")
            if raw_status in ("not_satisfied", "violated"):
                return True
            # If provider claims satisfied on a non-mandatory recommendation with ambiguity,
            # a non-blocking low/medium issue does not prove violation over specification ambiguity
            if (
                raw_status in ("satisfied", "appears_satisfied")
                and req is not None
                and not cls._is_mandatory_requirement(req)
            ):
                return False

        # 6. Default for unassessed/model-free issues or mandatory requirements
        return True

    @staticmethod
    def _extract_clarification_text(f: ReviewFinding) -> str:
        """Extract clarification question or ambiguity summary from finding."""
        if f.label == ConventionalLabel.QUESTION:
            return f.subject

        m = re.search(
            r"\*\(Related perspective \[question\]:\s*([^\*\n—\)]+)",
            f.description,
            re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()

        return f.subject

    def _assess_single_requirement(
        self,
        req: ParsedRequirement,
        model_assessments_map: dict[str, RawRequirementAssessment],
        deduped_findings: list[ReviewFinding],
        dismissal_notes: list[str],
        context: GitReviewContext,
        all_findings: list[ReviewFinding] | None = None,
    ) -> RequirementAssessment:
        req_id_clean = req.id.upper()
        raw_assessment = model_assessments_map.get(req_id_clean)

        # Use un-deduplicated findings when available to prevent presentation clustering
        # from suppressing or altering constituent perspectives
        source_findings = all_findings if all_findings is not None else deduped_findings
        all_related_findings = [f for f in source_findings if req.id in f.requirement_ids]
        is_relevant = self._is_requirement_relevant_to_context(req, context)

        # 1. Distinguish established violations from questions / ambiguities
        proven_violations = [
            f for f in all_related_findings if self._is_proven_violation(f, req, raw_assessment)
        ]
        clarifying_findings = [f for f in all_related_findings if self._is_clarifying_finding(f)]

        # Also inspect deduped_findings for any merged question perspectives
        # when all_findings was not supplied
        if all_findings is None:
            for f in deduped_findings:
                if (
                    req.id in f.requirement_ids
                    and f not in clarifying_findings
                    and "related perspective [question]" in f.description.lower()
                ):
                    clarifying_findings.append(f)

        # 1. Established, sufficiently evidenced requirement violations
        if proven_violations:
            status = RequirementStatus.NOT_SATISFIED
            finding_ids = ", ".join(f.id for f in proven_violations)
            explanation = (
                f"Requirement not satisfied: unresolved issue(s) identified ({finding_ids})."
            )
        # 2. Positive evidence via verified compensating controls in context
        elif any(req.id in note for note in dismissal_notes):
            status = RequirementStatus.APPEARS_SATISFIED
            explanation = (
                "[Grounding: Statically detected compensating control in context] "
                "Requirement verified satisfied via compensating controls in context."
            )
        # 3. Ambiguous specifications or unresolved clarification questions
        elif clarifying_findings or (
            raw_assessment is not None
            and raw_assessment.status.lower().strip().replace("-", "_")
            in ("unclear", "partially_satisfied")
        ):
            status = RequirementStatus.UNCLEAR
            q_summaries = [self._extract_clarification_text(f) for f in clarifying_findings]
            if not q_summaries and raw_assessment and raw_assessment.explanation:
                q_summaries = [raw_assessment.explanation]
            unique_summaries = list(dict.fromkeys(q_summaries))
            q_text = (
                "; ".join(unique_summaries)
                if unique_summaries
                else f"unresolved questions ({', '.join(f.id for f in clarifying_findings)})"
            )
            is_spec_ambiguity = any(
                any(
                    k in s.lower()
                    for k in (
                        "precedence",
                        "priority",
                        "acceptance criteria",
                        "unspecified",
                        "ambiguous",
                    )
                )
                for s in unique_summaries
            )
            if is_spec_ambiguity:
                explanation = (
                    f"Requirement '{req.id}' is unclear: specification does not establish "
                    f"clear precedence or acceptance criteria ({q_text})."
                )
            else:
                explanation = (
                    f"Requirement '{req.id}' has unresolved questions or suggestions ({q_text})."
                )
        # 4. Provider-supplied assessment validated against context
        elif raw_assessment is not None:
            status, explanation = self._assess_from_provider(
                raw_assessment,
                req.id,
                is_relevant,
                context,
                has_violating_findings=bool(proven_violations),
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
            related_finding_ids=[f.id for f in deduped_findings if req.id in f.requirement_ids],
        )

    def _build_requirement_assessments(
        self,
        parsed_reqs: list[ParsedRequirement],
        provider_resp: ProviderResponse,
        deduped_findings: list[ReviewFinding],
        dismissal_notes: list[str],
        context: GitReviewContext,
        all_findings: list[ReviewFinding] | None = None,
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
                all_findings=all_findings,
            )
            for req in parsed_reqs
        ]

    def _format_changed_file_context(
        self,
        cfile: ChangedFile,
        max_lines_per_file: int = 250,
        context_window: int = 25,
    ) -> str:
        """Format a changed file with 1-indexed line numbers and bounded surrounding context."""
        if getattr(cfile, "is_context_omitted", False):
            return "(Content omitted due to context size limits)"
        if not cfile.content:
            return "(Empty file)"
        if cfile.prompt_context is not None:
            return cfile.prompt_context

        lines = cfile.content.splitlines()
        total_lines = len(lines)
        if total_lines <= max_lines_per_file:
            return "\n".join(f"{i}: {line}" for i, line in enumerate(lines, start=1))

        intervals: list[tuple[int, int]] = [(1, min(30, total_lines))]
        for hunk in cfile.hunks:
            h_start = max(1, hunk.new_start - context_window)
            h_end = min(total_lines, hunk.new_start + max(hunk.new_lines, 1) + context_window)
            intervals.append((h_start, h_end))

        merged = _merge_intervals(intervals)
        return _render_windowed_chunks(lines, merged, total_lines)

    def _format_changed_files_section(self, changed_files: dict[str, ChangedFile]) -> list[str]:
        """Format surrounding context for changed files."""
        parts = [
            "## Changed Files (Surrounding Implementation Context)",
            "*(Numbered lines showing surrounding functions, classes, and imports)*",
            "",
        ]
        for path, cfile in changed_files.items():
            if cfile.status == "deleted":
                parts.extend([f"### Changed File: `{path}` *(deleted)*", ""])
                continue
            file_repr = self._format_changed_file_context(cfile)
            parts.extend([f"### Changed File: `{path}`", "```python", file_repr, "```", ""])
        return parts

    def _build_prompt(
        self,
        context: Any,
        requirements: list[ParsedRequirement],
    ) -> str:
        """Construct the prompt sent to the reviewer model adapter."""
        head_str = _format_prompt_head(context)
        parts = [
            "# Code Review Request",
            "",
            f"Repository Base: {context.base_revision} ({context.base_commit[:8]})",
            f"Repository Head: {head_str}",
            "",
        ]

        if getattr(context, "omitted_scope", None):
            parts.extend(
                [
                    "## Context Scope and Omissions Notice",
                    "*(The following limitations or omissions apply to this review context)*",
                    "",
                ]
            )
            for omission in context.omitted_scope:
                parts.append(f"- {omission}")
            parts.append("")

        parts.extend(
            [
                "## Unified Diff",
                "```diff",
                context.raw_diff or "(No changes in diff)",
                "```",
                "",
            ]
        )

        changed_files = getattr(context, "changed_files", None)
        if changed_files:
            parts.extend(self._format_changed_files_section(changed_files))

        related_files = getattr(context, "related_files", None)
        if related_files:
            parts.extend(_format_related_files_section(related_files, changed_files or {}))

        parts.extend(_format_requirements_section(requirements))
        return "\n".join(parts)
