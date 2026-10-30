"""Provider registry and factory for VerifyCraft review subsystem."""

from __future__ import annotations

from verifycraft.review.providers.anthropic import (
    DEFAULT_ANTHROPIC_MODEL,
    AnthropicReviewerAdapter,
)
from verifycraft.review.providers.base import (
    ProviderConfig,
    ProviderConfigurationError,
    ReviewerModelAdapter,
    is_loopback_url,
)
from verifycraft.review.providers.bedrock import (
    DEFAULT_BEDROCK_MODEL,
    BedrockReviewerAdapter,
)
from verifycraft.review.providers.deterministic import DeterministicReviewerAdapter
from verifycraft.review.providers.gemini import (
    DEFAULT_GEMINI_MODEL,
    GeminiReviewerAdapter,
)
from verifycraft.review.providers.openai_compatible import OpenAICompatibleReviewerAdapter

PROVIDER_CONFIGS: dict[str, ProviderConfig] = {
    # OpenAI and OpenAI-compatible cloud providers
    "openai": ProviderConfig(
        provider_name="openai",
        provider_type="openai_compatible",
        model="gpt-4o",
        base_url=None,
        api_key_env_var="OPENAI_API_KEY",
        is_local=False,
        supports_json_mode=True,
    ),
    "deepseek": ProviderConfig(
        provider_name="deepseek",
        provider_type="openai_compatible",
        model="deepseek-chat",
        base_url="https://api.deepseek.com",
        api_key_env_var="DEEPSEEK_API_KEY",
        is_local=False,
        supports_json_mode=True,
    ),
    "kimi": ProviderConfig(
        provider_name="kimi",
        provider_type="openai_compatible",
        model="moonshot-v1-8k",
        base_url="https://api.moonshot.cn/v1",
        api_key_env_var="MOONSHOT_API_KEY",
        is_local=False,
        supports_json_mode=True,
    ),
    "xai": ProviderConfig(
        provider_name="xai",
        provider_type="openai_compatible",
        model="grok-beta",
        base_url="https://api.x.ai/v1",
        api_key_env_var="XAI_API_KEY",
        is_local=False,
        supports_json_mode=True,
    ),
    "mistral": ProviderConfig(
        provider_name="mistral",
        provider_type="openai_compatible",
        model="mistral-large-latest",
        base_url="https://api.mistral.ai/v1",
        api_key_env_var="MISTRAL_API_KEY",
        is_local=False,
        supports_json_mode=True,
    ),
    "groq": ProviderConfig(
        provider_name="groq",
        provider_type="openai_compatible",
        model="llama-3.3-70b-versatile",
        base_url="https://api.groq.com/openai/v1",
        api_key_env_var="GROQ_API_KEY",
        is_local=False,
        supports_json_mode=True,
    ),
    "cerebras": ProviderConfig(
        provider_name="cerebras",
        provider_type="openai_compatible",
        model="llama3.1-70b",
        base_url="https://api.cerebras.ai/v1",
        api_key_env_var="CEREBRAS_API_KEY",
        is_local=False,
        supports_json_mode=True,
    ),
    "together": ProviderConfig(
        provider_name="together",
        provider_type="openai_compatible",
        model="meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
        base_url="https://api.together.xyz/v1",
        api_key_env_var="TOGETHER_API_KEY",
        is_local=False,
        supports_json_mode=True,
    ),
    "fireworks": ProviderConfig(
        provider_name="fireworks",
        provider_type="openai_compatible",
        model="accounts/fireworks/models/llama-v3p1-70b-instruct",
        base_url="https://api.fireworks.ai/inference/v1",
        api_key_env_var="FIREWORKS_API_KEY",
        is_local=False,
        supports_json_mode=True,
    ),
    "deepinfra": ProviderConfig(
        provider_name="deepinfra",
        provider_type="openai_compatible",
        model="meta-llama/Meta-Llama-3.1-70B-Instruct",
        base_url="https://api.deepinfra.com/v1/openai",
        api_key_env_var="DEEPINFRA_API_KEY",
        is_local=False,
        supports_json_mode=True,
    ),
    "openrouter": ProviderConfig(
        provider_name="openrouter",
        provider_type="openai_compatible",
        model="anthropic/claude-3.5-sonnet",
        base_url="https://openrouter.ai/api/v1",
        api_key_env_var="OPENROUTER_API_KEY",
        is_local=False,
        supports_json_mode=True,
    ),
    # Local inference servers
    "ollama": ProviderConfig(
        provider_name="ollama",
        provider_type="openai_compatible",
        model="llama3.1:latest",
        base_url="http://127.0.0.1:11434/v1",
        api_key_env_var="OLLAMA_API_KEY",
        default_api_key="ollama",
        is_local=True,
        supports_json_mode=True,
    ),
    "lmstudio": ProviderConfig(
        provider_name="lmstudio",
        provider_type="openai_compatible",
        model="local-model",
        base_url="http://127.0.0.1:1234/v1",
        api_key_env_var="LMSTUDIO_API_KEY",
        default_api_key="lm-studio",
        is_local=True,
        supports_json_mode=True,
    ),
    "vllm": ProviderConfig(
        provider_name="vllm",
        provider_type="openai_compatible",
        model="default",
        base_url="http://127.0.0.1:8000/v1",
        api_key_env_var="VLLM_API_KEY",
        default_api_key="vllm",
        is_local=True,
        supports_json_mode=True,
    ),
    "llamacpp": ProviderConfig(
        provider_name="llamacpp",
        provider_type="openai_compatible",
        model="default",
        base_url="http://127.0.0.1:8080/v1",
        api_key_env_var="LLAMACPP_API_KEY",
        default_api_key="llama.cpp",
        is_local=True,
        supports_json_mode=True,
    ),
    "openai-compatible": ProviderConfig(
        provider_name="openai-compatible",
        provider_type="openai_compatible",
        model="default",
        base_url=None,
        api_key_env_var="OPENAI_API_KEY",
        default_api_key="local",
        is_local=False,
        supports_json_mode=True,
    ),
    # Anthropic
    "anthropic": ProviderConfig(
        provider_name="anthropic",
        provider_type="anthropic",
        model=DEFAULT_ANTHROPIC_MODEL,
        api_key_env_var="ANTHROPIC_API_KEY",
        is_local=False,
    ),
    # AWS Bedrock
    "bedrock": ProviderConfig(
        provider_name="bedrock",
        provider_type="bedrock",
        model=DEFAULT_BEDROCK_MODEL,
        is_local=False,
    ),
    # Google Gemini
    "gemini": ProviderConfig(
        provider_name="gemini",
        provider_type="gemini",
        model=DEFAULT_GEMINI_MODEL,
        api_key_env_var="GEMINI_API_KEY",
        is_local=False,
    ),
    # Deterministic test provider
    "deterministic": ProviderConfig(
        provider_name="deterministic",
        provider_type="deterministic",
        model="deterministic",
        is_local=True,
    ),
}

PROVIDER_ALIASES: dict[str, str] = {
    "moonshot": "kimi",
    "grok": "xai",
    "llama.cpp": "llamacpp",
    "claude": "anthropic",
    "aws-bedrock": "bedrock",
    "google": "gemini",
    "fake": "deterministic",
    "none": "none",
    "model-free": "none",
    "agent": "none",
}


def normalize_provider_name(name: str) -> str:
    """Normalize provider name string and resolve aliases."""
    cleaned = name.lower().strip()
    return PROVIDER_ALIASES.get(cleaned, cleaned)


def is_local_provider(provider_name: str, base_url: str | None = None) -> bool:
    """Determine whether the specified provider or endpoint executes locally."""
    canonical = normalize_provider_name(provider_name)
    if canonical in ("none", "deterministic"):
        return True

    if base_url and is_loopback_url(base_url):
        return True

    cfg = PROVIDER_CONFIGS.get(canonical)
    return bool(cfg and cfg.is_local)


def format_provider_disclosure(
    provider_name: str,
    model: str | None = None,
    base_url: str | None = None,
) -> str:
    """Format explicit disclosure message for CLI and logging."""
    canonical = normalize_provider_name(provider_name)
    if canonical == "none":
        return "VerifyCraft: Model-free review mode selected. No LLM calls will be made."

    cfg = PROVIDER_CONFIGS.get(canonical)
    effective_model = model or (cfg.model if cfg else "default")
    effective_url = base_url or (cfg.base_url if cfg else None)

    if is_local_provider(provider_name, base_url=base_url):
        endpoint_desc = effective_url or "local loopback endpoint"
        return (
            f"VerifyCraft: Local provider '{provider_name}' selected ({endpoint_desc}). "
            "Source code will be processed on the local server without remote cloud transmission."
        )

    api_target = "the Gemini API" if canonical == "gemini" else f"the {provider_name} API"
    return (
        f"VerifyCraft: Remote provider '{provider_name}' selected (model: '{effective_model}'). "
        f"Note: git diff and relevant surrounding code will be transmitted to {api_target}."
    )


def resolve_provider_adapter(
    provider_name: str,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    allow_insecure_http: bool = False,
) -> ReviewerModelAdapter | None:
    """Instantiate and return a configured ReviewerModelAdapter."""
    canonical = normalize_provider_name(provider_name)

    if canonical == "none":
        return None

    if canonical == "deterministic":
        return DeterministicReviewerAdapter()

    cfg = PROVIDER_CONFIGS.get(canonical)
    if not cfg:
        supported = ", ".join(sorted(set(list(PROVIDER_CONFIGS.keys()) + ["none"])))
        raise ProviderConfigurationError(
            f"Unsupported provider: '{provider_name}'. Supported providers: {supported}."
        )

    if cfg.provider_type == "openai_compatible":
        return OpenAICompatibleReviewerAdapter(
            config=cfg,
            model_name=model or cfg.model,
            base_url=base_url or cfg.base_url,
            api_key=api_key,
            allow_insecure_http=allow_insecure_http,
        )

    if cfg.provider_type == "anthropic":
        return AnthropicReviewerAdapter(
            api_key=api_key,
            model_name=model or cfg.model,
            base_url=base_url,
            allow_insecure_http=allow_insecure_http,
        )

    if cfg.provider_type == "bedrock":
        return BedrockReviewerAdapter(
            model_name=model or cfg.model,
        )

    if cfg.provider_type == "gemini":
        return GeminiReviewerAdapter(
            api_key=api_key,
            model_name=model or cfg.model,
        )

    raise ProviderConfigurationError(
        f"Unknown provider type '{cfg.provider_type}' for '{canonical}'."
    )
