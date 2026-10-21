"""Unit tests for the VerifyCraft CLI."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from verifycraft.cli import main
from verifycraft.contracts import (
    CheckSpec,
    CheckType,
    ExpectedObservation,
    Hypothesis,
    VerificationInput,
)


def test_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "VerifyCraft" in captured.out


def test_cli_verify_dry_run_summary(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "verify",
                "--repo",
                ".",
                "--target",
                "tests/unit/test_smoke.py",
                "--claim",
                "Smoke test passes",
                "--mode",
                "dry_run",
            ]
        )
    # Blocked verdict produces exit code 4
    assert exc_info.value.code == 4
    captured = capsys.readouterr()
    assert "Verdict:       BLOCKED" in captured.out
    assert "dry_run" in captured.out


def test_cli_verify_trusted_local_summary_and_json(capsys: pytest.CaptureFixture[str]) -> None:
    # Summary format: smoke test passes cleanly, so failure claim is refuted -> exit 2
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "verify",
                "--repo",
                ".",
                "--target",
                "tests/unit/test_smoke.py",
                "--claim",
                "Smoke test fails",
                "--mode",
                "trusted_local",
                "--match-pattern",
                "FAILED",
            ]
        )
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "Verdict:" in captured.out

    # JSON format: smoke test passes cleanly, refuting failure -> exit 2
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "verify",
                "--repo",
                ".",
                "--target",
                "tests/unit/test_smoke.py",
                "--claim",
                "Smoke test fails",
                "--mode",
                "trusted_local",
                "--format",
                "json",
            ]
        )
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["schema_version"] == "verifycraft.v0"
    assert data["verdict"] == "refuted"


def test_cli_verify_from_json_input(capsys: pytest.CaptureFixture[str]) -> None:
    with TemporaryDirectory() as tmpdir:
        input_file = Path(tmpdir) / "input.json"
        v_input = VerificationInput(
            repo_path=tmpdir,
            hypothesis=Hypothesis(
                id="hyp-cli-test",
                claim="Test claim",
                expected_observation=ExpectedObservation(
                    expected_outcome="fail",
                    match_patterns=["Error"],
                ),
            ),
            check_spec=CheckSpec(
                check_id="chk-1",
                check_type=CheckType.PYTEST,
                target_path="test_file.py",
            ),
        )
        input_file.write_text(v_input.model_dump_json())

        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "verify",
                    "--input",
                    str(input_file),
                    "--format",
                    "json",
                ]
            )
        # Default policy mode is dry_run, producing BLOCKED (exit code 4)
        assert exc_info.value.code == 4
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["schema_version"] == "verifycraft.v0"
        assert data["verdict"] == "blocked"


def test_cli_missing_required_args(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["verify"])
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Error:" in captured.err


def test_cli_bad_input_file(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["verify", "--input", "non_existent_file.json"])
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Error reading input JSON" in captured.err
