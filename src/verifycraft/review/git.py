"""Git context collector and diff parser using safe subprocess invocations."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class GitError(Exception):
    """Raised when a Git command fails or repo is invalid."""


@dataclass(frozen=True)
class DiffHunk:
    """A hunk in a unified diff."""

    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    content: str


@dataclass
class ChangedFile:
    """Metadata and changes for a single file in a git diff."""

    path: str
    old_path: str | None = None
    status: str = "modified"  # modified, added, deleted, renamed
    changed_lines: set[int] = field(default_factory=set)
    hunks: list[DiffHunk] = field(default_factory=list)
    content: str | None = None


@dataclass
class GitReviewContext:
    """Full collected context for code review."""

    repo_path: str
    base_revision: str
    head_revision: str
    base_commit: str
    head_commit: str
    changed_files: dict[str, ChangedFile]
    raw_diff: str
    related_files: dict[str, str]  # path -> content of related helpers/tests
    omitted_scope: list[str] = field(default_factory=list)
    diff_lines_count: int = 0
    total_context_bytes: int = 0


class GitContextCollector:
    """Collects git diffs, file changes, and related context without shell execution."""

    def __init__(self, repo_path: str):
        self.repo_path = str(Path(repo_path).resolve())
        self._validate_git_repo()

    def _run_git(self, args: list[str], timeout: float = 30.0) -> str:
        """Run a git command safely with shell=False."""
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                shell=False,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise GitError(f"Git command timed out after {timeout}s: git {' '.join(args)}") from e
        except Exception as e:
            raise GitError(f"Failed to execute git command: {e}") from e

        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            raise GitError(f"Git command failed (exit code {result.returncode}): {err}")

        return result.stdout

    def _validate_git_repo(self) -> None:
        """Ensure the target path is a git repository."""
        if not os.path.exists(self.repo_path):
            raise GitError(f"Repository path does not exist: {self.repo_path}")
        try:
            out = self._run_git(["rev-parse", "--is-inside-work-tree"])
            if out.strip() != "true":
                raise GitError(f"Path is not inside a git work tree: {self.repo_path}")
        except GitError as e:
            raise GitError(f"Invalid git repository at {self.repo_path}: {e}") from e

    def resolve_commit(self, ref: str) -> str:
        """Resolve a git reference or branch name to its full commit SHA."""
        try:
            return self._run_git(["rev-parse", ref]).strip()
        except GitError as e:
            raise GitError(f"Could not resolve git reference '{ref}': {e}") from e

    def get_diff(self, base_ref: str, head_ref: str) -> str:
        """Fetch unified diff between base and head references."""
        # Try three-dot diff first (merge-base), fallback to two-dot
        try:
            diff = self._run_git(["diff", "-U3", "-M", f"{base_ref}...{head_ref}"])
            if diff:
                return diff
        except GitError:
            pass

        try:
            return self._run_git(["diff", "-U3", "-M", base_ref, head_ref])
        except GitError as e:
            raise GitError(f"Failed to diff '{base_ref}' against '{head_ref}': {e}") from e

    def parse_diff(self, diff_text: str) -> dict[str, ChangedFile]:
        """Parse unified diff text into structured ChangedFile objects."""
        changed: dict[str, ChangedFile] = {}
        if not diff_text.strip():
            return changed

        file_diffs = re.split(r"(^diff --git )", diff_text, flags=re.MULTILINE)
        chunks: list[str] = []
        for i in range(1, len(file_diffs), 2):
            chunks.append(file_diffs[i] + file_diffs[i + 1])

        for chunk in chunks:
            lines = chunk.splitlines()
            if not lines:
                continue

            header_match = re.match(r"^diff --git a/(.*?) b/(.*)$", lines[0])
            if not header_match:
                continue

            old_path = header_match.group(1)
            new_path = header_match.group(2)
            status = "modified"

            for line in lines[1:10]:
                if line.startswith("new file mode"):
                    status = "added"
                elif line.startswith("deleted file mode"):
                    status = "deleted"
                elif line.startswith("similarity index") or line.startswith("rename from"):
                    status = "renamed"

            target_path = new_path if status != "deleted" else old_path
            changed_file = ChangedFile(
                path=target_path,
                old_path=old_path if status == "renamed" else None,
                status=status,
            )

            current_new_line = 0
            in_hunk = False
            hunk_lines: list[str] = []
            hunk_old_start = 0
            hunk_old_len = 0
            hunk_new_start = 0
            hunk_new_len = 0

            for line in lines:
                hunk_match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
                if hunk_match:
                    if in_hunk:
                        changed_file.hunks.append(
                            DiffHunk(
                                old_start=hunk_old_start,
                                old_lines=hunk_old_len,
                                new_start=hunk_new_start,
                                new_lines=hunk_new_len,
                                content="\n".join(hunk_lines),
                            )
                        )
                        hunk_lines = []

                    in_hunk = True
                    hunk_old_start = int(hunk_match.group(1))
                    hunk_old_len = int(hunk_match.group(2) or 1)
                    hunk_new_start = int(hunk_match.group(3))
                    hunk_new_len = int(hunk_match.group(4) or 1)
                    current_new_line = hunk_new_start
                    hunk_lines.append(line)
                    continue

                if in_hunk:
                    hunk_lines.append(line)
                    if line.startswith("+"):
                        changed_file.changed_lines.add(current_new_line)
                        current_new_line += 1
                    elif line.startswith(" "):
                        current_new_line += 1
                    elif line.startswith("-"):
                        pass

            if in_hunk:
                changed_file.hunks.append(
                    DiffHunk(
                        old_start=hunk_old_start,
                        old_lines=hunk_old_len,
                        new_start=hunk_new_start,
                        new_lines=hunk_new_len,
                        content="\n".join(hunk_lines),
                    )
                )

            changed[target_path] = changed_file

        return changed

    def read_file_at_ref(self, path: str, ref: str) -> str | None:
        """Safely read file content at a given ref using git show."""
        try:
            return self._run_git(["show", f"{ref}:{path}"])
        except GitError:
            # Fallback to local filesystem if file exists on disk
            local_path = os.path.join(self.repo_path, path)
            if os.path.isfile(local_path):
                try:
                    with open(local_path, encoding="utf-8", errors="replace") as f:
                        return f.read()
                except OSError:
                    return None
            return None

    def find_related_context(
        self,
        changed_files: dict[str, ChangedFile],
        head_ref: str,
        max_bytes: int = 64 * 1024,
    ) -> dict[str, str]:
        """Find compensating logic, helpers, and test files associated with changed files."""
        related: dict[str, str] = {}
        total_bytes = 0

        # Scan changed files for imports
        imported_modules: set[str] = set()
        for cfile in changed_files.values():
            if not cfile.content or not cfile.path.endswith(".py"):
                continue
            for line in cfile.content.splitlines():
                line = line.strip()
                import_match = re.match(r"^from ([\w\.]+) import", line)
                if import_match:
                    mod = import_match.group(1).split(".")[0]
                    imported_modules.add(mod)
                import_match2 = re.match(r"^import ([\w\.]+)", line)
                if import_match2:
                    mod = import_match2.group(1).split(".")[0]
                    imported_modules.add(mod)

        # Look for imported module files in repo
        candidate_paths: list[str] = []
        for mod in imported_modules:
            candidate_paths.extend(
                [
                    f"{mod}.py",
                    f"src/{mod}.py",
                    f"{mod}/__init__.py",
                    f"src/{mod}/__init__.py",
                    "helpers.py",
                    "src/helpers.py",
                ]
            )

        # Also search for tests corresponding to changed files
        for path in changed_files:
            p = Path(path)
            stem = p.stem
            candidate_paths.extend(
                [
                    f"tests/test_{stem}.py",
                    f"test_{stem}.py",
                    f"tests/{stem}_test.py",
                ]
            )

        for cand in candidate_paths:
            if cand in changed_files or cand in related:
                continue
            content = self.read_file_at_ref(cand, head_ref)
            if content is not None:
                content_len = len(content.encode("utf-8"))
                if total_bytes + content_len <= max_bytes:
                    related[cand] = content
                    total_bytes += content_len

        return related

    def collect(
        self,
        base_revision: str = "main",
        head_revision: str = "HEAD",
        explicit_diff: str | None = None,
        max_files: int = 50,
        max_diff_lines: int = 2500,
        max_context_bytes: int = 128 * 1024,
    ) -> GitReviewContext:
        """Collect all git diffs, changed files, and relevant context within budgets."""
        base_commit = self.resolve_commit(base_revision)
        head_commit = self.resolve_commit(head_revision)

        diff_text = (
            explicit_diff
            if explicit_diff is not None
            else self.get_diff(base_revision, head_revision)
        )
        diff_lines = diff_text.splitlines()
        diff_lines_count = len(diff_lines)

        omitted_scope: list[str] = []
        if diff_lines_count > max_diff_lines:
            diff_text = "\n".join(diff_lines[:max_diff_lines])
            omitted_scope.append(
                f"Diff truncated: {diff_lines_count} lines exceeded limit of {max_diff_lines} lines"
            )

        changed_files = self.parse_diff(diff_text)

        if len(changed_files) > max_files:
            all_keys = list(changed_files.keys())
            retained_keys = all_keys[:max_files]
            omitted_keys = all_keys[max_files:]
            changed_files = {k: changed_files[k] for k in retained_keys}
            omitted_str = ", ".join(omitted_keys)
            omitted_scope.append(
                f"Files truncated: {len(all_keys)} files exceeded limit of "
                f"{max_files} (omitted: {omitted_str})"
            )

        total_bytes = 0
        for path, cfile in list(changed_files.items()):
            if cfile.status == "deleted":
                continue
            content = self.read_file_at_ref(path, head_revision)
            if content is not None:
                encoded_len = len(content.encode("utf-8"))
                if total_bytes + encoded_len <= max_context_bytes:
                    cfile.content = content
                    total_bytes += encoded_len
                else:
                    omitted_scope.append(
                        f"Content for '{path}' omitted due to context size limits."
                    )

        related = self.find_related_context(
            changed_files=changed_files,
            head_ref=head_revision,
            max_bytes=max(0, max_context_bytes - total_bytes),
        )
        for r_content in related.values():
            total_bytes += len(r_content.encode("utf-8"))

        return GitReviewContext(
            repo_path=self.repo_path,
            base_revision=base_revision,
            head_revision=head_revision,
            base_commit=base_commit,
            head_commit=head_commit,
            changed_files=changed_files,
            raw_diff=diff_text,
            related_files=related,
            omitted_scope=omitted_scope,
            diff_lines_count=diff_lines_count,
            total_context_bytes=total_bytes,
        )

    def validate_line_anchor(
        self,
        file_path: str,
        line: int | None,
        changed_files: dict[str, ChangedFile],
    ) -> tuple[int | None, str | None]:
        """Validate line anchor against changed files and hunks.

        Returns (validated_line, fallback_reason).
        If the line is invalid or not in changed hunks, validated_line will be None
        or clamped to a valid anchor, never an invented location.
        """
        if file_path not in changed_files:
            return None, f"File '{file_path}' was not modified in this review"

        cfile = changed_files[file_path]
        if line is None:
            return None, None

        if line in cfile.changed_lines:
            return line, None

        # Check if line is within any changed hunk
        for hunk in cfile.hunks:
            if hunk.new_start <= line <= hunk.new_start + max(hunk.new_lines - 1, 0):
                return line, None

        # Not in any changed hunk
        reason = (
            f"Line {line} is outside changed hunks in '{file_path}'; "
            "downgraded to file-level comment"
        )
        return None, reason
