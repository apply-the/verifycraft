"""Git context collector and diff parser using safe subprocess invocations."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable
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
    is_working_tree: bool = False


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
        if ref.upper() in ("WORKTREE", "WORKING_TREE"):
            return "WORKTREE (uncommitted)"
        try:
            return self._run_git(["rev-parse", ref]).strip()
        except GitError as e:
            raise GitError(f"Could not resolve git reference '{ref}': {e}") from e

    def get_diff(self, base_ref: str, head_ref: str) -> str:
        """Fetch unified diff between base and head references."""
        if head_ref.upper() in ("WORKTREE", "WORKING_TREE"):
            return self._run_git(["diff", "-U3", "-M", base_ref])

        # Try three-dot diff first (merge-base), fallback to two-dot
        try:
            return self._run_git(["diff", "-U3", "-M", f"{base_ref}...{head_ref}"])
        except GitError:
            pass

        try:
            return self._run_git(["diff", "-U3", "-M", base_ref, head_ref])
        except GitError as e:
            raise GitError(f"Failed to diff '{base_ref}' against '{head_ref}': {e}") from e

    def _parse_diff_header(self, lines: list[str]) -> tuple[str, str | None, str] | None:
        """Parse the header lines of a single file diff chunk."""
        if not lines:
            return None

        first_line = lines[0]
        if not first_line.startswith("diff --git a/"):
            return None

        parts = first_line[len("diff --git a/") :].split(" b/", 1)
        if len(parts) != 2:
            return None

        old_path, new_path = parts[0], parts[1]
        status = "modified"

        for line in lines[1:10]:
            if line.startswith("new file mode"):
                status = "added"
            elif line.startswith("deleted file mode"):
                status = "deleted"
            elif line.startswith(("similarity index", "rename from")):
                status = "renamed"

        target_path = new_path if status != "deleted" else old_path
        renamed_old = old_path if status == "renamed" else None
        return target_path, renamed_old, status

    def _parse_hunks(self, lines: list[str]) -> tuple[list[DiffHunk], set[int]]:
        """Parse unified diff hunk lines into DiffHunk objects and changed line numbers."""
        hunks: list[DiffHunk] = []
        changed_lines: set[int] = set()

        current_new_line = 0
        in_hunk = False
        hunk_lines: list[str] = []
        hunk_old_start = hunk_old_len = hunk_new_start = hunk_new_len = 0

        for line in lines:
            hunk_match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if hunk_match:
                if in_hunk:
                    hunks.append(
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
                    changed_lines.add(current_new_line)
                    current_new_line += 1
                elif line.startswith(" "):
                    current_new_line += 1

        if in_hunk:
            hunks.append(
                DiffHunk(
                    old_start=hunk_old_start,
                    old_lines=hunk_old_len,
                    new_start=hunk_new_start,
                    new_lines=hunk_new_len,
                    content="\n".join(hunk_lines),
                )
            )

        return hunks, changed_lines

    def parse_diff(self, diff_text: str) -> dict[str, ChangedFile]:
        """Parse unified diff text into structured ChangedFile objects."""
        changed: dict[str, ChangedFile] = {}
        if not diff_text.strip():
            return changed

        file_diffs = re.split(r"(^diff --git )", diff_text, flags=re.MULTILINE)
        for i in range(1, len(file_diffs), 2):
            chunk = file_diffs[i] + file_diffs[i + 1]
            lines = chunk.splitlines()
            header_info = self._parse_diff_header(lines)
            if not header_info:
                continue

            target_path, old_path, status = header_info
            hunks, changed_lines = self._parse_hunks(lines)
            changed[target_path] = ChangedFile(
                path=target_path,
                old_path=old_path,
                status=status,
                hunks=hunks,
                changed_lines=changed_lines,
            )

        return changed

    def read_file_at_ref(self, path: str, ref: str) -> str | None:
        """Safely read file content at a given ref using git show."""
        if ref.upper() in ("WORKTREE", "WORKING_TREE"):
            local_path = os.path.join(self.repo_path, path)
            if os.path.isfile(local_path):
                try:
                    with open(local_path, encoding="utf-8", errors="replace") as f:
                        return f.read()
                except OSError:
                    return None
            return None

        try:
            return self._run_git(["show", f"{ref}:{path}"])
        except GitError:
            return None

    def _extract_imported_modules(self, changed_files: dict[str, ChangedFile]) -> set[str]:
        """Scan changed Python files for imported module base names."""
        imported_modules: set[str] = set()
        for cfile in changed_files.values():
            if not cfile.content or not cfile.path.endswith(".py"):
                continue
            for line in cfile.content.splitlines():
                line = line.strip()
                import_match = re.match(r"^from ([\w\.]+) import", line)
                if import_match:
                    imported_modules.add(import_match.group(1).split(".")[0])
                import_match2 = re.match(r"^import ([\w\.]+)", line)
                if import_match2:
                    imported_modules.add(import_match2.group(1).split(".")[0])
        return imported_modules

    def _build_candidate_paths(
        self, imported_modules: set[str], changed_paths: Iterable[str]
    ) -> list[str]:
        """Construct candidate file paths for helpers, modules, and tests."""
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

        for path in changed_paths:
            stem = Path(path).stem
            candidate_paths.extend(
                [
                    f"tests/test_{stem}.py",
                    f"test_{stem}.py",
                    f"tests/{stem}_test.py",
                ]
            )

        return candidate_paths

    def find_related_context(
        self,
        changed_files: dict[str, ChangedFile],
        head_ref: str,
        max_bytes: int = 64 * 1024,
    ) -> dict[str, str]:
        """Find compensating logic, helpers, and test files associated with changed files."""
        related: dict[str, str] = {}
        total_bytes = 0

        imported_modules = self._extract_imported_modules(changed_files)
        candidate_paths = self._build_candidate_paths(imported_modules, changed_files.keys())

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

    def _truncate_diff(self, diff_text: str, max_diff_lines: int, omitted_scope: list[str]) -> str:
        diff_lines = diff_text.splitlines()
        diff_lines_count = len(diff_lines)
        if diff_lines_count > max_diff_lines:
            omitted_scope.append(
                f"Diff truncated: {diff_lines_count} lines exceeded limit of {max_diff_lines} lines"
            )
            return "\n".join(diff_lines[:max_diff_lines])
        return diff_text

    def _truncate_files(
        self,
        changed_files: dict[str, ChangedFile],
        max_files: int,
        omitted_scope: list[str],
    ) -> dict[str, ChangedFile]:
        if len(changed_files) <= max_files:
            return changed_files
        all_keys = list(changed_files.keys())
        retained_keys = all_keys[:max_files]
        omitted_keys = all_keys[max_files:]
        omitted_str = ", ".join(omitted_keys)
        omitted_scope.append(
            f"Files truncated: {len(all_keys)} files exceeded limit of "
            f"{max_files} (omitted: {omitted_str})"
        )
        return {k: changed_files[k] for k in retained_keys}

    def _populate_file_contents(
        self,
        changed_files: dict[str, ChangedFile],
        head_revision: str,
        max_context_bytes: int,
        omitted_scope: list[str],
    ) -> int:
        total_bytes = 0
        for path, cfile in changed_files.items():
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
        return total_bytes

    def _check_worktree_untracked_files(self, omitted_scope: list[str]) -> None:
        try:
            untracked_out = self._run_git(["ls-files", "--others", "--exclude-standard"])
            untracked_files = [f.strip() for f in untracked_out.splitlines() if f.strip()]
            if untracked_files:
                untracked_summary = ", ".join(untracked_files[:5])
                more = f" and {len(untracked_files) - 5} more" if len(untracked_files) > 5 else ""
                omitted_scope.append(
                    f"WORKTREE mode limitation: {len(untracked_files)} untracked file(s) "
                    f"omitted from review ({untracked_summary}{more}). "
                    "Track with 'git add' to include."
                )
        except GitError:
            pass

    def _record_worktree_scope(self, head_revision: str, omitted_scope: list[str]) -> bool:
        is_working_tree = head_revision.upper() in ("WORKTREE", "WORKING_TREE")
        if is_working_tree:
            omitted_scope.append(
                "WORKTREE mode active: review inspects working tree state relative to base "
                "revision; unstaged and staged modifications are included, but untracked files "
                "require explicit tracking."
            )
            self._check_worktree_untracked_files(omitted_scope)
        return is_working_tree

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
        diff_lines_count = len(diff_text.splitlines())
        omitted_scope: list[str] = []
        truncated_diff = self._truncate_diff(diff_text, max_diff_lines, omitted_scope)

        changed_files = self.parse_diff(truncated_diff)
        changed_files = self._truncate_files(changed_files, max_files, omitted_scope)

        total_bytes = self._populate_file_contents(
            changed_files, head_revision, max_context_bytes, omitted_scope
        )

        related = self.find_related_context(
            changed_files=changed_files,
            head_ref=head_revision,
            max_bytes=max(0, max_context_bytes - total_bytes),
        )
        for r_content in related.values():
            total_bytes += len(r_content.encode("utf-8"))

        is_working_tree = self._record_worktree_scope(head_revision, omitted_scope)

        return GitReviewContext(
            repo_path=self.repo_path,
            base_revision=base_revision,
            head_revision=head_revision,
            base_commit=base_commit,
            head_commit=head_commit,
            changed_files=changed_files,
            raw_diff=truncated_diff,
            related_files=related,
            omitted_scope=omitted_scope,
            diff_lines_count=diff_lines_count,
            total_context_bytes=total_bytes,
            is_working_tree=is_working_tree,
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
