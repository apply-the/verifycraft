"""Unit tests for Gemini reviewer adapter."""

from __future__ import annotations

import sys
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

from verifycraft.review.providers.base import (
    ProviderCallError,
    ProviderConfigurationError,
)
from verifycraft.review.providers.gemini import GeminiReviewerAdapter


@pytest.fixture
def mock_genai_env() -> Generator[MagicMock, None, None]:
    """Mock the google and google.genai modules so tests run even without google-genai."""
    mock_genai = MagicMock()
    mock_types = MagicMock()
    mock_genai.types = mock_types

    with patch.dict(
        sys.modules,
        {
            "google": MagicMock(genai=mock_genai),
            "google.genai": mock_genai,
            "google.genai.types": mock_types,
        },
    ):
        yield mock_genai


def test_gemini_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(ProviderConfigurationError, match="GEMINI_API_KEY or GOOGLE_API_KEY"):
        GeminiReviewerAdapter()


def test_gemini_custom_api_key_and_model() -> None:
    adapter = GeminiReviewerAdapter(api_key="custom-key-123", model_name="gemini-custom")
    assert adapter.api_key == "custom-key-123"
    assert adapter.model_name == "gemini-custom"


def test_gemini_missing_package(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = GeminiReviewerAdapter(api_key="dummy-key")
    with (
        patch.dict(sys.modules, {"google.genai": None, "google": None}),
        pytest.raises(ProviderConfigurationError, match="google-genai package is not installed"),
    ):
        adapter._get_client()


def test_gemini_client_init_error_redacts_key(mock_genai_env: MagicMock) -> None:
    adapter = GeminiReviewerAdapter(api_key="super-secret-key-xyz")
    mock_genai_env.Client.side_effect = ValueError("Failed with super-secret-key-xyz")
    with pytest.raises(ProviderConfigurationError) as exc_info:
        adapter._get_client()
    assert "super-secret-key-xyz" not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


def test_gemini_review_successful_json(mock_genai_env: MagicMock) -> None:
    adapter = GeminiReviewerAdapter(api_key="test-key")
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = """
    {
        "summary": "Auth token check is missing.",
        "candidate_findings": [
            {
                "file_path": "src/auth.py",
                "line": 42,
                "end_line": 45,
                "label": "issue",
                "decorations": ["blocking"],
                "subject": "Missing expiry check",
                "description": "Token expiry check omitted.",
                "severity": "high",
                "blocking": true,
                "requirement_ids": ["REQ-AUTH-01"],
                "suggested_action": "Add expires_at check."
            }
        ],
        "requirement_assessments": [
            {
                "requirement_id": "REQ-AUTH-01",
                "status": "not_satisfied",
                "explanation": "No expiration check observed.",
                "related_locations": ["src/auth.py:42"]
            }
        ]
    }
    """
    mock_response.usage_metadata.total_token_count = 512
    mock_response.usage_metadata.prompt_token_count = 350
    mock_response.usage_metadata.candidates_token_count = 162
    mock_client.models.generate_content.return_value = mock_response
    adapter._client = mock_client

    result = adapter.review("Analyze diff")
    assert result.summary == "Auth token check is missing."
    assert len(result.candidate_findings) == 1
    assert result.candidate_findings[0].file_path == "src/auth.py"
    assert result.candidate_findings[0].line == 42
    assert result.candidate_findings[0].end_line == 45
    assert result.candidate_findings[0].blocking is True
    assert len(result.requirement_assessments) == 1
    assert result.requirement_assessments[0].requirement_id == "REQ-AUTH-01"
    assert result.tokens_used == 512
    assert result.prompt_tokens == 350
    assert result.output_tokens == 162


def test_gemini_review_markdown_fenced_json(mock_genai_env: MagicMock) -> None:
    adapter = GeminiReviewerAdapter(api_key="test-key")
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = """```json
    {
        "summary": "Clean diff.",
        "candidate_findings": [],
        "requirement_assessments": []
    }
    ```"""
    mock_response.usage_metadata = None
    mock_client.models.generate_content.return_value = mock_response
    adapter._client = mock_client

    result = adapter.review("Analyze clean diff", system_instruction="Extra context")
    assert result.summary == "Clean diff."
    assert len(result.candidate_findings) == 0


def test_gemini_review_api_call_failure_redacts_key(mock_genai_env: MagicMock) -> None:
    adapter = GeminiReviewerAdapter(api_key="sensitive-gemini-key")
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = RuntimeError(
        "API error with key sensitive-gemini-key: quota exceeded"
    )
    adapter._client = mock_client

    with pytest.raises(ProviderCallError) as exc_info:
        adapter.review("Analyze")
    assert "sensitive-gemini-key" not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


def test_gemini_review_invalid_json(mock_genai_env: MagicMock) -> None:
    adapter = GeminiReviewerAdapter(api_key="test-key")
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "This is not valid json at all!"
    mock_client.models.generate_content.return_value = mock_response
    adapter._client = mock_client

    with pytest.raises(ProviderCallError, match="Model returned invalid JSON"):
        adapter.review("Analyze")


def test_gemini_review_malformed_structure(mock_genai_env: MagicMock) -> None:
    adapter = GeminiReviewerAdapter(api_key="test-key")
    mock_client = MagicMock()
    mock_response = MagicMock()
    # candidate_findings line is an unparseable string
    mock_response.text = '{"candidate_findings": [{"line": "invalid_int"}]}'
    mock_client.models.generate_content.return_value = mock_response
    adapter._client = mock_client

    with pytest.raises(ProviderCallError, match="Failed to validate model response structure"):
        adapter.review("Analyze")
