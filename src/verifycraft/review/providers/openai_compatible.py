"""OpenAI-compatible LLM provider adapter.

Shared transport implementation for all OpenAI-protocol-compatible endpoints,
including cloud providers (OpenAI, DeepSeek, Kimi, Grok, Mistral, Groq, Cerebras,
Together, Fireworks, DeepInfra, OpenRouter) and local inference servers
(Ollama, LM Studio, vLLM, llama.cpp server, and custom openai-compatible endpoints).
"""

from __future__ import annotations

import os
from typing import Any

from verifycraft.review.providers.base import (
    ProviderCallError,
    ProviderConfig,
    ProviderConfigurationError,
    ProviderResponse,
    ProviderResponseParser,
    ReviewerModelAdapter,
    is_loopback_url,
    validate_endpoint_url,
)


def _resolve_endpoint(
    config: ProviderConfig | None,
    base_url: str | None,
    allow_insecure_http: bool,
) -> tuple[str | None, bool]:
    resolved_base_url = base_url or (config.base_url if config else None)
    if resolved_base_url:
        validate_endpoint_url(resolved_base_url, allow_insecure_http=allow_insecure_http)
    is_local_target = (config.is_local if config else False) or (
        bool(resolved_base_url and is_loopback_url(resolved_base_url))
    )
    return resolved_base_url, is_local_target


def _resolve_model(
    config: ProviderConfig | None,
    model_name: str | None,
    provider_name: str,
) -> str:
    resolved_model = model_name or (config.model if config else None)
    if not resolved_model:
        raise ProviderConfigurationError(
            f"Model identifier is required for provider '{provider_name}'. "
            "Specify --model <model-id> or set in configuration."
        )
    return resolved_model


def _resolve_api_key(
    config: ProviderConfig | None,
    api_key: str | None,
    is_local_target: bool,
    provider_name: str,
) -> str:
    resolved_key = api_key
    if not resolved_key and config and config.api_key_env_var:
        resolved_key = os.environ.get(config.api_key_env_var)
    if not resolved_key:
        resolved_key = os.environ.get("OPENAI_API_KEY")

    if resolved_key:
        return resolved_key

    if is_local_target:
        return (config.default_api_key if config else None) or "local"

    env_hint = config.api_key_env_var if config else "OPENAI_API_KEY"
    raise ProviderConfigurationError(
        f"Provider '{provider_name}' requires environment variable '{env_hint}'."
    )


class OpenAICompatibleReviewerAdapter(ReviewerModelAdapter):
    """Reviewer adapter for any OpenAI-compatible API endpoint."""

    def __init__(
        self,
        config: ProviderConfig | None = None,
        model_name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        allow_insecure_http: bool = False,
        supports_json_mode: bool | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        self.config = config
        self.provider_name = config.provider_name if config else "openai-compatible"
        self.base_url, self.is_local = _resolve_endpoint(config, base_url, allow_insecure_http)
        self.model_name = _resolve_model(config, model_name, self.provider_name)
        self.api_key = _resolve_api_key(config, api_key, self.is_local, self.provider_name)

        if supports_json_mode is not None:
            self.supports_json_mode = supports_json_mode
        elif config is not None:
            self.supports_json_mode = config.supports_json_mode
        else:
            self.supports_json_mode = True

        self.extra_headers: dict[str, str] = extra_headers or (
            dict(config.extra_headers) if config else {}
        )
        self._client: Any = None

    def _sanitize(self, text: str) -> str:
        return ProviderResponseParser.sanitize_secrets(text, secret=self.api_key)

    def _get_client(self, timeout_seconds: float = 60.0) -> Any:
        if self._client is not None:
            return self._client

        try:
            from openai import OpenAI
        except ImportError as e:
            raise ProviderConfigurationError(
                "The 'openai' package is required for OpenAI-compatible providers. "
                "Install with: pip install 'verifycraft[openai]'"
            ) from e

        try:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                default_headers=self.extra_headers or None,
                timeout=timeout_seconds,
            )
            return self._client
        except Exception as e:
            clean_err = self._sanitize(str(e))
            raise ProviderConfigurationError(
                f"Failed to initialize OpenAI client for '{self.provider_name}': {clean_err}"
            ) from e

    def review(
        self,
        prompt: str,
        system_instruction: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> ProviderResponse:
        """Execute a review prompt with an OpenAI-compatible endpoint."""
        client = self._get_client(timeout_seconds=timeout_seconds)

        system_prompt = ProviderResponseParser.build_system_prompt(system_instruction)
        full_prompt = ProviderResponseParser.build_prompt_with_schema(prompt)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_prompt},
        ]

        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0.1,
            "timeout": timeout_seconds,
        }

        if self.supports_json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = self._call_completion(client, kwargs, timeout_seconds)

        raw_text = ""
        if response.choices:
            choice = response.choices[0]
            message = choice.message
            raw_text = message.content or ""
            if not raw_text.strip() and hasattr(message, "reasoning_content"):
                raw_text = getattr(message, "reasoning_content", "") or ""

        tokens_used = None
        prompt_tokens = None
        output_tokens = None
        if hasattr(response, "usage") and response.usage:
            tokens_used = getattr(response.usage, "total_tokens", None)
            prompt_tokens = getattr(response.usage, "prompt_tokens", None)
            output_tokens = getattr(response.usage, "completion_tokens", None)

        return ProviderResponseParser.parse_json_response(
            raw_text,
            tokens_used=tokens_used,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
        )

    def _call_completion(
        self,
        client: Any,
        kwargs: dict[str, Any],
        timeout_seconds: float,
    ) -> Any:
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as e:
            err_msg = str(e).lower()
            # If endpoint does not support response_format, fallback to prompt-guided JSON
            if "response_format" in kwargs and (
                "response_format" in err_msg or "json_object" in err_msg or "schema" in err_msg
            ):
                fallback_kwargs = dict(kwargs)
                del fallback_kwargs["response_format"]
                try:
                    return client.chat.completions.create(**fallback_kwargs)
                except Exception as inner_e:
                    self._handle_api_error(inner_e, timeout_seconds)
            self._handle_api_error(e, timeout_seconds)

    def _handle_api_error(self, exc: Exception, timeout_seconds: float) -> None:
        clean_err = self._sanitize(str(exc))
        err_lower = clean_err.lower()
        if "timeout" in err_lower or "timed out" in err_lower or "deadline" in err_lower:
            raise ProviderCallError(
                f"{self.provider_name} API call timed out after {timeout_seconds}s: {clean_err}"
            ) from exc
        raise ProviderCallError(f"{self.provider_name} API call failed: {clean_err}") from exc
