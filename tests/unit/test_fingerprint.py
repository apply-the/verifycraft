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
