"""Comprehensive unit tests for compensating-control and call-path validation.

Per PR #4 Mission:
1. Helper exists but is never called -> finding must be retained.
2. Helper is called on the relevant path -> finding dismissed.
3. Helper checks a different token or condition -> finding must be retained.
4. Compensating logic exists in unchanged code -> finding dismissed.
5. Evidence is insufficient to decide -> conservatively retained.
Never silently suppress a valid security or correctness finding.
"""

from __future__ import annotations

from pathlib import Path

from verifycraft.contracts.review import (
    ConventionalLabel,
    FindingSeverity,
    ReviewFinding,
)
from verifycraft.review.counterexamples import CounterexampleChecker
from verifycraft.review.git import ChangedFile, GitReviewContext


def _make_context(
    target_content: str,
    target_path: str = "src/auth.py",
    related_files: dict[str, str] | None = None,
) -> GitReviewContext:
    return GitReviewContext(
        repo_path=str(Path(".")),
        base_revision="main",
        head_revision="HEAD",
        base_commit="a" * 40,
        head_commit="b" * 40,
        raw_diff="diff --git ...",
        changed_files={
            target_path: ChangedFile(
                path=target_path,
                status="modified",
                content=target_content,
            )
        },
        related_files=related_files or {},
    )


def test_helper_exists_but_is_never_called() -> None:
    """Case 1: Helper exists in related files, but the changed code never invokes it.

    Must NOT suppress the finding!
    """
    checker = CounterexampleChecker()
    finding = ReviewFinding(
        id="f-auth-01",
        file_path="src/auth.py",
        line=2,
        label=ConventionalLabel.ISSUE,
        subject="Missing token expiration check",
        description="The authenticate handler omits token expiry verification",
        severity=FindingSeverity.HIGH,
        blocking=True,
        requirement_ids=["REQ-AUTH-01"],
    )

    # Helper is present in related file, but auth.py NEVER calls it
    context = _make_context(
        target_content="def authenticate(token):\n    return token.is_valid\n",
        related_files={
            "src/token_helper.py": (
                "def check_token_expiration(token):\n    return not token.is_expired()\n"
            )
        },
    )

    validated, notes = checker.check_compensating_controls([finding], context)
    assert len(validated) == 1
    assert validated[0].id == "f-auth-01"
    assert len(notes) == 0


def test_helper_called_on_relevant_path() -> None:
    """Case 2: Helper exists and IS verified called on the execution path.

    Should dismiss the finding with a traceable note.
    """
    checker = CounterexampleChecker()
    finding = ReviewFinding(
        id="f-auth-02",
        file_path="src/auth.py",
        line=3,
        label=ConventionalLabel.ISSUE,
        subject="Missing token expiration check",
        description="The authenticate handler omits token expiry verification",
        severity=FindingSeverity.HIGH,
        blocking=True,
        requirement_ids=["REQ-AUTH-01"],
    )

    context = _make_context(
        target_content=(
            "from token_helper import check_token_expiration\n"
            "def authenticate(token):\n"
            "    check_token_expiration(token)\n"
            "    return token.is_valid\n"
        ),
        related_files={
            "src/token_helper.py": (
                "def check_token_expiration(token):\n    return not token.is_expired()\n"
            )
        },
    )

    validated, notes = checker.check_compensating_controls([finding], context)
    assert len(validated) == 0
    assert len(notes) == 1
    assert "f-auth-02" in notes[0]
    assert "REQ-AUTH-01" in notes[0]
    assert "call to 'check_token_expiration' verified on execution path" in notes[0]


def test_helper_checks_different_token_or_condition() -> None:
    """Case 3: Helper checks a different token (e.g. refresh_token instead of session_token).

    Must NOT suppress the finding for session_token!
    """
    checker = CounterexampleChecker()
    finding = ReviewFinding(
        id="f-auth-03",
        file_path="src/auth.py",
        line=3,
        label=ConventionalLabel.ISSUE,
        subject="Missing session_token expiration check",
        description=(
            "The authenticate handler checks refresh_token expiry "
            "but omits session_token verification"
        ),
        severity=FindingSeverity.HIGH,
        blocking=True,
        requirement_ids=["REQ-AUTH-02"],
    )

    # Function calls check_token_expiration on refresh_token, but finding is about session_token
    context = _make_context(
        target_content=(
            "from token_helper import check_token_expiration\n"
            "def authenticate(session_token, refresh_token):\n"
            "    check_token_expiration(refresh_token)\n"
            "    return session_token.user_id\n"
        ),
        related_files={
            "src/token_helper.py": (
                "def check_token_expiration(token):\n    return not token.is_expired()\n"
            )
        },
    )

    validated, notes = checker.check_compensating_controls([finding], context)
    assert len(validated) == 1
    assert validated[0].id == "f-auth-03"
    assert len(notes) == 0


def test_compensating_logic_exists_in_unchanged_code() -> None:
    """Case 4: Compensating logic exists in unchanged code within the same function scope.

    Should dismiss the finding because the guard is already present in surrounding scope.
    """
    checker = CounterexampleChecker()
    finding = ReviewFinding(
        id="f-auth-04",
        file_path="src/auth.py",
        line=4,
        label=ConventionalLabel.ISSUE,
        subject="Missing token expiration check",
        description="The handler omits token expiry verification",
        severity=FindingSeverity.HIGH,
        blocking=True,
        requirement_ids=["REQ-AUTH-01"],
    )

    # Function has existing timestamp comparison in unchanged surrounding scope
    context = _make_context(
        target_content=(
            "def authenticate(token):\n"
            "    if token.expires_at < current_time():\n"
            "        raise ExpiredTokenError()\n"
            "    return token.is_valid\n"
        ),
        related_files={},
    )

    validated, notes = checker.check_compensating_controls([finding], context)
    assert len(validated) == 0
    assert len(notes) == 1
    assert "f-auth-04" in notes[0]
    assert "expiration comparison check verified in scope" in notes[0]


def test_evidence_is_insufficient_to_decide() -> None:
    """Case 5: Evidence is insufficient or ambiguous (e.g. syntax error or non-Python code).

    Must conservatively retain the finding.
    """
    checker = CounterexampleChecker()
    finding = ReviewFinding(
        id="f-auth-05",
        file_path="src/auth.py",
        line=2,
        label=ConventionalLabel.ISSUE,
        subject="Missing token expiration check",
        description="The handler omits token expiry verification",
        severity=FindingSeverity.HIGH,
        blocking=True,
        requirement_ids=["REQ-AUTH-01"],
    )

    # Corrupted / invalid syntax where AST parsing fails
    context = _make_context(
        target_content="def broken_syntax( { : not valid python at all !!!",
        related_files={"src/token_helper.py": "def is_expired(): return True"},
    )

    validated, notes = checker.check_compensating_controls([finding], context)
    assert len(validated) == 1
    assert validated[0].id == "f-auth-05"
    assert len(notes) == 0


def test_zero_division_guards_assert_and_try() -> None:
    """Verifies that assert guards and try/except handlers dismiss zero division findings."""
    checker = CounterexampleChecker()
    finding_assert = ReviewFinding(
        id="f-div-01",
        file_path="src/calc.py",
        line=4,
        label=ConventionalLabel.ISSUE,
        subject="Unhandled zero division",
        description="b could be zero in denominator",
        severity=FindingSeverity.HIGH,
    )
    context_assert = _make_context(
        target_content="def divide(a, b):\n    assert b != 0\n    return a / b\n",
        target_path="src/calc.py",
    )
    val1, notes1 = checker.check_compensating_controls([finding_assert], context_assert)
    assert len(val1) == 0
    assert len(notes1) == 1
    assert "guard check found protecting division" in notes1[0]

    # Try/except handler
    finding_try = ReviewFinding(
        id="f-div-02",
        file_path="src/calc.py",
        line=4,
        label=ConventionalLabel.ISSUE,
        subject="Unhandled zero division",
        description="b could be zero in denominator",
        severity=FindingSeverity.HIGH,
    )
    context_try = _make_context(
        target_content=(
            "def divide(a, b):\n"
            "    try:\n"
            "        return a / b\n"
            "    except ZeroDivisionError:\n"
            "        return 0\n"
        ),
        target_path="src/calc.py",
    )
    val2, notes2 = checker.check_compensating_controls([finding_try], context_try)
    assert len(val2) == 0
    assert len(notes2) == 1


def test_zero_division_syntax_error_and_missing_file() -> None:
    """Verifies zero division does not suppress on syntax error or missing content."""
    checker = CounterexampleChecker()
    finding = ReviewFinding(
        id="f-div-03",
        file_path="src/calc.py",
        line=4,
        label=ConventionalLabel.ISSUE,
        subject="Unhandled zero division",
        description="denominator may be zero",
        severity=FindingSeverity.HIGH,
    )
    # Syntax error
    ctx_bad = _make_context(
        target_content="def broken_syntax( { : 123",
        target_path="src/calc.py",
    )
    val_bad, _ = checker.check_compensating_controls([finding], ctx_bad)
    assert len(val_bad) == 1

    # Missing file content
    ctx_missing = _make_context(
        target_content="",
        target_path="src/calc.py",
    )
    ctx_missing.changed_files["src/calc.py"].content = None
    val_missing, _ = checker.check_compensating_controls([finding], ctx_missing)
    assert len(val_missing) == 1


def test_attribute_method_calls_and_expires_at_subject_mismatch() -> None:
    """Verifies attribute method calls and token filtering on direct expires_at comparisons."""
    checker = CounterexampleChecker()

    # Method call on attribute (self.validator.check_token_expiration(self.token))
    finding = ReviewFinding(
        id="f-auth-06",
        file_path="src/auth.py",
        line=3,
        label=ConventionalLabel.ISSUE,
        subject="Missing token expiration check",
        description="The handler omits token expiry",
        severity=FindingSeverity.HIGH,
    )
    ctx_method = _make_context(
        target_content=(
            "class Auth:\n"
            "    def verify(self):\n"
            "        self.validator.check_token_expiration(self.token)\n"
            "        return True\n"
        )
    )
    val1, notes1 = checker.check_compensating_controls([finding], ctx_method)
    assert len(val1) == 0
    assert len(notes1) == 1

    # Direct expires_at comparison on different token variant (refresh_token vs session_token)
    finding_mismatch = ReviewFinding(
        id="f-auth-07",
        file_path="src/auth.py",
        line=4,
        label=ConventionalLabel.ISSUE,
        subject="Missing session_token expiration check",
        description="The handler omits session_token expiry verification",
        severity=FindingSeverity.HIGH,
    )
    ctx_diff = _make_context(
        target_content=(
            "def authenticate(session_token, refresh_token):\n"
            "    if refresh_token.expires_at < now():\n"
            "        raise Expired()\n"
            "    return session_token.id\n"
        )
    )
    val2, notes2 = checker.check_compensating_controls([finding_mismatch], ctx_diff)
    assert len(val2) == 1
    assert len(notes2) == 0
