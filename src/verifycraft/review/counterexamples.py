"""Counterexample and compensating logic validation for candidate findings."""

from __future__ import annotations

import re

from verifycraft.contracts.review import ReviewFinding
from verifycraft.review.git import GitReviewContext


class CounterexampleChecker:
    """Challenges candidate findings by checking for compensating logic in surrounding context."""

    COMPENSATING_PATTERNS = {
        "expiration": [
            re.compile(r"def (?:check_)?(?:token_)?expir", re.IGNORECASE),
            re.compile(r"is_expired\(", re.IGNORECASE),
            re.compile(r"expires_at\s*[<>]\s*", re.IGNORECASE),
            re.compile(r"validate_expiration\(", re.IGNORECASE),
        ],
        "authentication": [
            re.compile(r"def authenticate", re.IGNORECASE),
            re.compile(r"verify_token\(", re.IGNORECASE),
        ],
        "zero_division": [
            re.compile(r"if\s+[\w\.]+\s*==\s*0\s*:", re.IGNORECASE),
            re.compile(r"if\s+not\s+[\w\.]+\s*:", re.IGNORECASE),
        ],
    }

    def _check_expiration_compensation(
        self,
        finding: ReviewFinding,
        finding_text: str,
        context: GitReviewContext,
    ) -> str | None:
        if not ("expir" in finding_text and ("missing" in finding_text or "omit" in finding_text)):
            return None

        for pat in self.COMPENSATING_PATTERNS["expiration"]:
            for rel_path, rel_content in context.related_files.items():
                if pat.search(rel_content):
                    return (
                        f"Candidate issue on '{finding.file_path}' claiming missing "
                        f"expiration was dismissed: compensating control found in '{rel_path}'."
                    )
        return None

    def _check_zero_division_compensation(
        self,
        finding: ReviewFinding,
        finding_text: str,
        context: GitReviewContext,
    ) -> str | None:
        has_zero_keyword = "zero" in finding_text
        has_division_keyword = "division" in finding_text or "denominator" in finding_text
        if not (has_zero_keyword and has_division_keyword):
            return None

        target_file = context.changed_files.get(finding.file_path)
        if not target_file or not target_file.content:
            return None

        for pat in self.COMPENSATING_PATTERNS["zero_division"]:
            if pat.search(target_file.content):
                return (
                    f"Candidate issue on '{finding.file_path}' claiming unhandled zero "
                    "division was dismissed: guard check found in surrounding file context."
                )
        return None

    def check_compensating_controls(
        self,
        findings: list[ReviewFinding],
        context: GitReviewContext,
    ) -> tuple[list[ReviewFinding], list[str]]:
        """Filter or challenge findings if compensating controls exist in surrounding context."""
        validated: list[ReviewFinding] = []
        dismissal_notes: list[str] = []

        for f in findings:
            finding_text = f"{f.subject} {f.description}".lower()

            note = self._check_expiration_compensation(f, finding_text, context)
            if not note:
                note = self._check_zero_division_compensation(f, finding_text, context)

            if note:
                dismissal_notes.append(note)
            else:
                validated.append(f)

        return validated, dismissal_notes
