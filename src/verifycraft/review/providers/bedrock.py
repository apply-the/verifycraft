"""AWS Bedrock LLM provider adapter using the Bedrock Converse API."""

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

DEFAULT_BEDROCK_MODEL = "anthropic.claude-3-5-sonnet-20240620-v1:0"


class BedrockReviewerAdapter(ReviewerModelAdapter):
    """Reviewer model adapter using AWS Bedrock through the official boto3 Converse API.

    Supports standard AWS credential resolution chain (environment variables,
    shared credentials/config files, IAM roles, SSO, etc.).
    """

    def __init__(
        self,
        model_name: str | None = None,
        region_name: str | None = None,
        session_kwargs: dict[str, Any] | None = None,
    ):
        self.model_name = (
            model_name or os.environ.get("VERIFYCRAFT_BEDROCK_MODEL") or DEFAULT_BEDROCK_MODEL
        )
        self.region_name = (
            region_name
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "us-east-1"
        )
        self.session_kwargs = session_kwargs or {}
        self._client: Any = None

    def _sanitize(self, text: str) -> str:
        return ProviderResponseParser.sanitize_secrets(text)

    def _get_client(self, timeout_seconds: float = 60.0) -> Any:
        if self._client is not None:
            return self._client

        try:
            import boto3
            from botocore.config import Config
        except ImportError as e:
            raise ProviderConfigurationError(
                "The 'boto3' package is required for AWS Bedrock. "
                "Install with: pip install 'verifycraft[bedrock]'"
            ) from e

        try:
            config = Config(
                region_name=self.region_name,
                connect_timeout=min(10.0, timeout_seconds),
                read_timeout=timeout_seconds,
                retries={"max_attempts": 2, "mode": "standard"},
            )
            session = boto3.Session(**self.session_kwargs)
            self._client = session.client("bedrock-runtime", config=config)
            return self._client
        except Exception as e:
            clean_err = self._sanitize(str(e))
            raise ProviderConfigurationError(
                f"Failed to initialize AWS Bedrock client: {clean_err}"
            ) from e

    def review(
        self,
        prompt: str,
        system_instruction: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> ProviderResponse:
        """Execute review prompt with AWS Bedrock Converse API."""
        client = self._get_client(timeout_seconds=timeout_seconds)

        system_prompt = ProviderResponseParser.build_system_prompt(system_instruction)
        full_prompt = ProviderResponseParser.build_prompt_with_schema(prompt)

        messages = [
            {
                "role": "user",
                "content": [{"text": full_prompt}],
            }
        ]
        system_blocks = [{"text": system_prompt}]

        try:
            response = client.converse(
                modelId=self.model_name,
                messages=messages,
                system=system_blocks,
                inferenceConfig={
                    "temperature": 0.1,
                    "maxTokens": 4096,
                },
            )
        except Exception as e:
            clean_err = self._sanitize(str(e))
            err_lower = clean_err.lower()
            if "timeout" in err_lower or "timed out" in err_lower or "read timeout" in err_lower:
                raise ProviderCallError(
                    f"AWS Bedrock API call timed out after {timeout_seconds}s: {clean_err}"
                ) from e
            raise ProviderCallError(f"AWS Bedrock API call failed: {clean_err}") from e

        # Extract text from converse response
        raw_parts: list[str] = []
        output = response.get("output", {})
        message = output.get("message", {})
        for content_item in message.get("content", []):
            if "text" in content_item:
                raw_parts.append(content_item["text"])
        raw_text = "\n".join(raw_parts)

        # Extract token usage
        usage = response.get("usage", {})
        prompt_tokens = usage.get("inputTokens")
        output_tokens = usage.get("outputTokens")
        tokens_used = usage.get("totalTokens")
        if tokens_used is None and prompt_tokens is not None and output_tokens is not None:
            tokens_used = prompt_tokens + output_tokens

        return ProviderResponseParser.parse_json_response(
            raw_text,
            tokens_used=tokens_used,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
        )
