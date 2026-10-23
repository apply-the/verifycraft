from pathlib import Path

from verifycraft.contracts import CheckSpec, CheckType, Evidence, ResourceUsage
from verifycraft.core.fingerprint import compute_fingerprint, is_evidence_stale


def test_fingerprint_determinism(tmp_path: Path) -> None:
    # Setup repo1 and repo2 with identical content at different absolute paths
    repo1 = tmp_path / "repo1"
    repo2 = tmp_path / "repo2"
    for r in (repo1, repo2):
        (r / "tests").mkdir(parents=True, exist_ok=True)
        (r / "tests" / "test_x.py").write_text("def test_foo(): pass\n", encoding="utf-8")
        (r / "file1.py").write_text("a = 1\n", encoding="utf-8")
        (r / "file2.py").write_text("b = 2\n", encoding="utf-8")

    spec = CheckSpec(
        check_id="chk-1",
        check_type=CheckType.PYTEST,
        target_path="tests/test_x.py",
        args=["-k", "foo"],
    )
    fp1 = compute_fingerprint(
        repo_path=str(repo1),
        revision="rev123",
        diff="diff --git a b",
        target_files=["file1.py", "file2.py"],
        check_spec=spec,
    )
    # Fingerprint is invariant to absolute path, capturing logical revision/diff/files
    fp2 = compute_fingerprint(
        repo_path=str(repo2),
        revision="rev123",
        diff="diff --git a b",
        target_files=["file2.py", "file1.py"],  # Order is sorted
        check_spec=spec,
    )
    assert fp1.startswith("sha256:")
    assert fp1 == fp2


def test_fingerprint_sensitivity_to_changes(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")

    spec1 = CheckSpec(
        check_id="chk-1",
        check_type=CheckType.PYTEST,
        target_path="tests/test_x.py",
        args=["-k", "foo"],
    )
    fp1 = compute_fingerprint(str(tmp_path), "rev1", "diff1", ["a.py"], spec1)

    # Different revision
    fp2 = compute_fingerprint(str(tmp_path), "rev2", "diff1", ["a.py"], spec1)
    assert fp1 != fp2

    # Different diff
    fp3 = compute_fingerprint(str(tmp_path), "rev1", "diff2", ["a.py"], spec1)
    assert fp1 != fp3

    # Different args
    spec2 = CheckSpec(
        check_id="chk-1",
        check_type=CheckType.PYTEST,
        target_path="tests/test_x.py",
        args=["-k", "bar"],
    )
    fp4 = compute_fingerprint(str(tmp_path), "rev1", "diff1", ["a.py"], spec2)
    assert fp1 != fp4


def test_staleness_detection(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")

    spec = CheckSpec(
        check_id="chk-1",
        check_type=CheckType.PYTEST,
        target_path="tests/test_x.py",
    )
    current_fp = compute_fingerprint(str(tmp_path), "rev-current", "diff-current", [], spec)
    old_fp = compute_fingerprint(str(tmp_path), "rev-old", "diff-old", [], spec)

    evidence_old = Evidence(
        evidence_id="evi-old",
        check_id="chk-1",
        evidence_type="pytest_execution",
        exit_code=0,
        stdout="",
        stderr="",
        fingerprint=old_fp,
        resource_usage=ResourceUsage(),
    )
    assert is_evidence_stale(evidence_old, current_fp) is True

    evidence_fresh = Evidence(
        evidence_id="evi-fresh",
        check_id="chk-1",
        evidence_type="pytest_execution",
        exit_code=0,
        stdout="",
        stderr="",
        fingerprint=current_fp,
        resource_usage=ResourceUsage(),
    )
    assert is_evidence_stale(evidence_fresh, current_fp) is False


def test_fingerprint_changes_when_on_disk_file_modified(tmp_path: Path) -> None:
    test_file = tmp_path / "test_mod.py"
    test_file.write_text("x = 1\n", encoding="utf-8")

    spec = CheckSpec(
        check_id="chk-1",
        check_type=CheckType.PYTEST,
        target_path="test_mod.py",
    )
    fp1 = compute_fingerprint(
        repo_path=str(tmp_path),
        revision="rev1",
        diff="diff1",
        target_files=["test_mod.py"],
        check_spec=spec,
    )

    # Modify file on disk WITHOUT changing revision or diff metadata
    test_file.write_text("x = 2\n", encoding="utf-8")

    fp2 = compute_fingerprint(
        repo_path=str(tmp_path),
        revision="rev1",
        diff="diff1",
        target_files=["test_mod.py"],
        check_spec=spec,
    )
    assert fp1 != fp2

    # Evidence collected at fp1 must now be detected as stale
    old_evidence = Evidence(
        evidence_id="evi-1",
        check_id="chk-1",
        evidence_type="pytest_execution",
        exit_code=0,
        fingerprint=fp1,
    )
    assert is_evidence_stale(old_evidence, fp2) is True


def test_parse_diff_file_paths_handles_dev_null_and_git_headers() -> None:
    from verifycraft.core.fingerprint import _parse_diff_file_paths

    # Test /dev/null exclusion for created or deleted files
    diff_dev_null = (
        "--- /dev/null\n"
        "+++ b/src/created.py\n"
        "@@ -0,0 +1 @@\n"
        "+print('hello')\n"
        "--- a/src/deleted.py\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-print('bye')\n"
    )
    parsed = _parse_diff_file_paths(diff_dev_null)
    assert parsed == {"src/created.py", "src/deleted.py"}

    # Test git diff header fallback
    diff_git_header = (
        "diff --git a/only_header.py b/only_header.py\nindex 1234567..89abcdef 100644\n"
    )
    parsed_git = _parse_diff_file_paths(diff_git_header)
    assert parsed_git == {"only_header.py"}

    # None and empty diffs
    assert _parse_diff_file_paths(None) == set()
    assert _parse_diff_file_paths("") == set()


def test_extract_affected_locations_empty_and_whitespace() -> None:
    from verifycraft.core.fingerprint import _extract_affected_location_paths

    locs = [":42", "   ", "src/module.py:100", "tests/test_mod.py:5"]
    extracted = _extract_affected_location_paths(locs)
    assert extracted == {"src/module.py", "tests/test_mod.py"}


def test_resolve_verification_scope_root_limit_exceeded(tmp_path: Path) -> None:
    from verifycraft.core.fingerprint import FingerprintError, resolve_verification_scope

    # Create root files exceeding max_scope_files
    for i in range(10):
        (tmp_path / f"root_{i}.py").write_text("x = 1\n", encoding="utf-8")

    spec = CheckSpec(
        check_id="chk-1",
        check_type=CheckType.PYTEST,
        target_path="root_0.py",
    )
    import pytest

    with pytest.raises(FingerprintError, match="exceeded maximum limit of 5"):
        resolve_verification_scope(
            repo_path=tmp_path,
            check_spec=spec,
            max_scope_files=5,
        )


def test_resolve_verification_scope_with_non_directory_and_custom_parent(tmp_path: Path) -> None:
    from verifycraft.core.fingerprint import resolve_verification_scope

    # 1. Non-directory repo_path returns explicit targets without scanning
    non_dir = tmp_path / "not_a_dir.txt"
    non_dir.write_text("test")
    spec = CheckSpec(
        check_id="chk-1",
        check_type=CheckType.PYTEST,
        target_path="tests/test_x.py",
    )
    scope = resolve_verification_scope(
        repo_path=non_dir,
        check_spec=spec,
        target_files=["extra.py", "  "],
    )
    assert scope == ["extra.py", "tests/test_x.py"]

    # 2. Target path parent outside src and tests (e.g. lib/pkg/tool.py)
    lib_dir = tmp_path / "lib" / "pkg"
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "tool.py").write_text("def run(): pass\n", encoding="utf-8")
    (lib_dir / "helper.py").write_text("def h(): pass\n", encoding="utf-8")
    # Hidden folder in lib should be ignored
    hidden_dir = lib_dir / ".hidden"
    hidden_dir.mkdir(exist_ok=True)
    (hidden_dir / "secret.py").write_text("hidden = True\n", encoding="utf-8")

    spec_lib = CheckSpec(
        check_id="chk-2",
        check_type=CheckType.PYTEST,
        target_path="lib/pkg/tool.py",
    )
    scope_lib = resolve_verification_scope(
        repo_path=tmp_path,
        check_spec=spec_lib,
    )
    assert "lib/pkg/tool.py" in scope_lib
    assert "lib/pkg/helper.py" in scope_lib
    assert not any(".hidden" in p for p in scope_lib)


def test_compute_fingerprint_validations_and_failures(tmp_path: Path) -> None:
    import pytest

    from verifycraft.core.fingerprint import FingerprintError

    # 1. Non-existent repo_path
    spec = CheckSpec(
        check_id="chk-1",
        check_type=CheckType.PYTEST,
        target_path="tests/test_a.py",
    )
    with pytest.raises(FingerprintError, match="does not exist or is not a directory"):
        compute_fingerprint("/non/existent/path/98765", "rev1", None, [], spec)

    # 2. Scoped file escaping repository boundary
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_a(): pass\n")
    with pytest.raises(FingerprintError, match="escapes repository boundary"):
        compute_fingerprint(
            repo_path=str(tmp_path),
            revision="rev1",
            diff=None,
            target_files=["../../outside.py"],
            check_spec=spec,
        )

    # 3. Target file does not exist on disk for non-fake check
    (tmp_path / "valid.py").write_text("x = 1\n")
    spec_missing_target = CheckSpec(
        check_id="chk-miss",
        check_type=CheckType.PYTEST,
        target_path="missing_test.py",
    )
    with pytest.raises(FingerprintError, match="Check target file does not exist on disk"):
        compute_fingerprint(
            repo_path=str(tmp_path),
            revision="rev1",
            diff=None,
            target_files=["valid.py"],
            check_spec=spec_missing_target,
        )

    # 4. Declared target_file does not exist on disk for non-fake check
    with pytest.raises(FingerprintError, match="Target file does not exist on disk"):
        compute_fingerprint(
            repo_path=str(tmp_path),
            revision="rev1",
            diff=None,
            target_files=["missing_declared.py"],
            check_spec=spec,
        )

    # 5. CheckType.FAKE allows missing target file on disk
    fake_spec = CheckSpec(
        check_id="chk-fake",
        check_type=CheckType.FAKE,
        target_path="non_existent_fake.py",
    )
    fake_fp = compute_fingerprint(
        repo_path=str(tmp_path),
        revision="rev1",
        diff=None,
        target_files=["missing_fake.py"],
        check_spec=fake_spec,
    )
    assert fake_fp.startswith("sha256:")

    # 6. OSError during file hashing
    from unittest.mock import mock_open, patch

    with patch("builtins.open", mock_open()) as m_open:
        m_open.side_effect = OSError("Read fault")
        with pytest.raises(FingerprintError, match="Cannot read file"):
            compute_fingerprint(
                repo_path=str(tmp_path),
                revision="rev1",
                diff=None,
                target_files=["valid.py"],
                check_spec=spec,
            )


def test_fingerprint_additional_branch_coverage(tmp_path: Path) -> None:
    from unittest.mock import patch

    import pytest

    from verifycraft.core.fingerprint import (
        FingerprintError,
        _parse_diff_file_paths,
        compute_fingerprint,
        resolve_verification_scope,
    )

    # 1. Empty raw_path in diff headers
    diff_empty = "--- a/\n+++ b/\ndiff --git a/ b/\n"
    assert _parse_diff_file_paths(diff_empty) == set()

    # 2. Ignored directories and hidden folders during scan
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "real.py").write_text("ok = True\n")
    (tmp_path / "src" / "__pycache__").mkdir()
    (tmp_path / "src" / "__pycache__" / "cached.pyc").write_text("bytes")
    (tmp_path / "src" / ".cache").mkdir()
    (tmp_path / "src" / ".cache" / "internal.py").write_text("cached = True\n")

    spec = CheckSpec(
        check_id="chk-1",
        check_type=CheckType.PYTEST,
        target_path="src/real.py",
    )
    scope = resolve_verification_scope(repo_path=tmp_path, check_spec=spec)
    assert "src/real.py" in scope
    assert not any("__pycache__" in s for s in scope)
    assert not any(".cache" in s for s in scope)

    # 3. Recursive scope limit exceeded in src/
    for i in range(10):
        (tmp_path / "src" / f"mod_{i}.py").write_text("x = 1\n")
    with pytest.raises(FingerprintError, match="exceeded maximum limit of 3"):
        resolve_verification_scope(repo_path=tmp_path, check_spec=spec, max_scope_files=3)

    # 4. Discovered file missing during compute_fingerprint for non-fake check
    # We resolve scope with an extra discovered file, then delete it before compute_fingerprint
    ephemeral_file = tmp_path / "src" / "ephemeral.py"
    ephemeral_file.write_text("temp = 1\n")
    spec_target = tmp_path / "src" / "target.py"
    spec_target.write_text("def test_it(): pass\n")
    spec_clean = CheckSpec(
        check_id="chk-clean",
        check_type=CheckType.PYTEST,
        target_path="src/target.py",
    )
    # Monkeypatch resolve_verification_scope to include a file that doesn't exist on disk
    with patch(
        "verifycraft.core.fingerprint.resolve_verification_scope",
        return_value=["src/target.py", "src/deleted_before_hash.py"],
    ):
        fp = compute_fingerprint(
            repo_path=str(tmp_path),
            revision="rev1",
            diff=None,
            target_files=[],
            check_spec=spec_clean,
        )
        assert fp.startswith("sha256:")

    # 5. Empty clean_target when target_path is "::func"
    spec_func_only = CheckSpec(
        check_id="chk-func",
        check_type=CheckType.PYTEST,
        target_path="::func_only",
    )
    scope_func = resolve_verification_scope(repo_path=tmp_path, check_spec=spec_func_only)
    assert isinstance(scope_func, list)

    # 6. Relative_to ValueError simulated at lines 153 (caught at 156) and 161 (caught at 170)
    call_count = 0
    orig_rel_to = Path.relative_to

    def conditional_rel_to(self: Path, other: Path) -> Path:
        nonlocal call_count
        call_count += 1
        if call_count in (1, 3):
            raise ValueError("Simulated relative_to failure")
        return orig_rel_to(self, other)

    with patch.object(Path, "relative_to", conditional_rel_to):
        scope_err = resolve_verification_scope(repo_path=tmp_path, check_spec=spec)
        assert isinstance(scope_err, list)
