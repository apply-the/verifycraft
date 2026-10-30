"""Provider adapters module for VerifyCraft review subsystem."""

from verifycraft.review.providers.anthropic import (
    DEFAULT_ANTHROPIC_MODEL,
    AnthropicReviewerAdapter,
)
from verifycraft.review.providers.base import (
    LOOPBACK_HOSTS,
    ProviderCallError,
    ProviderConfig,
    ProviderConfigurationError,
    ProviderError,
    ProviderResponse,
    ProviderResponseParser,
    RawCandidateFinding,
    RawRequirementAssessment,
    ReviewerModelAdapter,
    is_loopback_url,
    load_dotenv_if_present,
    validate_endpoint_url,
)
from verifycraft.review.providers.bedrock import (
    DEFAULT_BEDROCK_MODEL,
    BedrockReviewerAdapter,
)
from verifycraft.review.providers.deterministic import (
    DeterministicReviewerAdapter,
    FakeReviewerAdapter,
)
from verifycraft.review.providers.gemini import (
    DEFAULT_GEMINI_MODEL,
    GeminiReviewerAdapter,
)
from verifycraft.review.providers.openai_compatible import OpenAICompatibleReviewerAdapter
from verifycraft.review.providers.registry import (
    PROVIDER_CONFIGS,
    format_provider_disclosure,
    is_local_provider,
    normalize_provider_name,
    resolve_provider_adapter,
)

__all__ = [
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_BEDROCK_MODEL",
    "DEFAULT_GEMINI_MODEL",
    "LOOPBACK_HOSTS",
    "AnthropicReviewerAdapter",
    "BedrockReviewerAdapter",
    "DeterministicReviewerAdapter",
    "FakeReviewerAdapter",
    "GeminiReviewerAdapter",
    "OpenAICompatibleReviewerAdapter",
    "ProviderCallError",
    "ProviderConfig",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderResponse",
    "ProviderResponseParser",
    "RawCandidateFinding",
    "RawRequirementAssessment",
    "ReviewerModelAdapter",
    "PROVIDER_CONFIGS",
    "format_provider_disclosure",
    "is_local_provider",
    "is_loopback_url",
    "load_dotenv_if_present",
    "normalize_provider_name",
    "resolve_provider_adapter",
    "validate_endpoint_url",
]
