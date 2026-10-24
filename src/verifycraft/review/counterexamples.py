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

    def check_compensating_controls(
        self,
        findings: list[ReviewFinding],
        context: GitReviewContext,
    ) -> tuple[list[ReviewFinding], list[str]]:
        """Filter or challenge findings if compensating controls exist in surrounding context."""
        validated: list[ReviewFinding] = []
        dismissal_notes: list[str] = []

        # Aggregate all context code (unchanged lines + related helper files)
        all_context_code = "\n".join(context.related_files.values())
        for cfile in context.changed_files.values():
            if cfile.content:
                all_context_code += f"\n{cfile.content}"

        for f in findings:
            finding_text = f"{f.subject} {f.description}".lower()
            compensated = False

            # Check for token expiration claim
            if "expir" in finding_text and ("missing" in finding_text or "omit" in finding_text):
                for pat in self.COMPENSATING_PATTERNS["expiration"]:
                    for rel_path, rel_content in context.related_files.items():
                        if pat.search(rel_content):
                            compensated = True
                            note = (
                                f"Candidate issue on '{f.file_path}' claiming missing "
                                f"expiration was dismissed: compensating control found "
                                f"in '{rel_path}'."
                            )
                            dismissal_notes.append(note)
                            break
                    if compensated:
                        break

            # Check for zero division claim
            if "zero" in finding_text and (
                "division" in finding_text or "denominator" in finding_text
            ):
                for pat in self.COMPENSATING_PATTERNS["zero_division"]:
                    target_file = context.changed_files.get(f.file_path)
                    if target_file and target_file.content and pat.search(target_file.content):
                        compensated = True
                        note = (
                            f"Candidate issue on '{f.file_path}' claiming unhandled zero "
                            "division was dismissed: guard check found in surrounding file context."
                        )
                        dismissal_notes.append(note)
                        break

            if not compensated:
                validated.append(f)

        return validated, dismissal_notes
