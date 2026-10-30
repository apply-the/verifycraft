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
    prompt_context: str | None = None


def merge_line_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sort and merge overlapping or adjacent (start, end) line intervals."""
    if not intervals:
        return []
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    merged: list[tuple[int, int]] = [sorted_intervals[0]]
    for start, end in sorted_intervals[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def render_windowed_source_context(
    lines: list[str], intervals: list[tuple[int, int]], total_lines: int
) -> str:
    """Render 1-indexed source lines with original line numbers and omission markers."""
    merged = merge_line_intervals(intervals)
    formatted_chunks: list[str] = []
    prev_end = 0
    for start, end in merged:
        if prev_end > 0 and start > prev_end + 1:
            omitted = start - prev_end - 1
            formatted_chunks.append(
                f"... [{omitted} surrounding lines omitted for brevity "
                f"(lines {prev_end + 1}-{start - 1}); see diff] ..."
            )
        for i in range(start, end + 1):
            formatted_chunks.append(f"{i}: {lines[i - 1]}")
        prev_end = end

    if prev_end < total_lines:
        omitted = total_lines - prev_end
        formatted_chunks.append(
            f"... [{omitted} trailing lines omitted for brevity "
            f"(lines {prev_end + 1}-{total_lines}); see diff] ..."
        )
    return "\n".join(formatted_chunks)


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

    @staticmethod
    def _parse_line_imports(line: str, parent_dir: str) -> set[str]:
        """Parse imported module names from a single Python source line."""
        modules: set[str] = set()
        stripped = line.strip()
        if not stripped:
            return modules

        rel_match = re.match(r"^from \.([\w\.]*) import", stripped)
        if rel_match:
            rel_mod = rel_match.group(1)
            if rel_mod:
                rel_path = rel_mod.replace(".", "/")
                if parent_dir and parent_dir != ".":
                    modules.add(f"{parent_dir}/{rel_path}")
                modules.add(rel_path)
            elif parent_dir and parent_dir != ".":
                modules.add(parent_dir)
            return modules

        from_match = re.match(r"^from ([\w\.]+) import", stripped)
        if from_match:
            full_mod = from_match.group(1)
            modules.update({full_mod, full_mod.split(".")[0], full_mod.replace(".", "/")})
            return modules

        import_match = re.match(r"^import ([\w\.]+)", stripped)
        if import_match:
            full_mod = import_match.group(1)
            modules.update({full_mod, full_mod.split(".")[0], full_mod.replace(".", "/")})
            return modules

        return modules

    def _extract_imported_modules(self, changed_files: dict[str, ChangedFile]) -> set[str]:
        """Scan changed Python files for imported module base names, submodules, and imports."""
        imported_modules: set[str] = set()
        for cfile in changed_files.values():
            if not cfile.content or not cfile.path.endswith(".py"):
                continue
            parent_dir = Path(cfile.path).parent.as_posix()
            for line in cfile.content.splitlines():
                imported_modules.update(self._parse_line_imports(line, parent_dir))
        return imported_modules

    def _build_candidate_paths(
        self, imported_modules: set[str], changed_paths: Iterable[str]
    ) -> list[str]:
        """Construct candidate file paths for helpers, modules, callers, interfaces, and tests."""
        candidate_paths: list[str] = []
        changed_paths_set = set(changed_paths)

        for mod in imported_modules:
            candidate_paths.extend(
                [
                    f"{mod}.py",
                    f"src/{mod}.py",
                    f"{mod}/__init__.py",
                    f"src/{mod}/__init__.py",
                ]
            )

        candidate_paths.extend(
            [
                "helpers.py",
                "src/helpers.py",
            ]
        )

        for path in changed_paths_set:
            path_obj = Path(path)
            stem = path_obj.stem
            parent_dir = path_obj.parent.as_posix()

            # Sibling helpers, interfaces, types, and models in same directory
            if parent_dir and parent_dir != ".":
                for sibling in (
                    "helpers.py",
                    "types.py",
                    "interfaces.py",
                    "protocols.py",
                    "models.py",
                ):
                    candidate_paths.append(f"{parent_dir}/{sibling}")

            # Associated tests
            candidate_paths.extend(
                [
                    f"tests/test_{stem}.py",
                    f"test_{stem}.py",
                    f"tests/{stem}_test.py",
                    f"tests/unit/test_{stem}.py",
                ]
            )
            if parent_dir and parent_dir != ".":
                candidate_paths.extend(
                    [
                        f"tests/{parent_dir}/test_{stem}.py",
                        f"tests/unit/{parent_dir}/test_{stem}.py",
                    ]
                )

        # Retain order but deduplicate and exclude already changed files
        seen: set[str] = set()
        deduped: list[str] = []
        for p in candidate_paths:
            if p not in seen and p not in changed_paths_set:
                seen.add(p)
                deduped.append(p)

        return deduped

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
        return self._bounded_diff_acquisition(diff_text, max_diff_lines, omitted_scope)

    def _bounded_diff_acquisition(
        self,
        diff_text: str,
        max_diff_lines: int,
        omitted_scope: list[str],
    ) -> str:
        """Bounded, structure-aware diff acquisition.

        Invariants:
        1. Never cut a diff in the middle of a hunk.
        2. Preserve changed file identities and boundaries.
        3. Fair representation across modified files.
        4. Explicit accounting of fully, partially, and unreviewed files.
        """
        raw_lines = diff_text.splitlines()
        total_lines = len(raw_lines)
        if total_lines <= max_diff_lines:
            return diff_text

        omitted_scope.append(
            f"Diff truncated: {total_lines} lines exceeded limit of {max_diff_lines} lines"
        )

        # Split diff into per-file chunks
        file_chunks = re.split(r"(^diff --git )", diff_text, flags=re.MULTILINE)
        if len(file_chunks) <= 1:
            # Fallback if diff has non-standard formatting
            return "\n".join(raw_lines[:max_diff_lines])

        files_data: list[tuple[str, str, list[str]]] = []
        for i in range(1, len(file_chunks), 2):
            full_chunk = file_chunks[i] + file_chunks[i + 1]
            c_lines = full_chunk.splitlines()
            header_info = self._parse_diff_header(c_lines)
            path = header_info[0] if header_info else f"unknown_file_{i}"

            # Separate header and hunks
            hunk_splits = re.split(r"(?=^@@ )", full_chunk, flags=re.MULTILINE)
            header_part = hunk_splits[0]
            hunk_parts = hunk_splits[1:] if len(hunk_splits) > 1 else []
            files_data.append((path, header_part, hunk_parts))

        num_files = len(files_data)
        base_quota_lines = max(30, max_diff_lines // max(1, num_files))

        retained_file_outputs: list[str] = []
        current_total_lines = 0
        fully_reviewed: list[str] = []
        partially_reviewed: list[tuple[str, int, int]] = []  # (path, kept_hunks, total_hunks)
        unreviewed: list[str] = []

        # First pass: Allocate up to base quota per file in complete hunks
        deferred_hunks: list[tuple[str, list[str]]] = []

        for path, header, hunks in files_data:
            if not hunks:
                # E.g. binary or empty diff
                retained_file_outputs.append(header)
                current_total_lines += len(header.splitlines())
                fully_reviewed.append(path)
                continue

            header_lines_count = len(header.splitlines())
            if current_total_lines + header_lines_count >= max_diff_lines:
                unreviewed.append(path)
                continue

            kept_hunks: list[str] = []
            file_lines_used = header_lines_count

            for idx, hunk in enumerate(hunks):
                h_lines = len(hunk.splitlines())
                if (
                    file_lines_used + h_lines <= base_quota_lines
                    and current_total_lines + file_lines_used + h_lines <= max_diff_lines
                ):
                    kept_hunks.append(hunk)
                    file_lines_used += h_lines
                else:
                    deferred_hunks.append((path, hunks[idx:]))
                    break

            if kept_hunks:
                file_chunk = header + "".join(kept_hunks)
                retained_file_outputs.append(file_chunk)
                current_total_lines += file_lines_used
                if len(kept_hunks) == len(hunks):
                    fully_reviewed.append(path)
                else:
                    partially_reviewed.append((path, len(kept_hunks), len(hunks)))
            else:
                unreviewed.append(path)

        # Second pass: If budget remains, append deferred hunks where feasible without splitting
        if current_total_lines < max_diff_lines and deferred_hunks:
            for path, rest_hunks in deferred_hunks:
                for hunk in rest_hunks:
                    h_lines = len(hunk.splitlines())
                    if current_total_lines + h_lines <= max_diff_lines:
                        # Append hunk to the corresponding file output
                        for idx, out in enumerate(retained_file_outputs):
                            if out.startswith(f"diff --git a/{path} ") or f" b/{path}\n" in out:
                                retained_file_outputs[idx] = out + hunk
                                current_total_lines += h_lines
                                break
                    else:
                        break

        # Record structured audit notes
        omitted_scope.append(
            f"Diff exceeded budget ({total_lines} lines > {max_diff_lines} limit). "
            f"Review scope is PARTIAL: {len(fully_reviewed)} file(s) fully examined, "
            f"{len(partially_reviewed)} partially examined, {len(unreviewed)} omitted."
        )
        if partially_reviewed:
            details = ", ".join(f"{p} ({k}/{t} hunks)" for p, k, t in partially_reviewed[:5])
            more = f" and {len(partially_reviewed) - 5} more" if len(partially_reviewed) > 5 else ""
            omitted_scope.append(f"Partially reviewed files: {details}{more}")
        if unreviewed:
            unrev_summary = ", ".join(unreviewed[:5])
            more_unrev = f" and {len(unreviewed) - 5} more" if len(unreviewed) > 5 else ""
            omitted_scope.append(
                f"Omitted files exceeding diff budget: {unrev_summary}{more_unrev}"
            )
        omitted_scope.append(
            "SCOPE LIMITATION: Incomplete diff scope cannot be interpreted as proof of clean code "
            "or absence of merge risks."
        )

        return "".join(retained_file_outputs)

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
        num_files = len(changed_files)
        per_file_bytes = max(2048, max_context_bytes // max(1, num_files))

        for path, cfile in changed_files.items():
            if cfile.status == "deleted":
                continue
            content = self.read_file_at_ref(path, head_revision)
            if content is None:
                cfile.content = None
                cfile.prompt_context = None
                continue

            # cfile.content always retains the complete, uncorrupted original source code
            cfile.content = content
            lines = content.splitlines()
            total_lines = len(lines)
            full_encoded = content.encode("utf-8")
            full_len = len(full_encoded)

            remaining_budget = max(0, max_context_bytes - total_bytes)

            if full_len <= per_file_bytes and full_len <= remaining_budget:
                cfile.prompt_context = "\n".join(
                    f"{i}: {line}" for i, line in enumerate(lines, start=1)
                )
                total_bytes += len(cfile.prompt_context.encode("utf-8"))
            elif remaining_budget > 0:
                # Bounded window around changed hunks preserving real source line coordinates
                intervals: list[tuple[int, int]] = [(1, min(30, total_lines))]
                for hunk in cfile.hunks:
                    h_start = max(1, hunk.new_start - 25)
                    h_end = min(total_lines, hunk.new_start + max(hunk.new_lines, 1) + 25)
                    intervals.append((h_start, h_end))

                window_repr = render_windowed_source_context(lines, intervals, total_lines)
                window_bytes = len(window_repr.encode("utf-8"))

                if window_bytes <= remaining_budget:
                    cfile.prompt_context = window_repr
                    total_bytes += window_bytes
                    omitted_scope.append(
                        f"Content for '{path}' bounded to changed hunk windows "
                        f"({window_bytes} bytes)."
                    )
                else:
                    # Try tighter window without header to fit remaining budget
                    tight_intervals: list[tuple[int, int]] = []
                    for hunk in cfile.hunks:
                        h_start = max(1, hunk.new_start - 5)
                        h_end = min(total_lines, hunk.new_start + max(hunk.new_lines, 1) + 5)
                        tight_intervals.append((h_start, h_end))

                    tight_repr = render_windowed_source_context(lines, tight_intervals, total_lines)
                    tight_bytes = len(tight_repr.encode("utf-8"))

                    if tight_bytes <= remaining_budget:
                        cfile.prompt_context = tight_repr
                        total_bytes += tight_bytes
                        omitted_scope.append(
                            f"Content for '{path}' bounded to tight hunk windows "
                            f"({tight_bytes} bytes)."
                        )
                    else:
                        cfile.prompt_context = None
                        omitted_scope.append(
                            f"Content for '{path}' omitted due to context size limits."
                        )
            else:
                cfile.prompt_context = None
                omitted_scope.append(f"Content for '{path}' omitted due to context size limits.")

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

    def validate_semantic_anchor(
        self,
        file_path: str,
        line: int | None,
        subject: str,
        description: str,
        changed_files: dict[str, ChangedFile],
        suggested_action: str | None = None,
    ) -> tuple[int | None, str | None]:
        """Validate or deterministically remap a candidate finding anchor.

        If line cannot be grounded or verified against supporting source code,
        downgrades to file-level comment (None). Never invents a location.
        """
        if file_path not in changed_files:
            return None, f"File '{file_path}' was not modified in this review"

        cfile = changed_files[file_path]

        combined_text = f"{subject}\n{description}\n{suggested_action or ''}"
        backticked = re.findall(r"`([a-zA-Z0-9_\.\-:]+)`", combined_text)
        quoted = re.findall(r"['\"]([a-zA-Z0-9_\.\-:]+)['\"]", combined_text)
        identifiers = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]{3,}\b", combined_text)

        candidate_symbols: list[str] = []
        for s in backticked + quoted:
            s_clean = s.strip()
            if len(s_clean) >= 3 and s_clean not in candidate_symbols:
                candidate_symbols.append(s_clean)
        for s in identifiers:
            if (
                len(s) >= 4
                and s not in candidate_symbols
                and s.lower()
                not in (
                    "this",
                    "that",
                    "with",
                    "from",
                    "should",
                    "could",
                    "would",
                    "function",
                    "method",
                    "class",
                    "issue",
                    "finding",
                )
            ):
                candidate_symbols.append(s)

        if not cfile.content:
            if candidate_symbols:
                return None, (
                    f"Source content unavailable for '{file_path}'; "
                    "finding downgraded to file-level comment"
                )
            # Pure line anchor validation without semantic symbols
            if line is not None:
                in_changed = line in cfile.changed_lines or any(
                    hunk.new_start <= line <= hunk.new_start + max(hunk.new_lines - 1, 0)
                    for hunk in cfile.hunks
                )
                if in_changed:
                    return line, None
                return None, (
                    f"Line {line} is outside changed hunks in '{file_path}'; "
                    "downgraded to file-level comment"
                )
            return None, None

        content_lines = cfile.content.splitlines()

        # 1. Check if the cited line itself directly contains any candidate symbol
        if line is not None and 1 <= line <= len(content_lines):
            exact_line_text = content_lines[line - 1]
            for sym in candidate_symbols:
                if sym in exact_line_text:
                    return line, None

        # 2. Check if the cited line is within 2 lines of candidate symbol in context
        if line is not None and 1 <= line <= len(content_lines):
            target_slice = content_lines[max(0, line - 2) : min(len(content_lines), line + 1)]
            slice_text = "\n".join(target_slice)
            for sym in candidate_symbols:
                if sym in slice_text:
                    return line, None

        # 3. Search for exact definition or unique reference match in file
        if content_lines and candidate_symbols:
            for sym in candidate_symbols:
                def_matches: list[int] = []
                hunk_matches: list[int] = []
                all_matches: list[int] = []

                for idx, src_line in enumerate(content_lines, start=1):
                    if sym in src_line:
                        all_matches.append(idx)
                        if idx in cfile.changed_lines:
                            hunk_matches.append(idx)
                        trimmed = src_line.strip()
                        is_def = (
                            trimmed.startswith(f"def {sym}")
                            or trimmed.startswith(f"class {sym}")
                            or f"{sym} =" in trimmed
                            or f"{sym}=" in trimmed
                        )
                        if is_def:
                            def_matches.append(idx)

                # Prioritize definition site in changed lines ONLY when unique
                hunk_defs = [
                    line_num for line_num in def_matches if line_num in cfile.changed_lines
                ]
                if len(hunk_defs) == 1:
                    remapped = hunk_defs[0]
                    reason = (
                        f"Anchor remapped from line {line} to line {remapped} "
                        f"based on exact definition of '{sym}'"
                        if line
                        else None
                    )
                    return remapped, reason
                if len(def_matches) == 1:
                    remapped = def_matches[0]
                    reason = (
                        f"Anchor remapped from line {line} to line {remapped} "
                        f"based on exact definition of '{sym}'"
                        if line
                        else None
                    )
                    return remapped, reason
                if len(hunk_matches) == 1:
                    remapped = hunk_matches[0]
                    reason = (
                        f"Anchor remapped from line {line} to line {remapped} "
                        f"based on exact reference to '{sym}'"
                        if line
                        else None
                    )
                    return remapped, reason
                if len(all_matches) == 1:
                    remapped = all_matches[0]
                    reason = (
                        f"Anchor remapped from line {line} to line {remapped} "
                        f"based on exact reference to '{sym}'"
                        if line
                        else None
                    )
                    return remapped, reason

        # 4. If line is provided:
        if line is not None:
            in_changed = line in cfile.changed_lines or any(
                hunk.new_start <= line <= hunk.new_start + max(hunk.new_lines - 1, 0)
                for hunk in cfile.hunks
            )
            if not in_changed:
                return None, (
                    f"Line {line} is outside changed hunks in '{file_path}'; "
                    "downgraded to file-level comment"
                )
            if not candidate_symbols:
                return line, None
            return None, (
                f"Unanchored observation: Line {line} could not be semantically grounded "
                f"to cited code in '{file_path}'; downgraded to file-level comment"
            )

        return None, None

    def validate_line_anchor(
        self,
        file_path: str,
        line: int | None,
        changed_files: dict[str, ChangedFile],
    ) -> tuple[int | None, str | None]:
        """Validate line anchor against changed files and hunks."""
        return self.validate_semantic_anchor(
            file_path=file_path,
            line=line,
            subject="",
            description="",
            changed_files=changed_files,
        )
