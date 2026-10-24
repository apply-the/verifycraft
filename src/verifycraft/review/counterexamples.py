"""Counterexample and compensating logic validation for candidate findings."""

from __future__ import annotations

import ast
import re

from verifycraft.contracts.review import ReviewFinding
from verifycraft.review.git import GitReviewContext


class CounterexampleChecker:
    """Challenges candidate findings by checking for defensible compensating logic."""

    COMPENSATING_PATTERNS = {
        "expiration": [
            re.compile(
                r"def\s+((?:check_)?(?:token_)?expir\w*|validate_expiration|verify_token)\b",
                re.IGNORECASE,
            ),
            re.compile(r"is_expired\(", re.IGNORECASE),
            re.compile(r"expires_at\s*[<>]\s*", re.IGNORECASE),
            re.compile(r"validate_expiration\(", re.IGNORECASE),
        ],
        "authentication": [
            re.compile(r"def\s+authenticate\b", re.IGNORECASE),
            re.compile(r"verify_token\(", re.IGNORECASE),
        ],
    }

    TOKEN_VARIANTS = ("session_token", "refresh_token", "access_token", "auth_token")

    def _find_enclosing_scope(self, tree: ast.AST, target_line: int | None) -> ast.AST:
        """Find the FunctionDef or AsyncFunctionDef enclosing target_line, if any."""
        if target_line is None:
            return tree

        enclosing: ast.FunctionDef | ast.AsyncFunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = node.lineno
                end = getattr(node, "end_lineno", start)
                if start <= target_line <= end and (
                    enclosing is None
                    or (
                        end - start
                        < getattr(enclosing, "end_lineno", enclosing.lineno) - enclosing.lineno
                    )
                ):
                    enclosing = node
        return enclosing if enclosing is not None else tree

    def _extract_calls(self, scope: ast.AST) -> list[tuple[str, list[str]]]:
        """Extract function and method calls with identifier argument names from AST scope."""
        calls: list[tuple[str, list[str]]] = []
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call):
                continue

            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                val = getattr(node.func.value, "id", "")
                func_name = f"{val}.{node.func.attr}" if val else node.func.attr

            args: list[str] = []
            for a in node.args:
                if isinstance(a, ast.Name):
                    args.append(a.id)
                elif isinstance(a, ast.Attribute):
                    args.append(a.attr)

            if func_name:
                calls.append((func_name, args))
        return calls

    def _detect_subject_token(self, finding_text: str) -> str | None:
        """Determine if a finding targets a specific token variant."""
        lowered = finding_text.lower()
        for t in self.TOKEN_VARIANTS:
            if t in lowered:
                return t
        return None

    def _is_matching_expiration_call(self, call_name: str) -> bool:
        """Check if call name represents an expiration or token validation check."""
        lowered = call_name.lower()
        return (
            "is_expired" in lowered
            or "check_token_expiration" in lowered
            or "validate_expiration" in lowered
            or "check_expiration" in lowered
            or "verify_expiration" in lowered
            or "verify_token" in lowered
        )

    def _has_expires_at_check(self, scope: ast.AST, subject_token: str | None = None) -> bool:
        """Check if AST scope directly compares expires_at on token."""
        for node in ast.walk(scope):
            if isinstance(node, ast.Compare):
                txt = ast.unparse(node)
                if "expires_at" in txt or "expired" in txt:
                    if subject_token and subject_token not in txt:
                        continue
                    return True
        return False

    def _has_zero_division_guard(self, scope: ast.AST, target_line: int | None) -> bool:
        """Check if AST scope contains an explicit guard or handler protecting division."""
        for sub in ast.walk(scope):
            if isinstance(sub, ast.If):
                txt = ast.unparse(sub.test)
                if any(p in txt for p in ("== 0", "!= 0", "not ", "<= 0", "is 0", "is None")) and (
                    target_line is None or getattr(sub, "lineno", 0) <= target_line
                ):
                    return True
            elif isinstance(sub, ast.Assert):
                txt = ast.unparse(sub.test)
                if any(p in txt for p in ("!= 0", "> 0", "not None", "is not None")) and (
                    target_line is None or getattr(sub, "lineno", 0) <= target_line
                ):
                    return True
            elif isinstance(sub, ast.Try):
                for h in sub.handlers:
                    h_name = getattr(h.type, "id", "") if h.type else ""
                    if h_name in ("ZeroDivisionError", "ArithmeticError", "Exception"):
                        return True
        return False

    def _check_expiration_compensation(
        self,
        finding: ReviewFinding,
        finding_text: str,
        context: GitReviewContext,
    ) -> str | None:
        """Verify defensible call-path evidence before dismissing an expiration finding."""
        if not (
            "expir" in finding_text
            and ("missing" in finding_text or "omit" in finding_text or "unhandled" in finding_text)
        ):
            return None

        target_file = context.changed_files.get(finding.file_path)
        if not target_file or not target_file.content:
            return None

        try:
            tree = ast.parse(target_file.content)
        except SyntaxError:
            # Conservative: if AST cannot be verified, never suppress finding
            return None

        scope = self._find_enclosing_scope(tree, finding.line)
        calls = self._extract_calls(scope)
        subject_token = self._detect_subject_token(finding_text)

        req_info = f" ({', '.join(finding.requirement_ids)})" if finding.requirement_ids else ""

        # 1. Check if compensating logic is imported and called on the execution path
        for rel_path, rel_content in context.related_files.items():
            for pat in self.COMPENSATING_PATTERNS["expiration"]:
                if not pat.search(rel_content):
                    continue

                for call_name, call_args in calls:
                    if self._is_matching_expiration_call(call_name):
                        if subject_token:
                            token_in_call = (subject_token in call_name) or any(
                                subject_token in a for a in call_args
                            )
                            if not token_in_call:
                                continue
                        return (
                            f"Candidate issue {finding.id}{req_info} on '{finding.file_path}' "
                            f"claiming missing expiration was dismissed: call to '{call_name}' "
                            f"verified on execution path in '{finding.file_path}' "
                            f"(defined in '{rel_path}')."
                        )

        # 2. Check if compensating logic is present in unchanged code within the same function scope
        for call_name, call_args in calls:
            if self._is_matching_expiration_call(call_name):
                if subject_token:
                    token_in_call = (subject_token in call_name) or any(
                        subject_token in a for a in call_args
                    )
                    if not token_in_call:
                        continue
                return (
                    f"Candidate issue {finding.id}{req_info} on '{finding.file_path}' "
                    f"claiming missing expiration was dismissed: compensating control "
                    f"'{call_name}' verified in surrounding execution scope."
                )

        # 3. Check direct timestamp comparison in scope
        if self._has_expires_at_check(scope, subject_token):
            return (
                f"Candidate issue {finding.id}{req_info} on '{finding.file_path}' "
                f"claiming missing expiration was dismissed: expiration comparison "
                "check verified in scope."
            )

        return None

    def _check_zero_division_compensation(
        self,
        finding: ReviewFinding,
        finding_text: str,
        context: GitReviewContext,
    ) -> str | None:
        """Verify guard evidence on the division path before dismissing a zero division finding."""
        has_zero_keyword = "zero" in finding_text
        has_division_keyword = "division" in finding_text or "denominator" in finding_text
        if not (has_zero_keyword and has_division_keyword):
            return None

        target_file = context.changed_files.get(finding.file_path)
        if not target_file or not target_file.content:
            return None

        try:
            tree = ast.parse(target_file.content)
        except SyntaxError:
            return None

        scope = self._find_enclosing_scope(tree, finding.line)
        if self._has_zero_division_guard(scope, finding.line):
            req_info = f" ({', '.join(finding.requirement_ids)})" if finding.requirement_ids else ""
            return (
                f"Candidate issue {finding.id}{req_info} on '{finding.file_path}' "
                "claiming unhandled zero division was dismissed: guard check found "
                "protecting division in scope."
            )

        return None

    def check_compensating_controls(
        self,
        findings: list[ReviewFinding],
        context: GitReviewContext,
    ) -> tuple[list[ReviewFinding], list[str]]:
        """Filter or challenge findings using defensible call-path evidence."""
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
