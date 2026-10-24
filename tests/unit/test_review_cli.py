"""Unit and CLI tests for verifycraft review subcommand."""

import subprocess
from pathlib import Path

import pytest

from verifycraft.cli import main


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Tester"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.local"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def test_cli_review_help(capsys: pytest.CaptureFixture[str]) -> None:
    try:
        main(["review", "--help"])
    except SystemExit as e:
        assert e.code == 0
    out = capsys.readouterr().out
    assert "review" in out
    assert "--base" in out
    assert "--head" in out
    assert "--requirements" in out
    assert "--provider" in out
    assert "--strict" in out


def test_cli_review_markdown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _init_repo(tmp_path)
    f = tmp_path / "mod.py"
    f.write_text("def a(): return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Init"], cwd=tmp_path, check=True, capture_output=True)

    f.write_text("def a(): return 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Change"], cwd=tmp_path, check=True, capture_output=True)

    try:
        main(
            [
                "review",
                str(tmp_path),
                "--base",
                "HEAD~1",
                "--head",
                "HEAD",
                "--provider",
                "deterministic",
                "--format",
                "markdown",
            ]
        )
    except SystemExit as e:
        assert e.code == 0

    out = capsys.readouterr().out
    assert "# VerifyCraft Code Review Report" in out


def test_cli_review_comments_format(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _init_repo(tmp_path)
    f = tmp_path / "mod.py"
    f.write_text("def a(): return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Init"], cwd=tmp_path, check=True, capture_output=True)

    f.write_text("def a(): return 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Change"], cwd=tmp_path, check=True, capture_output=True)

    try:
        main(
            [
                "review",
                str(tmp_path),
                "--base",
                "HEAD~1",
                "--head",
                "HEAD",
                "--provider",
                "deterministic",
                "--format",
                "comments",
            ]
        )
    except SystemExit as e:
        assert e.code == 0

    out = capsys.readouterr().out
    assert "Review Summary" in out


def test_cli_review_strict_blocking(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    docs = tmp_path / "docs"
    docs.mkdir()

    f = src / "auth.py"
    f.write_text("# Initial\n", encoding="utf-8")
    r = docs / "auth.md"
    r.write_text("- **[REQ-AUTH-01]**: Tokens expire after 15 minutes.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Init"], cwd=tmp_path, check=True, capture_output=True)

    f.write_text("def authenticate_user(t): return create_session(t)\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Change"], cwd=tmp_path, check=True, capture_output=True)

    try:
        main(
            [
                "review",
                str(tmp_path),
                "--base",
                "HEAD~1",
                "--head",
                "HEAD",
                "--requirements",
                str(r),
                "--provider",
                "deterministic",
                "--strict",
            ]
        )
    except SystemExit as e:
        # Strict mode exits 2 on blocking findings!
        assert e.code == 2


def test_cli_review_git_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Non-git directory causes clean error and exit 1
    try:
        main(["review", str(tmp_path), "--provider", "deterministic"])
    except SystemExit as e:
        assert e.code == 1
    err = capsys.readouterr().err
    assert "VerifyCraft Git Error" in err
