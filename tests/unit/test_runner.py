from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from verifycraft.contracts import (
    CheckSpec,
    CheckType,
    ExecutionMode,
    ExecutionPolicy,
)
from verifycraft.core.runner import FakeRunner, TrustedPytestRunner


def test_fake_runner_deterministic_execution() -> None:
    runner = FakeRunner()
    runner.set_response("chk-1", exit_code=1, stdout="fake failure", stderr="")

    spec = CheckSpec(
        check_id="chk-1",
        check_type=CheckType.FAKE,
        target_path="dummy.py",
    )
    policy = ExecutionPolicy(mode=ExecutionMode.FAKE)
    evidence = runner.run(spec, repo_path=".", policy=policy, fingerprint="sha256:123")

    assert evidence.exit_code == 1
    assert evidence.stdout == "fake failure"
    assert evidence.fingerprint == "sha256:123"
    assert len(runner.invocations) == 1


def test_fake_runner_output_truncation() -> None:
    runner = FakeRunner()
    runner.set_response("chk-1", exit_code=0, stdout="A" * 2000, stderr="")

    spec = CheckSpec(check_id="chk-1", check_type=CheckType.FAKE, target_path="dummy.py")
    policy = ExecutionPolicy(mode=ExecutionMode.FAKE, max_output_bytes=1024)
    evidence = runner.run(spec, repo_path=".", policy=policy, fingerprint="sha256:123")

    assert evidence.output_truncated is True
    assert len(evidence.stdout) == 1024


def test_trusted_pytest_runner_basic_execution() -> None:
    with TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "test_simple.py"
        test_file.write_text("def test_ok(): pass\n")

        spec = CheckSpec(
            check_id="chk-pytest-1",
            check_type=CheckType.PYTEST,
            target_path="test_simple.py",
            args=["-q"],
        )
        policy = ExecutionPolicy(mode=ExecutionMode.TRUSTED_LOCAL, timeout_seconds=10.0)
        runner = TrustedPytestRunner()

        evidence = runner.run(spec, repo_path=tmpdir, policy=policy, fingerprint="sha256:abc")
        assert evidence.exit_code == 0
        assert "passed" in evidence.stdout
        assert evidence.fingerprint == "sha256:abc"
        assert evidence.output_truncated is False


def test_trusted_pytest_runner_timeout() -> None:
    with TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "test_slow.py"
        test_file.write_text("import time\ndef test_sleep(): time.sleep(5)\n")

        spec = CheckSpec(
            check_id="chk-pytest-timeout",
            check_type=CheckType.PYTEST,
            target_path="test_slow.py",
        )
        # Timeout after 0.5s
        policy = ExecutionPolicy(mode=ExecutionMode.TRUSTED_LOCAL, timeout_seconds=0.5)
        runner = TrustedPytestRunner()

        evidence = runner.run(spec, repo_path=tmpdir, policy=policy, fingerprint="sha256:time")
        assert evidence.exit_code == 124
        assert "timed out" in evidence.stderr.lower()


def test_trusted_pytest_runner_streaming_output_truncation() -> None:
    with TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "test_spam.py"
        test_file.write_text(
            "def test_spam():\n"
            "    for _ in range(500):\n"
            "        print('SPAM_LINE_' * 20)\n"
            "    assert False\n"
        )
        spec = CheckSpec(
            check_id="chk-pytest-spam",
            check_type=CheckType.PYTEST,
            target_path="test_spam.py",
            args=["-s"],
        )
        policy = ExecutionPolicy(
            mode=ExecutionMode.TRUSTED_LOCAL,
            max_output_bytes=1024,
            timeout_seconds=10.0,
        )
        runner = TrustedPytestRunner()

        evidence = runner.run(spec, repo_path=tmpdir, policy=policy, fingerprint="sha256:spam")
        assert evidence.output_truncated is True
        assert len(evidence.stdout) <= 1024


def test_trusted_pytest_runner_os_error_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    runner = TrustedPytestRunner()
    spec = CheckSpec(
        check_id="chk-err",
        check_type=CheckType.PYTEST,
        target_path="dummy.py",
    )
    policy = ExecutionPolicy(mode=ExecutionMode.TRUSTED_LOCAL)

    def mock_popen(*args: Any, **kwargs: Any) -> Any:
        raise OSError("Permission denied or broken pipe")

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    evidence = runner.run(spec, repo_path=".", policy=policy, fingerprint="sha256:err")
    assert evidence.exit_code == 1
    assert "OS error" in evidence.stderr
