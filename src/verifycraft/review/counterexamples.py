"""Counterexample and compensating logic validation for candidate findings."""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from dataclasses import dataclass

from verifycraft.contracts.review import ReviewFinding
from verifycraft.review.git import GitReviewContext


@dataclass(frozen=True)
class CallInfo:
    """Metadata for an AST call invocation."""

    func_name: str
    args: list[str]
    lineno: int
    is_statement_expr: bool


class CounterexampleChecker:
    """Challenges candidate findings by checking for defensible compensating logic."""

    COMPENSATING_PATTERNS = {
        "expiration": [
            re.compile(
                r"def\s+((?:check_)?(?:token_)?expir\w*|validate_expiration|has_expired)\b",
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

    def _extract_func_name(self, func_node: ast.AST) -> str:
        if isinstance(func_node, ast.Name):
            return func_node.id
        if isinstance(func_node, ast.Attribute):
            val = getattr(func_node.value, "id", "")
            return f"{val}.{func_node.attr}" if val else func_node.attr
        return ""

    def _extract_call_args(self, call_args: Sequence[ast.expr]) -> list[str]:
        args: list[str] = []
        for a in call_args:
            if isinstance(a, ast.Name):
                args.append(a.id)
            elif isinstance(a, ast.Attribute):
                args.append(a.attr)
        return args

    def _extract_calls(self, scope: ast.AST, max_line: int | None = None) -> list[CallInfo]:
        """Extract calls with arguments, lineno, and whether call return value is discarded."""
        parent_map: dict[ast.AST, ast.AST] = {}
        for p in ast.walk(scope):
            for ch in ast.iter_child_nodes(p):
                parent_map[ch] = p

        calls: list[CallInfo] = []
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call):
                continue
            lineno = getattr(node, "lineno", 0)
            if max_line is not None and lineno > max_line:
                continue
            func_name = self._extract_func_name(node.func)
            if not func_name:
                continue
            parent = parent_map.get(node)
            is_stmt_expr = isinstance(parent, ast.Expr)
            calls.append(
                CallInfo(
                    func_name=func_name,
                    args=self._extract_call_args(node.args),
                    lineno=lineno,
                    is_statement_expr=is_stmt_expr,
                )
            )
        return calls

    def _detect_subject_token(self, finding_text: str) -> str | None:
        """Determine if a finding targets a specific token variant."""
        lowered = finding_text.lower()
        for t in self.TOKEN_VARIANTS:
            if t in lowered:
                return t
        return None

    def _is_matching_expiration_call(self, call_name: str) -> bool:
        """Check if call name represents an explicit expiration check."""
        lowered = call_name.lower()
        return (
            "is_expired" in lowered
            or "check_token_expiration" in lowered
            or "validate_expiration" in lowered
            or "check_expiration" in lowered
            or "verify_expiration" in lowered
            or "has_expired" in lowered
        )

    def _is_matching_expiry_compare(
        self,
        node: ast.AST,
        subject_token: str | None,
        max_line: int | None,
    ) -> bool:
        if not isinstance(node, ast.Compare):
            return False
        lineno = getattr(node, "lineno", 0)
        if max_line is not None and lineno > max_line:
            return False
        txt = ast.unparse(node)
        if "expires_at" not in txt and "expired" not in txt:
            return False
        return not subject_token or subject_token in txt

    def _has_expires_at_check(
        self,
        scope: ast.AST,
        subject_token: str | None = None,
        max_line: int | None = None,
    ) -> bool:
        """Check if AST scope directly compares expires_at on token before max_line."""
        return any(
            self._is_matching_expiry_compare(node, subject_token, max_line)
            for node in ast.walk(scope)
        )

    def _find_denominator_variable(self, scope: ast.AST, target_line: int | None) -> str | None:
        """Find the denominator identifier in a division expression at target_line."""
        for node in ast.walk(scope):
            if (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod))
                and (target_line is None or getattr(node, "lineno", 0) == target_line)
                and isinstance(node.right, ast.Name)
            ):
                return node.right.id
        for node in ast.walk(scope):
            if (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod))
                and isinstance(node.right, ast.Name)
            ):
                return node.right.id
        return None

    def _is_if_guard(self, node: ast.If, target_line: int | None, denom: str | None) -> bool:
        if target_line is not None and getattr(node, "lineno", 0) > target_line:
            return False
        if denom is None:
            return False
        names_in_test = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        if denom not in names_in_test:
            return False
        txt = ast.unparse(node.test)
        return any(
            p in txt for p in ("== 0", "!= 0", "not ", "<= 0", "is 0", "is None", "> 0", "< 0")
        )

    def _is_assert_guard(
        self, node: ast.Assert, target_line: int | None, denom: str | None
    ) -> bool:
        if target_line is not None and getattr(node, "lineno", 0) > target_line:
            return False
        if denom is None:
            return False
        names_in_test = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        if denom not in names_in_test:
            return False
        txt = ast.unparse(node.test)
        return any(p in txt for p in ("!= 0", "> 0", "not None", "is not None"))

    def _is_try_guard(self, node: ast.Try, target_line: int | None) -> bool:
        if target_line is not None:
            start = getattr(node, "lineno", 0)
            end = getattr(node, "end_lineno", start)
            if not (start <= target_line <= end):
                return False
        for h in node.handlers:
            h_name = getattr(h.type, "id", "") if h.type else ""
            if h_name in ("ZeroDivisionError", "ArithmeticError", "Exception"):
                return True
        return False

    def _has_zero_division_guard(self, scope: ast.AST, target_line: int | None) -> bool:
        """Check if AST scope contains an explicit guard protecting division on target variable."""
        denom = self._find_denominator_variable(scope, target_line)
        for sub in ast.walk(scope):
            if isinstance(sub, ast.If) and self._is_if_guard(sub, target_line, denom):
                return True
            if isinstance(sub, ast.Assert) and self._is_assert_guard(sub, target_line, denom):
                return True
            if isinstance(sub, ast.Try) and self._is_try_guard(sub, target_line):
                return True
        return False

    def _matches_call_token(
        self,
        call_name: str,
        call_args: list[str],
        subject_token: str | None,
    ) -> bool:
        if not self._is_matching_expiration_call(call_name):
            return False
        if not subject_token:
            return True
        return (subject_token in call_name) or any(subject_token in a for a in call_args)

    def _check_related_expiration(
        self,
        calls: list[CallInfo],
        finding: ReviewFinding,
        req_info: str,
        context: GitReviewContext,
        subject_token: str | None,
    ) -> str | None:
        for rel_path, rel_content in context.related_files.items():
            has_pat = any(p.search(rel_content) for p in self.COMPENSATING_PATTERNS["expiration"])
            if not has_pat:
                continue
            helper_raises = bool(re.search(r"\braise\b", rel_content))
            for call in calls:
                if not self._matches_call_token(call.func_name, call.args, subject_token):
                    continue
                # If call is a statement expr whose return value is discarded,
                # it only enforces expiration if the helper actually raises an exception
                if call.is_statement_expr and not helper_raises:
                    continue
                return (
                    f"Candidate issue {finding.id}{req_info} on '{finding.file_path}' "
                    f"claiming missing expiration was challenged: potential compensating check "
                    f"'{call.func_name}' detected in scope (defined in '{rel_path}')."
                )
        return None

    def _check_scope_expiration(
        self,
        calls: list[CallInfo],
        finding: ReviewFinding,
        req_info: str,
        subject_token: str | None,
    ) -> str | None:
        for call in calls:
            if not self._matches_call_token(call.func_name, call.args, subject_token):
                continue
            if call.is_statement_expr:
                continue
            return (
                f"Candidate issue {finding.id}{req_info} on '{finding.file_path}' "
                f"claiming missing expiration was challenged: potential compensating check "
                f"'{call.func_name}' detected in scope."
            )
        return None

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
        scope_lineno = getattr(scope, "lineno", None)
        effective_max_line = (
            None if (finding.line is not None and finding.line == scope_lineno) else finding.line
        )

        calls = self._extract_calls(scope, max_line=effective_max_line)
        subject_token = self._detect_subject_token(finding_text)
        req_info = f" ({', '.join(finding.requirement_ids)})" if finding.requirement_ids else ""

        related_note = self._check_related_expiration(
            calls, finding, req_info, context, subject_token
        )
        if related_note:
            return related_note

        scope_note = self._check_scope_expiration(calls, finding, req_info, subject_token)
        if scope_note:
            return scope_note

        if self._has_expires_at_check(scope, subject_token, max_line=effective_max_line):
            return (
                f"Candidate issue {finding.id}{req_info} on '{finding.file_path}' "
                "claiming missing expiration was challenged: expiration comparison "
                "check detected in scope."
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
                "claiming unhandled zero division was challenged: guard check found "
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
