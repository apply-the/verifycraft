"""Unit tests for Git revision provenance and context integrity.

Per PR #4 Mission:
- Context attributed to a Git revision must be read strictly from that revision.
- Do not silently substitute working-tree files when git show fails.
- Keep uncommitted working-tree inspection as an explicit, separately labeled mode.
- Preserve correct base/head SHAs, changed-line anchors, and scoped-context omissions.
- Add regression tests for dirty working trees, missing files and divergent branches.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from verifycraft.review.git import (
    GitContextCollector,
    GitError,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Initialize a git repository with standard test branches."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

    init_file = repo / "init.txt"
    init_file.write_text("initial commit\n", encoding="utf-8")
    subprocess.run(["git", "add", "init.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def test_committed_ref_never_falls_back_to_working_tree(git_repo: Path) -> None:
    """Ensures that reading a committed revision never falls back to dirty disk state."""
    collector = GitContextCollector(str(git_repo))

    committed_file = git_repo / "module.py"
    committed_file.write_text("COMMITTED_VERSION\n", encoding="utf-8")
    subprocess.run(["git", "add", "module.py"], cwd=git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add module"], cwd=git_repo, check=True, capture_output=True
    )

    # Now dirty the working tree file on disk
    committed_file.write_text("DIRTY_UNCOMMITTED_VERSION\n", encoding="utf-8")

    # Reading committed HEAD must return the committed content, NOT the dirty disk content
    content = collector.read_file_at_ref("module.py", "HEAD")
    assert content == "COMMITTED_VERSION\n"

    # Even if git show fails, it must return None, NOT local disk content!
    with patch.object(collector, "_run_git", side_effect=GitError("git show failed")):
        assert collector.read_file_at_ref("module.py", "HEAD") is None


def test_explicit_working_tree_mode(git_repo: Path) -> None:
    """Ensures that uncommitted working-tree inspection is explicit and correctly labeled."""
    collector = GitContextCollector(str(git_repo))

    # Create uncommitted dirty file in working tree
    dirty_file = git_repo / "uncommitted.py"
    dirty_file.write_text("x = 42\n", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "uncommitted.py"], cwd=git_repo, check=True)

    # When ref is WORKTREE, it reads from disk and labels correctly
    content = collector.read_file_at_ref("uncommitted.py", "WORKTREE")
    assert content == "x = 42\n"

    context = collector.collect(base_revision="main", head_revision="WORKTREE")
    assert context.is_working_tree is True
    assert "WORKTREE" in context.head_commit
    assert "uncommitted.py" in context.changed_files


def test_missing_file_at_ref(git_repo: Path) -> None:
    """Ensures missing files at ref return None without raising uncaught exceptions."""
    collector = GitContextCollector(str(git_repo))
    res = collector.read_file_at_ref("nonexistent_file_xyz.py", "HEAD")
    assert res is None


def test_divergent_branches_merge_base(git_repo: Path) -> None:
    """Ensures three-dot diff calculates against merge-base on divergent branches."""
    # Branch feature-a
    subprocess.run(
        ["git", "checkout", "-b", "feature-a"], cwd=git_repo, check=True, capture_output=True
    )
    f_a = git_repo / "feature_a.py"
    f_a.write_text("print('feat a')\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature_a.py"], cwd=git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feature a commit"], cwd=git_repo, check=True, capture_output=True
    )

    # Switch back to main and make a divergent commit
    subprocess.run(["git", "checkout", "main"], cwd=git_repo, check=True, capture_output=True)
    f_main = git_repo / "main_change.py"
    f_main.write_text("print('main divergent')\n", encoding="utf-8")
    subprocess.run(["git", "add", "main_change.py"], cwd=git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "main divergent commit"],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )

    collector = GitContextCollector(str(git_repo))
    # Review feature-a against main
    context = collector.collect(base_revision="main", head_revision="feature-a")
    assert context.is_working_tree is False
    # main_change.py was made on main after fork point,
    # so it should not appear as a changed file in feature-a
    assert "main_change.py" not in context.changed_files
