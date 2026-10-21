"""Core verification components for VerifyCraft."""

from verifycraft.core.evaluator import evaluate_hypothesis
from verifycraft.core.fingerprint import (
    FingerprintError,
    compute_fingerprint,
    is_evidence_stale,
)
from verifycraft.core.policy import (
    PolicyViolationError,
    sanitize_environment,
    validate_execution_authorization,
)
from verifycraft.core.runner import (
    CheckRunner,
    FakeRunner,
    TrustedPytestRunner,
)

__all__ = [
    "CheckRunner",
    "FakeRunner",
    "FingerprintError",
    "PolicyViolationError",
    "TrustedPytestRunner",
    "compute_fingerprint",
    "evaluate_hypothesis",
    "is_evidence_stale",
    "sanitize_environment",
    "validate_execution_authorization",
]
