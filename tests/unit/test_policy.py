"""Unit tests for execution policy validation and security boundaries."""

from verifycraft.contracts import (
    CheckSpec,
    CheckType,
    ExecutionMode,
    ExecutionPolicy,
)
from verifycraft.core.policy import (
    sanitize_environment,
    validate_execution_authorization,
)


def test_dry_run_is_blocked() -> None:
    policy = ExecutionPolicy(mode=ExecutionMode.DRY_RUN)
    spec = CheckSpec(check_id="c1", check_type=CheckType.PYTEST, target_path="test.py")
    reason = validate_execution_authorization(policy, spec)
    assert reason is not None
    assert "dry_run" in reason


def test_untrusted_code_is_blocked_locally() -> None:
    policy = ExecutionPolicy(mode=ExecutionMode.TRUSTED_LOCAL, untrusted_code=True)
    spec = CheckSpec(check_id="c1", check_type=CheckType.PYTEST, target_path="test.py")
    reason = validate_execution_authorization(policy, spec)
    assert reason is not None
    assert "Untrusted code execution is blocked" in reason
    assert "isolated container" in reason


def test_untrusted_code_in_container_mode_reports_not_available() -> None:
    policy = ExecutionPolicy(mode=ExecutionMode.ISOLATED_CONTAINER, untrusted_code=True)
    spec = CheckSpec(check_id="c1", check_type=CheckType.PYTEST, target_path="test.py")
    reason = validate_execution_authorization(policy, spec)
    assert reason is not None
    assert "not available in this iteration" in reason


def test_disallowed_check_type() -> None:
    policy = ExecutionPolicy(
        mode=ExecutionMode.TRUSTED_LOCAL,
        allowed_check_types=[CheckType.PYTEST],
    )
    spec = CheckSpec(check_id="c1", check_type=CheckType.STATIC, target_path="src/")
    reason = validate_execution_authorization(policy, spec)
    assert reason is not None
    assert "not permitted by policy" in reason


def test_trusted_local_authorized_when_conditions_met() -> None:
    policy = ExecutionPolicy(
        mode=ExecutionMode.TRUSTED_LOCAL,
        untrusted_code=False,
        allowed_check_types=[CheckType.PYTEST],
    )
    spec = CheckSpec(check_id="c1", check_type=CheckType.PYTEST, target_path="test.py")
    reason = validate_execution_authorization(policy, spec)
    assert reason is None


def test_sanitize_environment_scrubs_secrets() -> None:
    policy = ExecutionPolicy(env_allowlist=["PATH", "TMPDIR"])
    dirty_env = {
        "PATH": "/usr/bin:/bin",
        "TMPDIR": "/tmp",
        "AWS_SECRET_ACCESS_KEY": "supersecret",
        "GITHUB_TOKEN": "ghp_1234567890",
        "DATABASE_PASSWORD": "pass",
        "PATH_SECRET": "hacked",  # Contains SECRET even though starts with PATH
        "RANDOM_VAR": "val",
    }
    cleaned = sanitize_environment(
        policy,
        base_env=dirty_env,
        overrides={"MY_CUSTOM_FLAG": "1", "LEAKED_API_KEY": "secret"},
    )
    assert cleaned == {
        "PATH": "/usr/bin:/bin",
        "TMPDIR": "/tmp",
        "MY_CUSTOM_FLAG": "1",
    }
    assert "AWS_SECRET_ACCESS_KEY" not in cleaned
    assert "GITHUB_TOKEN" not in cleaned
    assert "DATABASE_PASSWORD" not in cleaned
    assert "PATH_SECRET" not in cleaned
    assert "RANDOM_VAR" not in cleaned
    assert "LEAKED_API_KEY" not in cleaned
