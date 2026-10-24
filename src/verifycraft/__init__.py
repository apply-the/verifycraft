"""VerifyCraft: Evidence-aware code review and verification engine."""

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
from verifycraft.contracts.review import (
    ConventionalLabel,
    EvidenceStatus,
    FindingSeverity,
    ModelInfo,
    RequirementAssessment,
    RequirementStatus,
    ReviewFinding,
    ReviewPolicy,
    ReviewReport,
    ReviewRequest,
    ReviewResourceUsage,
    ReviewStatus,
)
from verifycraft.core.fingerprint import FingerprintError
from verifycraft.core.policy import PolicyViolationError
from verifycraft.core.runner import (
    CheckRunner,
    FakeRunner,
    TrustedPytestRunner,
)
from verifycraft.engine import VerificationEngine
from verifycraft.review.engine import ReviewEngine

__version__ = "0.1.0"

__all__ = [
    "CheckRunner",
    "CheckSpec",
    "CheckType",
    "ConventionalLabel",
    "Evidence",
    "EvidenceStatus",
    "ExecutionMode",
    "ExecutionPolicy",
    "ExpectedObservation",
    "FakeRunner",
    "Finding",
    "FindingSeverity",
    "FingerprintError",
    "Hypothesis",
    "ModelInfo",
    "PolicyViolationError",
    "RequirementAssessment",
    "RequirementStatus",
    "ResourceUsage",
    "ReviewEngine",
    "ReviewFinding",
    "ReviewPolicy",
    "ReviewReport",
    "ReviewRequest",
    "ReviewResourceUsage",
    "ReviewStatus",
    "TrustedPytestRunner",
    "Verdict",
    "VerificationEngine",
    "VerificationInput",
    "VerificationResult",
    "__version__",
]
