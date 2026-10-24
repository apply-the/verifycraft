"""Unit tests for GitContextCollector and diff parser."""

from pathlib import Path

import pytest

from verifycraft.review.git import ChangedFile, DiffHunk, GitContextCollector, GitError

SAMPLE_DIFF = """diff --git a/src/auth.py b/src/auth.py
index e69de29..d95f3ad 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -10,6 +10,8 @@ def authenticate_user(token):
+    # New authentication logic
+    session = create_session(token)
     return session
diff --git a/old_name.py b/new_name.py
similarity index 100%
rename from old_name.py
rename to new_name.py
"""


def test_parse_diff() -> None:
    collector = GitContextCollector(".")
    changed = collector.parse_diff(SAMPLE_DIFF)

    assert "src/auth.py" in changed
    auth_file = changed["src/auth.py"]
    assert auth_file.status == "modified"
    assert len(auth_file.hunks) == 1
    assert 10 in auth_file.changed_lines
    assert 11 in auth_file.changed_lines

    assert "new_name.py" in changed
    rename_file = changed["new_name.py"]
    assert rename_file.status == "renamed"
    assert rename_file.old_path == "old_name.py"


def test_validate_line_anchor() -> None:
    collector = GitContextCollector(".")
    changed = {
        "src/auth.py": ChangedFile(
            path="src/auth.py",
            changed_lines={10, 11},
            hunks=[
                DiffHunk(
                    old_start=10,
                    old_lines=3,
                    new_start=10,
                    new_lines=5,
                    content="",
                )
            ],
        )
    }

    # Exact changed line
    line, reason = collector.validate_line_anchor("src/auth.py", 10, changed)
    assert line == 10
    assert reason is None

    # Line within changed hunk
    line, reason = collector.validate_line_anchor("src/auth.py", 13, changed)
    assert line == 13
    assert reason is None

    # Line outside hunk -> downgraded to None
    line, reason = collector.validate_line_anchor("src/auth.py", 99, changed)
    assert line is None
    assert reason is not None
    assert "downgraded to file-level" in reason

    # File not in review
    line, reason = collector.validate_line_anchor("other.py", 10, changed)
    assert line is None
    assert reason is not None
    assert "was not modified in this review" in reason


def test_git_collector_invalid_repo(tmp_path: Path) -> None:
    # Point to empty non-git directory
    with pytest.raises(GitError):
        GitContextCollector(str(tmp_path))
