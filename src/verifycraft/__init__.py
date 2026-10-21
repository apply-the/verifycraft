"""VerifyCraft: Composable review, testing, and verification capabilities for AI agents."""

from verifycraft.contracts.models import (
    CheckSpec,
    CheckType,
    Evidence,
    ExecutionMode,
    ExecutionPolicy,
    ExpectedObservation,
    Finding,
    Hypothesis,
    ResourceUsage,
    Verdict,
    VerificationInput,
    VerificationResult,
)
from verifycraft.core.fingerprint import FingerprintError
from verifycraft.core.policy import PolicyViolationError
from verifycraft.core.runner import (
    CheckRunner,
    FakeRunner,
    TrustedPytestRunner,
)
from verifycraft.engine import VerificationEngine

__version__ = "0.1.0a1"

__all__ = [
    "CheckRunner",
    "CheckSpec",
    "CheckType",
    "Evidence",
    "ExecutionMode",
    "ExecutionPolicy",
    "ExpectedObservation",
    "FakeRunner",
    "Finding",
    "FingerprintError",
    "Hypothesis",
    "PolicyViolationError",
    "ResourceUsage",
    "TrustedPytestRunner",
    "Verdict",
    "VerificationEngine",
    "VerificationInput",
    "VerificationResult",
    "__version__",
]
