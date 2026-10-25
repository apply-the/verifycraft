"""Regression tests for PR #4 Final Correctness Gate on CounterexampleChecker.

Audit cases:
A. Unrelated token validation: verify_token(token) validates signature, not expiration.
B. Ignored return value: check_token_expiration(token) return value is discarded.
C. Unrelated division guard: if debug == 0: does not guard a / b.
D. Guard after dangerous operation: guard executed after division/consumption.
E. Valid protection: correctly enforced check before operation.
"""

from __future__ import annotations

import ast
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


def test_case_a_unrelated_token_validation() -> None:
    """Case A: verify_token(token) validates signature, not token expiration.

    A missing-expiration finding must NOT be dismissed.
    """
    checker = CounterexampleChecker()
    finding = ReviewFinding(
        id="f-auth-01",
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
            "def authenticate(token):\n"
            "    verify_token(token)  # Validates signature only\n"
            "    return token.is_valid\n"
        ),
        related_files={"src/token.py": "def verify_token(t): return True\n"},
    )

    validated, notes = checker.check_compensating_controls([finding], context)
    assert len(validated) == 1
    assert validated[0].id == "f-auth-01"
    assert len(notes) == 0


def test_case_b_ignored_return_value() -> None:
    """Case B: check_token_expiration(token) returns False on expiry, but return value is ignored.

    Must NOT assume expiry is enforced if the validation result is ignored.
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
            "def authenticate(token):\n"
            "    check_token_expiration(token)  # Returns False on expiry, ignored!\n"
            "    return token.is_valid\n"
        ),
        related_files={
            "src/token.py": "def check_token_expiration(t):\n    return not t.is_expired()\n"
        },
    )

    validated, notes = checker.check_compensating_controls([finding], context)
    assert len(validated) == 1
    assert validated[0].id == "f-auth-02"
    assert len(notes) == 0


def test_case_c_unrelated_division_guard() -> None:
    """Case C: if debug == 0: guards debug flag, NOT denominator b in a / b.

    Must NOT dismiss unhandled division by zero on b.
    """
    checker = CounterexampleChecker()
    finding = ReviewFinding(
        id="f-div-01",
        file_path="src/calc.py",
        line=4,
        label=ConventionalLabel.ISSUE,
        subject="Unhandled zero division",
        description="Denominator b may be zero",
        severity=FindingSeverity.HIGH,
        blocking=True,
    )

    context = _make_context(
        target_content=(
            "def divide(a, b, debug):\n"
            "    if debug == 0:\n"
            "        log_warning()\n"
            "    return a / b\n"
        ),
        target_path="src/calc.py",
    )

    validated, notes = checker.check_compensating_controls([finding], context)
    assert len(validated) == 1
    assert validated[0].id == "f-div-01"
    assert len(notes) == 0


def test_case_d_guard_after_dangerous_operation() -> None:
    """Case D: Guard executed only AFTER division or token-consuming operation.

    Must NOT suppress the finding.
    """
    checker = CounterexampleChecker()
    # 1. Division executed before guard
    finding_div = ReviewFinding(
        id="f-div-02",
        file_path="src/calc.py",
        line=2,
        label=ConventionalLabel.ISSUE,
        subject="Unhandled zero division",
        description="Denominator b may be zero",
        severity=FindingSeverity.HIGH,
        blocking=True,
    )
    context_div = _make_context(
        target_content=(
            "def divide(a, b):\n"
            "    res = a / b\n"
            "    if b == 0:\n"
            "        raise ValueError('b was zero')\n"
            "    return res\n"
        ),
        target_path="src/calc.py",
    )
    val_div, notes_div = checker.check_compensating_controls([finding_div], context_div)
    assert len(val_div) == 1
    assert val_div[0].id == "f-div-02"

    # 2. Expiration check after token consumed
    finding_auth = ReviewFinding(
        id="f-auth-04",
        file_path="src/auth.py",
        line=2,
        label=ConventionalLabel.ISSUE,
        subject="Missing token expiration check",
        description="Token used before expiry verification",
        severity=FindingSeverity.HIGH,
        blocking=True,
    )
    context_auth = _make_context(
        target_content=(
            "def authenticate(token):\n"
            "    user_id = token.get_user()\n"
            "    if is_expired(token):\n"
            "        raise ExpiredError()\n"
            "    return user_id\n"
        )
    )
    val_auth, notes_auth = checker.check_compensating_controls([finding_auth], context_auth)
    assert len(val_auth) == 1
    assert val_auth[0].id == "f-auth-04"


def test_case_e_valid_protection() -> None:
    """Case E: Valid protection on relevant variable and execution path before operation.

    Should be recognized without false-positive comments.
    """
    checker = CounterexampleChecker()
    # 1. Valid expiration guard
    finding_auth = ReviewFinding(
        id="f-auth-05",
        file_path="src/auth.py",
        line=4,
        label=ConventionalLabel.ISSUE,
        subject="Missing token expiration check",
        description="The authenticate handler omits token expiry",
        severity=FindingSeverity.HIGH,
        blocking=True,
    )
    context_auth = _make_context(
        target_content=(
            "def authenticate(token):\n"
            "    if is_expired(token):\n"
            "        raise ExpiredError('Token is expired')\n"
            "    return token.is_valid\n"
        )
    )
    val_auth, notes_auth = checker.check_compensating_controls([finding_auth], context_auth)
    assert len(val_auth) == 0
    assert len(notes_auth) == 1
    assert "f-auth-05" in notes_auth[0]

    # 2. Valid zero division guard
    finding_div = ReviewFinding(
        id="f-div-05",
        file_path="src/calc.py",
        line=4,
        label=ConventionalLabel.ISSUE,
        subject="Unhandled zero division",
        description="Denominator b may be zero",
        severity=FindingSeverity.HIGH,
        blocking=True,
    )
    context_div = _make_context(
        target_content=(
            "def divide(a, b):\n"
            "    if b == 0:\n"
            "        raise ZeroDivisionError('b is 0')\n"
            "    return a / b\n"
        ),
        target_path="src/calc.py",
    )
    val_div, notes_div = checker.check_compensating_controls([finding_div], context_div)
    assert len(val_div) == 0
    assert len(notes_div) == 1
    assert "f-div-05" in notes_div[0]


def test_zero_division_assert_and_try_guards() -> None:
    checker = CounterexampleChecker()

    # Assert guard: assert b != 0
    finding_assert = ReviewFinding(
        id="f-assert-01",
        file_path="src/calc.py",
        line=3,
        label=ConventionalLabel.ISSUE,
        subject="Zero division",
        description="Division by b without guard",
        severity=FindingSeverity.HIGH,
        blocking=True,
    )
    ctx_assert = _make_context(
        target_content="def divide(a, b):\n    assert b != 0\n    return a / b\n",
        target_path="src/calc.py",
    )
    val, notes = checker.check_compensating_controls([finding_assert], ctx_assert)
    assert len(val) == 0
    assert len(notes) == 1

    # Try guard: try / except ZeroDivisionError
    finding_try = ReviewFinding(
        id="f-try-01",
        file_path="src/calc.py",
        line=3,
        label=ConventionalLabel.ISSUE,
        subject="Zero division",
        description="Division by b without guard",
        severity=FindingSeverity.HIGH,
        blocking=True,
    )
    ctx_try = _make_context(
        target_content=(
            "def divide(a, b):\n"
            "    try:\n"
            "        return a / b\n"
            "    except ZeroDivisionError:\n"
            "        return 0\n"
        ),
        target_path="src/calc.py",
    )
    val2, notes2 = checker.check_compensating_controls([finding_try], ctx_try)
    assert len(val2) == 0
    assert len(notes2) == 1

    try_node = ast.parse("try:\n    pass\nexcept Exception:\n    pass").body[0]
    assert isinstance(try_node, ast.Try)
    assert checker._is_try_guard(try_node, target_line=None) is True


def test_counterexample_corner_cases() -> None:
    checker = CounterexampleChecker()

    # Division by literal constant (no denominator variable)
    finding_const = ReviewFinding(
        id="f-const-01",
        file_path="src/calc.py",
        line=2,
        label=ConventionalLabel.ISSUE,
        subject="Zero division",
        description="Division by constant",
        severity=FindingSeverity.HIGH,
        blocking=True,
    )
    ctx_const = _make_context(
        target_content="def divide(a):\n    return a / 2\n",
        target_path="src/calc.py",
    )
    val_c, _ = checker.check_compensating_controls([finding_const], ctx_const)
    assert len(val_c) == 1  # Finding remains (no variable denominator to guard)

    # Lambda call / unextractable function name in scope
    finding_lambda = ReviewFinding(
        id="f-lambda-01",
        file_path="src/auth.py",
        line=3,
        label=ConventionalLabel.ISSUE,
        subject="Missing token expiration check",
        description="No expiration check",
        severity=FindingSeverity.HIGH,
        blocking=True,
    )
    ctx_lambda = _make_context(
        target_content="def auth(token):\n    (lambda x: x)(token)\n    return token.is_valid\n",
        target_path="src/auth.py",
    )
    val_l, _ = checker.check_compensating_controls([finding_lambda], ctx_lambda)
    assert len(val_l) == 1  # Finding remains


def test_guard_branches_and_corner_cases() -> None:
    checker = CounterexampleChecker()

    # 1. Missing target file or content is None in _check_expiration_compensation
    f_missing = ReviewFinding(
        id="f-miss",
        file_path="src/missing.py",
        line=2,
        label=ConventionalLabel.ISSUE,
        subject="Missing token expiration check",
        description="Missing expiration",
        severity=FindingSeverity.HIGH,
    )
    val_m, _ = checker.check_compensating_controls([f_missing], _make_context(target_content=""))
    assert len(val_m) == 1

    # 2. Compare node with unrelated expression and compare node after max_line
    ctx_compare = _make_context(
        target_content=(
            "def authenticate(token):\n"
            "    x = 10\n"
            "    if x > 5:\n"  # unrelated compare
            "        pass\n"
            "    return token.is_valid\n"
            "    if token.expires_at < 100: pass\n"  # after finding line 2
        )
    )
    f_comp = ReviewFinding(
        id="f-comp",
        file_path="src/auth.py",
        line=5,
        label=ConventionalLabel.ISSUE,
        subject="Missing token expiration check",
        description="Missing expiration",
        severity=FindingSeverity.HIGH,
    )
    val_comp, _ = checker.check_compensating_controls([f_comp], ctx_compare)
    assert len(val_comp) == 1

    # 3. Assert guard: assert after division, assert on unrelated variable
    ctx_assert_after = _make_context(
        target_content=(
            "def divide(a, b):\n"
            "    res = a / b\n"  # line 2
            "    assert b != 0\n"  # line 3: too late
            "    return res\n"
        ),
        target_path="src/calc.py",
    )
    f_assert_after = ReviewFinding(
        id="f-aa",
        file_path="src/calc.py",
        line=2,
        label=ConventionalLabel.ISSUE,
        subject="Zero division",
        description="Denominator b may be zero",
        severity=FindingSeverity.HIGH,
    )
    val_aa, _ = checker.check_compensating_controls([f_assert_after], ctx_assert_after)
    assert len(val_aa) == 1

    ctx_assert_unrel = _make_context(
        target_content=("def divide(a, b):\n    assert debug > 0\n    return a / b\n"),
        target_path="src/calc.py",
    )
    f_assert_unrel = ReviewFinding(
        id="f-au",
        file_path="src/calc.py",
        line=3,
        label=ConventionalLabel.ISSUE,
        subject="Zero division",
        description="Denominator b may be zero",
        severity=FindingSeverity.HIGH,
    )
    val_au, _ = checker.check_compensating_controls([f_assert_unrel], ctx_assert_unrel)
    assert len(val_au) == 1

    # 4. Try guard: division outside try block, and try block with non-matching exception (KeyError)
    ctx_try_outside = _make_context(
        target_content=(
            "def divide(a, b):\n"
            "    try:\n"
            "        x = 1\n"
            "    except KeyError:\n"
            "        return 0\n"
            "    return a / b\n"  # line 6: outside try block
        ),
        target_path="src/calc.py",
    )
    f_try_outside = ReviewFinding(
        id="f-to",
        file_path="src/calc.py",
        line=6,
        label=ConventionalLabel.ISSUE,
        subject="Zero division",
        description="Denominator b may be zero",
        severity=FindingSeverity.HIGH,
    )
    val_to, _ = checker.check_compensating_controls([f_try_outside], ctx_try_outside)
    assert len(val_to) == 1

    ctx_try_keyerror = _make_context(
        target_content=(
            "def divide(a, b):\n"
            "    try:\n"
            "        return a / b\n"
            "    except KeyError:\n"
            "        return 0\n"
        ),
        target_path="src/calc.py",
    )
    f_try_keyerror = ReviewFinding(
        id="f-tk",
        file_path="src/calc.py",
        line=3,
        label=ConventionalLabel.ISSUE,
        subject="Zero division",
        description="Denominator b may be zero",
        severity=FindingSeverity.HIGH,
    )
    val_tk, _ = checker.check_compensating_controls([f_try_keyerror], ctx_try_keyerror)
    assert len(val_tk) == 1
