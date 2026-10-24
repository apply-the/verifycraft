"""Fingerprinting and provenance tracking for VerifyCraft.

Computes deterministic fingerprints linking verification inputs, code revisions,
diffs, check specifications, and real on-disk file contents within an explicit,
bounded verification scope.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from verifycraft.contracts.models import CheckSpec, CheckType, Evidence, Hypothesis


class FingerprintError(Exception):
    """Raised when reliable change fingerprinting cannot be performed."""


# Directories explicitly excluded from repository scanning to avoid unbounded searches,
# caches, and false invalidations from local environments or version control.
IGNORED_DIRECTORIES: tuple[str, ...] = (
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "ENV",
    ".tox",
    ".nox",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "site-packages",
    "htmlcov",
)

# Standard code and configuration extensions within verification scope
SCOPED_FILE_EXTENSIONS: tuple[str, ...] = (
    ".py",
    ".toml",
    ".cfg",
    ".ini",
)

MAX_VERIFICATION_SCOPE_FILES = 500


def _parse_diff_file_paths(diff: str | None) -> set[str]:
    """Extract relative file paths referenced in unified diff headers."""
    if not diff:
        return set()
    paths: set[str] = set()
    for raw_line in diff.splitlines():
        line = raw_line.strip()
        if line.startswith(("--- a/", "+++ b/")):
            raw_path = line[6:].strip()
            if raw_path and raw_path != "/dev/null":
                paths.add(raw_path)
        elif line.startswith("diff --git a/") and " b/" in line:
            parts = line.split(" b/", 1)
            raw_path = parts[0][13:].strip()
            if raw_path:
                paths.add(raw_path)
    return paths


def _extract_affected_location_paths(affected_locations: Sequence[str]) -> set[str]:
    """Extract relative file paths from hypothesis affected locations (e.g. 'src/auth.py:42')."""
    paths: set[str] = set()
    for loc in affected_locations:
        clean = loc.split(":")[0].strip()
        if clean:
            paths.add(clean)
    return paths


def _scan_root_items(repo_path: Path, scope_paths: set[str], max_scope_files: int) -> None:
    """Scan root-level Python modules and configs into scope_paths."""
    for root_item in repo_path.glob("*"):
        if not root_item.is_file() or root_item.name.startswith("."):
            continue
        if root_item.suffix in SCOPED_FILE_EXTENSIONS:
            scope_paths.add(root_item.name)
            if len(scope_paths) > max_scope_files:
                raise FingerprintError(
                    f"Verification scope exceeded maximum limit of {max_scope_files} "
                    f"relevant files (discovered {len(scope_paths)}). "
                    "Reliable verification scope cannot be determined; failing closed."
                )


def _collect_candidate_scan_roots(repo_path: Path, clean_target: str) -> list[Path]:
    """Collect candidate directories to scan (src, tests, or check target parent)."""
    candidate_dirs = [repo_path / "src", repo_path / "tests"]
    if clean_target:
        target_parent = (repo_path / clean_target).parent
        if target_parent.is_dir() and target_parent not in candidate_dirs:
            candidate_dirs.append(target_parent)

    existing = [d for d in candidate_dirs if d.is_dir()]
    return existing if existing else [repo_path]


def _is_ignored_path(item: Path, repo_path: Path) -> bool:
    """Determine whether a path should be excluded from verification scope."""
    if any(ignored in item.parts for ignored in IGNORED_DIRECTORIES):
        return True
    try:
        rel_parts = item.relative_to(repo_path).parts
        return any(part.startswith(".") for part in rel_parts)
    except ValueError:
        return True


def _scan_candidate_root(
    repo_path: Path,
    scan_root: Path,
    scope_paths: set[str],
    max_scope_files: int,
) -> None:
    """Scan a single candidate root directory recursively for scoped files."""
    for item in scan_root.rglob("*"):
        if _is_ignored_path(item, repo_path):
            continue

        if item.is_file() and item.suffix in SCOPED_FILE_EXTENSIONS:
            try:
                rel = item.relative_to(repo_path).as_posix()
                scope_paths.add(rel)
                if len(scope_paths) > max_scope_files:
                    raise FingerprintError(
                        f"Verification scope exceeded maximum limit of {max_scope_files} "
                        f"relevant files (discovered {len(scope_paths)}). "
                        "Reliable verification scope cannot be determined; failing closed."
                    )
            except ValueError:
                continue


def _scan_candidate_directories(
    repo_path: Path,
    scan_roots: list[Path],
    scope_paths: set[str],
    max_scope_files: int,
) -> None:
    """Scan candidate directories for scoped files."""
    for scan_root in scan_roots:
        _scan_candidate_root(repo_path, scan_root, scope_paths, max_scope_files)


def resolve_verification_scope(
    repo_path: Path,
    check_spec: CheckSpec,
    target_files: Sequence[str] = (),
    diff: str | None = None,
    affected_locations: Sequence[str] = (),
    max_scope_files: int = MAX_VERIFICATION_SCOPE_FILES,
) -> list[str]:
    """Define the explicit, bounded verification scope for fingerprinting.

    Verification scope includes:
    1. The check target path (e.g. tests/test_auth.py).
    2. Any explicitly declared target_files.
    3. File locations referenced in hypothesis affected_locations.
    4. File locations modified in unified diff.
    5. Discovered repository source and test files within standard roots (src/, tests/, root .py).

    Excludes version control (.git), virtualenvs, build artifacts, and caches to avoid
    misleading or unbounded guarantees.
    """
    scope_paths: set[str] = set()

    # 1. Target check path
    clean_target = check_spec.target_path.split("::")[0].strip()
    if clean_target:
        scope_paths.add(Path(clean_target).as_posix())

    # 2. Declared target files
    for tf in target_files:
        clean_tf = tf.strip()
        if clean_tf:
            scope_paths.add(Path(clean_tf).as_posix())

    # 3. Affected locations
    for loc_path in _extract_affected_location_paths(affected_locations):
        scope_paths.add(Path(loc_path).as_posix())

    # 4. Diff files
    for diff_path in _parse_diff_file_paths(diff):
        scope_paths.add(Path(diff_path).as_posix())

    # 5. Discover relevant repo source and test files (handling omitted target_files)
    if repo_path.is_dir():
        _scan_root_items(repo_path, scope_paths, max_scope_files)
        scan_roots = _collect_candidate_scan_roots(repo_path, clean_target)
        _scan_candidate_directories(repo_path, scan_roots, scope_paths, max_scope_files)

    return sorted(scope_paths)


def _hash_file_content(path: Path) -> str:
    """Compute sha256 hash of a file's content in 64KB chunks."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def _process_scoped_file(
    repo: Path,
    repo_path: str,
    rel_path: str,
    clean_target: str,
    target_files_normalized: set[str],
    check_type: CheckType,
) -> str:
    """Validate and hash a single scoped file, returning hash or '<missing>'."""
    target_path_obj = (repo / rel_path).resolve()

    # Prevent directory traversal escaping repo
    try:
        target_path_obj.relative_to(repo.resolve())
    except ValueError:
        raise FingerprintError(
            f"Scoped file '{rel_path}' escapes repository boundary '{repo_path}'."
        ) from None

    if not target_path_obj.is_file():
        # For non-fake checks, target paths and explicit target_files MUST exist on disk
        if check_type != CheckType.FAKE:
            if rel_path == clean_target:
                raise FingerprintError(
                    f"Check target file does not exist on disk: '{rel_path}' in '{repo_path}'."
                )
            if rel_path in target_files_normalized:
                raise FingerprintError(
                    f"Target file does not exist on disk: '{rel_path}' in '{repo_path}'."
                )
        return "<missing>"

    try:
        return _hash_file_content(target_path_obj)
    except OSError as err:
        raise FingerprintError(f"Cannot read file '{rel_path}' for fingerprinting: {err}") from err


def compute_fingerprint(
    repo_path: str,
    revision: str,
    diff: str | None,
    target_files: Sequence[str],
    check_spec: CheckSpec,
    hypothesis: Hypothesis | None = None,
) -> str:
    """Compute a deterministic SHA-256 fingerprint for verification state within the defined scope.

    Fails closed by raising FingerprintError if:
    - repo_path does not exist or is not a directory.
    - Check target file does not exist on disk (for non-fake checks).
    - Declared target file does not exist on disk (for non-fake checks).
    - Any scoped target file cannot be read or escapes the repository.
    """
    repo = Path(repo_path)
    if not repo.exists() or not repo.is_dir():
        raise FingerprintError(
            f"Repository path '{repo_path}' does not exist or is not a directory."
        )

    affected_locs = hypothesis.affected_locations if hypothesis else ()
    scope = resolve_verification_scope(
        repo_path=repo,
        check_spec=check_spec,
        target_files=target_files,
        diff=diff,
        affected_locations=affected_locs,
    )

    clean_target = Path(check_spec.target_path.split("::")[0].strip()).as_posix()
    target_files_normalized = {Path(tf.strip()).as_posix() for tf in target_files if tf.strip()}
    file_hashes: dict[str, str] = {
        rel_path: _process_scoped_file(
            repo=repo,
            repo_path=repo_path,
            rel_path=rel_path,
            clean_target=clean_target,
            target_files_normalized=target_files_normalized,
            check_type=check_spec.check_type,
        )
        for rel_path in scope
    }

    payload = {
        "revision": revision.strip(),
        "diff": (diff.strip() if diff is not None else None),
        "target_files": sorted(target_files_normalized),
        "verification_scope": scope,
        "file_hashes": file_hashes,
        "check": {
            "check_id": check_spec.check_id,
            "check_type": check_spec.check_type.value,
            "target_path": check_spec.target_path.strip(),
            "args": list(check_spec.args),
            "env_overrides": dict(sorted(check_spec.env_overrides.items())),
        },
    }

    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    sha = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"sha256:{sha}"


def is_evidence_stale(evidence: Evidence, current_fingerprint: str) -> bool:
    """Return True if evidence fingerprint does not match the current fingerprint."""
    return evidence.fingerprint != current_fingerprint
