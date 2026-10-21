"""Integration tests for VerifyCraft trusted pytest execution with real subprocesses."""

from pathlib import Path
from tempfile import TemporaryDirectory

from verifycraft import (
    CheckSpec,
    CheckType,
    ExecutionMode,
    ExecutionPolicy,
    ExpectedObservation,
    Hypothesis,
    Verdict,
    VerificationEngine,
    VerificationInput,
)


def _setup_test_repo(base_dir: Path) -> tuple[Path, Path]:
    src_dir = base_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = base_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    calc_file = src_dir / "calc.py"
    calc_file.write_text(
        "def compute_discount(price: float, discount: float) -> float:\n"
        "    if discount < 0:\n"
        "        raise ValueError('Discount cannot be negative')\n"
        "    return price * (1 - discount)\n"
    )

    test_file = tests_dir / "test_calc.py"
    test_file.write_text(
        "import pytest\n"
        "from src.calc import compute_discount\n\n"
        "def test_negative_discount():\n"
        "    with pytest.raises(ValueError, match='Discount cannot be negative'):\n"
        "        compute_discount(100.0, -0.2)\n\n"
        "def test_normal_discount():\n"
        "    assert compute_discount(100.0, 0.1) == 90.0\n"
    )
    return calc_file, test_file


def test_real_pytest_supported_regression_check() -> None:
    with TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        calc_file, test_file = _setup_test_repo(tmp_path)

        # Introduce a regression in calc.py (e.g. division by zero or unexpected error)
        calc_file.write_text(
            "def compute_discount(price: float, discount: float) -> float:\n"
            "    if discount < 0:\n"
            "        raise ZeroDivisionError('Unexpected division error on negative discount')\n"
            "    return price * (1 - discount)\n"
        )

        hyp = Hypothesis(
            id="hyp-reg-zero-div",
            claim="Negative discount triggers unexpected ZeroDivisionError regression",
            expected_observation=ExpectedObservation(
                expected_outcome="fail",
                expected_exit_codes=[1],
                match_patterns=["ZeroDivisionError: Unexpected division error"],
            ),
        )
        spec = CheckSpec(
            check_id="chk-pytest-regression",
            check_type=CheckType.PYTEST,
            target_path="tests/test_calc.py",
            args=["-k", "test_negative_discount", "-q"],
        )
        v_input = VerificationInput(
            repo_path=str(tmp_path),
            revision="git-sha-buggy",
            diff="--- a/src/calc.py\n+++ b/src/calc.py",
            hypothesis=hyp,
            check_spec=spec,
        )

        policy = ExecutionPolicy(
            mode=ExecutionMode.TRUSTED_LOCAL,
            timeout_seconds=15.0,
        )

        engine = VerificationEngine()
        result = engine.verify(v_input, policy=policy)

        assert result.verdict == Verdict.SUPPORTED
        assert len(result.evidence) == 1
        assert result.evidence[0].exit_code == 1
        assert "ZeroDivisionError" in result.evidence[0].stdout


def test_real_pytest_refuted_regression_check() -> None:
    with TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        _setup_test_repo(tmp_path)

        # The test actually passes cleanly, so the hypothesis of regression failure is refuted
        hyp = Hypothesis(
            id="hyp-reg-false",
            claim="Normal discount calculation causes failure",
            expected_observation=ExpectedObservation(
                expected_outcome="fail",
                expected_exit_codes=[1],
                match_patterns=["AssertionError"],
            ),
        )
        spec = CheckSpec(
            check_id="chk-pytest-pass",
            check_type=CheckType.PYTEST,
            target_path="tests/test_calc.py",
            args=["-k", "test_normal_discount", "-q"],
        )
        v_input = VerificationInput(
            repo_path=str(tmp_path),
            revision="git-sha-clean",
            diff=None,
            hypothesis=hyp,
            check_spec=spec,
        )

        policy = ExecutionPolicy(
            mode=ExecutionMode.TRUSTED_LOCAL,
            timeout_seconds=15.0,
        )

        engine = VerificationEngine()
        result = engine.verify(v_input, policy=policy)

        assert result.verdict == Verdict.REFUTED
        assert len(result.evidence) == 1
        assert result.evidence[0].exit_code == 0
