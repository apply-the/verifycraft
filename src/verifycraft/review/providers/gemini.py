"""Gemini LLM provider adapter using the official Google GenAI SDK."""

from __future__ import annotations

import os
from typing import Any

from verifycraft.review.providers.base import (
    ProviderCallError,
    ProviderConfigurationError,
    ProviderResponse,
    ProviderResponseParser,
    ReviewerModelAdapter,
)

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


class GeminiReviewerAdapter(ReviewerModelAdapter):
    """Reviewer model adapter using Google Gemini through the official google-genai SDK."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
    ):
        resolved_key = (
            api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        )
        if not resolved_key:
            raise ProviderConfigurationError(
                "Gemini provider requires GEMINI_API_KEY or GOOGLE_API_KEY environment variable."
            )
        self.api_key: str = resolved_key

        self.model_name = (
            model_name or os.environ.get("VERIFYCRAFT_GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
        )
        self._client: Any = None

    def _sanitize_secrets(self, text: str) -> str:
        """Sanitize API keys and secret patterns from strings and exceptions."""
        return ProviderResponseParser.sanitize_secrets(text, secret=self.api_key)

    def _get_client(self) -> Any:
        """Lazily initialize the google-genai client."""
        if self._client is not None:
            return self._client

        try:
            from google import genai
        except ImportError as e:
            raise ProviderConfigurationError(
                "google-genai package is not installed. "
                "Install with: pip install 'verifycraft[gemini]'"
            ) from e

        try:
            self._client = genai.Client(api_key=self.api_key)
            return self._client
        except Exception as e:
            clean_err = self._sanitize_secrets(str(e))
            raise ProviderConfigurationError(
                f"Failed to initialize Gemini client: {clean_err}"
            ) from e

    def review(
        self,
        prompt: str,
        system_instruction: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> ProviderResponse:
        """Execute a review prompt with Gemini and return structured findings."""
        client = self._get_client()

        system_prompt = ProviderResponseParser.build_system_prompt(system_instruction)
        full_prompt = ProviderResponseParser.build_prompt_with_schema(prompt)

        try:
            from google.genai import types

            http_opts = None
            if timeout_seconds and timeout_seconds > 0:
                http_opts = types.HttpOptions(timeout=int(timeout_seconds * 1000))

            response = client.models.generate_content(
                model=self.model_name,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    temperature=0.1,
                    http_options=http_opts,
                ),
            )
        except Exception as e:
            clean_err = self._sanitize_secrets(str(e))
            if "timeout" in clean_err.lower() or "deadline" in clean_err.lower():
                raise ProviderCallError(
                    f"Gemini API call timed out after {timeout_seconds}s: {clean_err}"
                ) from e
            raise ProviderCallError(f"Gemini API call failed: {clean_err}") from e

        raw_text = response.text or ""
        tokens_used = None
        prompt_tokens = None
        output_tokens = None
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            tokens_used = getattr(response.usage_metadata, "total_token_count", None)
            prompt_tokens = getattr(response.usage_metadata, "prompt_token_count", None)
            output_tokens = getattr(response.usage_metadata, "candidates_token_count", None)

        return self._parse_json_response(
            raw_text,
            tokens_used=tokens_used,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
        )

    def _parse_json_response(
        self,
        text: str,
        tokens_used: int | None = None,
        prompt_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> ProviderResponse:
        """Parse and sanitize JSON response from model."""
        return ProviderResponseParser.parse_json_response(
            text,
            tokens_used=tokens_used,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
        )
