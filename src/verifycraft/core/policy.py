"""Execution policy validation and security enforcement for VerifyCraft.

Enforces execution permissions, check allowlists, untrusted code boundaries,
and environment sanitization to prevent secret leakage.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from verifycraft.contracts.models import (
    CheckSpec,
    ExecutionMode,
    ExecutionPolicy,
)

SENSITIVE_ENV_SUBSTRINGS = (
    "KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "CREDENTIAL",
    "AUTH",
    "BEARER",
    "SIGNATURE",
    "PRIVATE",
)


class PolicyViolationError(Exception):
    """Raised when an operation violates the execution policy."""


def validate_execution_authorization(
    policy: ExecutionPolicy,
    check_spec: CheckSpec,
    repo_path: str | None = None,
) -> str | None:
    """Validate whether check execution is authorized under the given policy.

    Returns None if authorized, or a human-readable blocked reason string if execution
    must be blocked.
    """
    if policy.mode == ExecutionMode.DRY_RUN:
        return "Execution is not authorized: policy mode is 'dry_run'."

    if (
        policy.mode == ExecutionMode.TRUSTED_LOCAL
        and repo_path is not None
        and not Path(repo_path).is_dir()
    ):
        return f"Repository path '{repo_path}' does not exist or is not a directory."

    # Untrusted PR code safety boundary
    if policy.untrusted_code:
        if policy.mode != ExecutionMode.ISOLATED_CONTAINER:
            return (
                "Untrusted code execution is blocked: local execution of untrusted "
                "code is prohibited. An isolated container backend is required."
            )
        return (
            "Untrusted code execution is blocked: isolated container backend "
            "is not available in this iteration."
        )

    # Check type allowlist
    if check_spec.check_type not in policy.allowed_check_types:
        return (
            f"Check type '{check_spec.check_type.value}' is not permitted by policy. "
            f"Allowed types: {[t.value for t in policy.allowed_check_types]}."
        )

    return None


def sanitize_environment(
    policy: ExecutionPolicy,
    base_env: Mapping[str, str] | None = None,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Filter environment variables to prevent secret leakage into execution evidence.

    Only variables explicitly permitted by policy.env_allowlist are retained,
    and any variable containing sensitive substrings is scrubbed.
    """
    env_source = base_env if base_env is not None else os.environ
    sanitized: dict[str, str] = {}

    allowed_set = {var.upper() for var in policy.env_allowlist}

    for key, value in env_source.items():
        key_upper = key.upper()
        if key_upper not in allowed_set:
            continue

        # Reject any variable that might contain credentials even if matched
        if any(substr in key_upper for substr in SENSITIVE_ENV_SUBSTRINGS):
            continue

        sanitized[key] = value

    # Apply approved overrides (also filtered for obvious secret names)
    if overrides:
        for key, value in overrides.items():
            key_upper = key.upper()
            if any(substr in key_upper for substr in SENSITIVE_ENV_SUBSTRINGS):
                continue
            sanitized[key] = value

    return sanitized
