"""Anthropic Claude LLM provider adapter using the official Anthropic Messages API."""

from __future__ import annotations

import os
from typing import Any

from verifycraft.review.providers.base import (
    ProviderCallError,
    ProviderConfigurationError,
    ProviderResponse,
    ProviderResponseParser,
    ReviewerModelAdapter,
    validate_endpoint_url,
)

DEFAULT_ANTHROPIC_MODEL = "claude-3-5-sonnet-20241022"


class AnthropicReviewerAdapter(ReviewerModelAdapter):
    """Reviewer model adapter using Anthropic Claude through the official Anthropic SDK."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        base_url: str | None = None,
        allow_insecure_http: bool = False,
    ):
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ProviderConfigurationError(
                "Anthropic provider requires ANTHROPIC_API_KEY environment variable."
            )
        self.api_key: str = resolved_key
        self.model_name = (
            model_name or os.environ.get("VERIFYCRAFT_ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL
        )
        self.base_url = base_url
        self.allow_insecure_http = allow_insecure_http
        if self.base_url:
            validate_endpoint_url(self.base_url, allow_insecure_http=self.allow_insecure_http)
        self._client: Any = None

    def _sanitize(self, text: str) -> str:
        return ProviderResponseParser.sanitize_secrets(text, secret=self.api_key)

    def _get_client(self, timeout_seconds: float = 60.0) -> Any:
        if self._client is not None:
            return self._client

        try:
            from anthropic import Anthropic
        except ImportError as e:
            raise ProviderConfigurationError(
                "The 'anthropic' package is required. "
                "Install with: pip install 'verifycraft[anthropic]'"
            ) from e

        try:
            self._client = Anthropic(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=timeout_seconds,
            )
            return self._client
        except Exception as e:
            clean_err = self._sanitize(str(e))
            raise ProviderConfigurationError(
                f"Failed to initialize Anthropic client: {clean_err}"
            ) from e

    def review(
        self,
        prompt: str,
        system_instruction: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> ProviderResponse:
        """Execute review prompt with Claude via Anthropic Messages API."""
        client = self._get_client(timeout_seconds=timeout_seconds)

        system_prompt = ProviderResponseParser.build_system_prompt(system_instruction)
        full_prompt = ProviderResponseParser.build_prompt_with_schema(prompt)

        try:
            response = client.messages.create(
                model=self.model_name,
                system=system_prompt,
                messages=[{"role": "user", "content": full_prompt}],
                max_tokens=4096,
                temperature=0.1,
                timeout=timeout_seconds,
            )
        except Exception as e:
            clean_err = self._sanitize(str(e))
            err_lower = clean_err.lower()
            if "timeout" in err_lower or "timed out" in err_lower or "deadline" in err_lower:
                raise ProviderCallError(
                    f"Anthropic API call timed out after {timeout_seconds}s: {clean_err}"
                ) from e
            raise ProviderCallError(f"Anthropic API call failed: {clean_err}") from e

        raw_parts = []
        for block in getattr(response, "content", []):
            if getattr(block, "type", "") == "text":
                raw_parts.append(getattr(block, "text", ""))
        raw_text = "\n".join(raw_parts)

        prompt_tokens = None
        output_tokens = None
        tokens_used = None
        if hasattr(response, "usage") and response.usage:
            prompt_tokens = getattr(response.usage, "input_tokens", None)
            output_tokens = getattr(response.usage, "output_tokens", None)
            if prompt_tokens is not None and output_tokens is not None:
                tokens_used = prompt_tokens + output_tokens

        return ProviderResponseParser.parse_json_response(
            raw_text,
            tokens_used=tokens_used,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
        )
