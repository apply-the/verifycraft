"""Unit and contract tests for universal LLM providers and local inference."""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from verifycraft.contracts.review import ReviewRequest
from verifycraft.review.engine import ReviewEngine
from verifycraft.review.git import GitReviewContext
from verifycraft.review.providers.anthropic import AnthropicReviewerAdapter
from verifycraft.review.providers.base import (
    ProviderCallError,
    ProviderConfigurationError,
    ProviderResponseParser,
    is_loopback_url,
    validate_endpoint_url,
)
from verifycraft.review.providers.bedrock import BedrockReviewerAdapter
from verifycraft.review.providers.openai_compatible import OpenAICompatibleReviewerAdapter
from verifycraft.review.providers.registry import (
    PROVIDER_CONFIGS,
    format_provider_disclosure,
    is_local_provider,
    normalize_provider_name,
    resolve_provider_adapter,
)

SAMPLE_STRUCTURED_JSON = """
{
    "summary": "Detected potential timing leak.",
    "candidate_findings": [
        {
            "file_path": "src/crypto.py",
            "line": 42,
            "end_line": 45,
            "label": "issue",
            "decorations": ["blocking"],
            "subject": "Non-constant time comparison",
            "description": "Direct equality operator leaks timing information.",
            "severity": "high",
            "blocking": true,
            "requirement_ids": ["REQ-SEC-01"],
            "suggested_action": "Use hmac.compare_digest."
        }
    ],
    "requirement_assessments": [
        {
            "requirement_id": "REQ-SEC-01",
            "status": "not_satisfied",
            "explanation": "Equality operator violates constant-time comparison.",
            "related_locations": ["src/crypto.py:42"]
        }
    ]
}
"""


# ---------------------------------------------------------------------------
# 1. Loopback and Endpoint Security Tests
# ---------------------------------------------------------------------------


def test_is_loopback_url() -> None:
    assert is_loopback_url("http://localhost:11434/v1") is True
    assert is_loopback_url("http://127.0.0.1:8000/v1") is True
    assert is_loopback_url("http://[::1]:8080/v1") is True
    assert is_loopback_url("http://0.0.0.0:1234/v1") is True
    assert is_loopback_url("https://api.openai.com/v1") is False
    assert is_loopback_url("http://remote-server.com:8000/v1") is False
    assert is_loopback_url("invalid-url") is False


def test_validate_endpoint_url_https() -> None:
    # HTTPS is always allowed
    validate_endpoint_url("https://api.deepseek.com")
    validate_endpoint_url("https://api.openai.com/v1")


def test_validate_endpoint_url_loopback_http() -> None:
    # HTTP is allowed for loopback
    validate_endpoint_url("http://127.0.0.1:11434/v1")
    validate_endpoint_url("http://localhost:1234/v1")


def test_validate_endpoint_url_rejects_insecure_remote_http() -> None:
    with pytest.raises(ProviderConfigurationError, match="Insecure plain HTTP endpoint"):
        validate_endpoint_url("http://api.deepseek.com")


def test_validate_endpoint_url_allows_insecure_http_with_flag() -> None:
    # Explicit override allowed
    validate_endpoint_url("http://remote-internal-server:8000/v1", allow_insecure_http=True)


def test_validate_endpoint_url_unsupported_scheme() -> None:
    with pytest.raises(ProviderConfigurationError, match="Unsupported URL scheme 'ftp'"):
        validate_endpoint_url("ftp://example.com/api")


# ---------------------------------------------------------------------------
# 2. Registry, Provider & Model Selection Tests
# ---------------------------------------------------------------------------


def test_normalize_provider_name() -> None:
    assert normalize_provider_name("DeepSeek") == "deepseek"
    assert normalize_provider_name("grok") == "xai"
    assert normalize_provider_name("moonshot") == "kimi"
    assert normalize_provider_name("llama.cpp") == "llamacpp"
    assert normalize_provider_name("claude") == "anthropic"
    assert normalize_provider_name("aws-bedrock") == "bedrock"
    assert normalize_provider_name("fake") == "deterministic"
    assert normalize_provider_name("model-free") == "none"
    assert normalize_provider_name("agent") == "none"


def test_is_local_provider() -> None:
    assert is_local_provider("ollama") is True
    assert is_local_provider("lmstudio") is True
    assert is_local_provider("vllm") is True
    assert is_local_provider("llamacpp") is True
    assert is_local_provider("deterministic") is True
    assert is_local_provider("none") is True
    assert is_local_provider("deepseek") is False
    assert is_local_provider("openai") is False
    assert is_local_provider("anthropic") is False
    assert is_local_provider("openai-compatible", base_url="http://127.0.0.1:8000/v1") is True
    assert is_local_provider("openai-compatible", base_url="https://remote.api.com/v1") is False


def test_format_provider_disclosure() -> None:
    disc_none = format_provider_disclosure("none")
    assert "Model-free review mode selected" in disc_none

    disc_local = format_provider_disclosure("ollama")
    assert "Local provider 'ollama' selected" in disc_local
    assert "without remote cloud transmission" in disc_local

    disc_remote = format_provider_disclosure("deepseek", model="deepseek-flash")
    assert "Remote provider 'deepseek' selected" in disc_remote
    assert "deepseek-flash" in disc_remote
    assert "transmitted to the deepseek API" in disc_remote


def test_resolve_unsupported_provider() -> None:
    with pytest.raises(ProviderConfigurationError, match="Unsupported provider: 'unknown-vendor'"):
        resolve_provider_adapter("unknown-vendor")


def test_resolve_none_and_deterministic() -> None:
    assert resolve_provider_adapter("none") is None
    assert resolve_provider_adapter("model-free") is None
    det = resolve_provider_adapter("deterministic")
    assert det is not None


# ---------------------------------------------------------------------------
# 3. Environment Variable Isolation & Local Keyless Operation
# ---------------------------------------------------------------------------


def test_cloud_providers_missing_credentials_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear all API keys
    for var in [
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "MOONSHOT_API_KEY",
        "XAI_API_KEY",
        "MISTRAL_API_KEY",
        "GROQ_API_KEY",
        "CEREBRAS_API_KEY",
        "TOGETHER_API_KEY",
        "FIREWORKS_API_KEY",
        "DEEPINFRA_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
    ]:
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ProviderConfigurationError, match="DEEPSEEK_API_KEY"):
        resolve_provider_adapter("deepseek")

    with pytest.raises(ProviderConfigurationError, match="MOONSHOT_API_KEY"):
        resolve_provider_adapter("kimi")

    with pytest.raises(ProviderConfigurationError, match="XAI_API_KEY"):
        resolve_provider_adapter("xai")

    with pytest.raises(ProviderConfigurationError, match="ANTHROPIC_API_KEY"):
        resolve_provider_adapter("anthropic")


def test_local_providers_do_not_require_cloud_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear all cloud keys
    for var in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"]:
        monkeypatch.delenv(var, raising=False)

    for prov in ["ollama", "lmstudio", "vllm", "llamacpp"]:
        adapter = resolve_provider_adapter(prov)
        assert isinstance(adapter, OpenAICompatibleReviewerAdapter)
        assert adapter.is_local is True
        assert adapter.api_key is not None
        assert adapter.base_url is not None


def test_custom_openai_compatible_loopback_keyless(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    adapter = resolve_provider_adapter(
        "openai-compatible",
        model="local-model",
        base_url="http://127.0.0.1:5000/v1",
    )
    assert isinstance(adapter, OpenAICompatibleReviewerAdapter)
    assert adapter.api_key == "local"
    assert adapter.base_url == "http://127.0.0.1:5000/v1"


# ---------------------------------------------------------------------------
# 4. OpenAICompatibleReviewerAdapter Hermetic Tests
# ---------------------------------------------------------------------------


def test_openai_compatible_review_success() -> None:
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = SAMPLE_STRUCTURED_JSON
    mock_response.choices = [mock_choice]
    mock_response.usage.prompt_tokens = 250
    mock_response.usage.completion_tokens = 90
    mock_response.usage.total_tokens = 340
    mock_client.chat.completions.create.return_value = mock_response

    adapter = OpenAICompatibleReviewerAdapter(
        config=PROVIDER_CONFIGS["deepseek"],
        api_key="test-deepseek-key",
        model_name="deepseek-flash",
    )
    adapter._client = mock_client

    result = adapter.review("Review diff", system_instruction="Security context")
    assert result.summary == "Detected potential timing leak."
    assert len(result.candidate_findings) == 1
    f = result.candidate_findings[0]
    assert f.file_path == "src/crypto.py"
    assert f.line == 42
    assert f.blocking is True
    assert result.tokens_used == 340
    assert result.prompt_tokens == 250
    assert result.output_tokens == 90


def test_openai_compatible_fallback_when_json_mode_unsupported() -> None:
    mock_client = MagicMock()
    first_call = True

    def side_effect(**kwargs: Any) -> Any:
        nonlocal first_call
        if first_call and "response_format" in kwargs:
            first_call = False
            raise Exception("400 Bad Request: response_format is not supported by this server")
        resp = MagicMock()
        choice = MagicMock()
        choice.message.content = SAMPLE_STRUCTURED_JSON
        resp.choices = [choice]
        resp.usage = None
        return resp

    mock_client.chat.completions.create.side_effect = side_effect

    adapter = OpenAICompatibleReviewerAdapter(
        config=PROVIDER_CONFIGS["ollama"],
        model_name="llama3.1",
    )
    adapter._client = mock_client

    result = adapter.review("Review diff")
    assert result.summary == "Detected potential timing leak."
    assert len(result.candidate_findings) == 1


def test_openai_compatible_reasoning_content_fallback() -> None:
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = ""  # Empty content
    mock_choice.message.reasoning_content = SAMPLE_STRUCTURED_JSON
    mock_response.choices = [mock_choice]
    mock_response.usage = None
    mock_client.chat.completions.create.return_value = mock_response

    adapter = OpenAICompatibleReviewerAdapter(
        config=PROVIDER_CONFIGS["deepseek"],
        api_key="test-key",
        model_name="deepseek-r1",
    )
    adapter._client = mock_client

    result = adapter.review("Review diff")
    assert result.summary == "Detected potential timing leak."


def test_openai_compatible_secret_redaction() -> None:
    adapter = OpenAICompatibleReviewerAdapter(
        config=PROVIDER_CONFIGS["openai"],
        api_key="sk-secret1234567890abcdef",
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = RuntimeError(
        "Failed request with token sk-secret1234567890abcdef: connection dropped"
    )
    adapter._client = mock_client

    with pytest.raises(ProviderCallError) as exc_info:
        adapter.review("Review")
    assert "sk-secret1234567890abcdef" not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 5. AnthropicReviewerAdapter Hermetic Tests
# ---------------------------------------------------------------------------


def test_anthropic_review_success() -> None:
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_block = MagicMock()
    mock_block.type = "text"
    mock_block.text = SAMPLE_STRUCTURED_JSON
    mock_response.content = [mock_block]
    mock_response.usage.input_tokens = 300
    mock_response.usage.output_tokens = 120
    mock_client.messages.create.return_value = mock_response

    adapter = AnthropicReviewerAdapter(api_key="ant-key-1234567890")
    adapter._client = mock_client

    result = adapter.review("Review diff")
    assert result.summary == "Detected potential timing leak."
    assert len(result.candidate_findings) == 1
    assert result.tokens_used == 420
    assert result.prompt_tokens == 300
    assert result.output_tokens == 120


def test_anthropic_secret_redaction() -> None:
    adapter = AnthropicReviewerAdapter(api_key="sk-ant-api03-verysecretkey123456")
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError(
        "Authentication failed for key sk-ant-api03-verysecretkey123456"
    )
    adapter._client = mock_client

    with pytest.raises(ProviderCallError) as exc_info:
        adapter.review("Review")
    assert "sk-ant-api03-verysecretkey123456" not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 6. BedrockReviewerAdapter Hermetic Tests
# ---------------------------------------------------------------------------


def test_bedrock_review_success() -> None:
    mock_client = MagicMock()
    mock_client.converse.return_value = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": SAMPLE_STRUCTURED_JSON}],
            }
        },
        "usage": {
            "inputTokens": 280,
            "outputTokens": 95,
            "totalTokens": 375,
        },
    }

    adapter = BedrockReviewerAdapter(model_name="anthropic.claude-3-5-sonnet-20240620-v1:0")
    adapter._client = mock_client

    result = adapter.review("Review diff")
    assert result.summary == "Detected potential timing leak."
    assert len(result.candidate_findings) == 1
    assert result.tokens_used == 375
    assert result.prompt_tokens == 280
    assert result.output_tokens == 95


def test_bedrock_secret_redaction() -> None:
    adapter = BedrockReviewerAdapter()
    mock_client = MagicMock()
    mock_client.converse.side_effect = RuntimeError(
        "AWS Bedrock error with key AKIA1234567890ABCDEF: AccessDeniedException"
    )
    adapter._client = mock_client

    with pytest.raises(ProviderCallError) as exc_info:
        adapter.review("Review")
    assert "AKIA1234567890ABCDEF" not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 7. Response Parser & Malformed Output Tests
# ---------------------------------------------------------------------------


def test_response_parser_markdown_fences() -> None:
    fenced = f"```json\n{SAMPLE_STRUCTURED_JSON}\n```"
    resp = ProviderResponseParser.parse_json_response(fenced)
    assert resp.summary == "Detected potential timing leak."


def test_response_parser_invalid_json() -> None:
    with pytest.raises(ProviderCallError, match="Model returned invalid JSON"):
        ProviderResponseParser.parse_json_response("Not valid JSON at all")


def test_response_parser_malformed_structure() -> None:
    with pytest.raises(ProviderCallError, match="Failed to validate model response structure"):
        ProviderResponseParser.parse_json_response('{"candidate_findings": [{"line": "invalid"}]}')


# ---------------------------------------------------------------------------
# 8. End-to-End ReviewEngine with Generic Provider
# ---------------------------------------------------------------------------


def test_review_engine_with_deepseek_adapter() -> None:
    mock_adapter = MagicMock()
    mock_adapter.model_name = "deepseek-flash"
    mock_adapter.review.return_value = ProviderResponseParser.parse_json_response(
        SAMPLE_STRUCTURED_JSON,
        tokens_used=400,
        prompt_tokens=300,
        output_tokens=100,
    )

    engine = ReviewEngine(provider_adapter=mock_adapter)
    req = ReviewRequest(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        provider="deepseek",
        model="deepseek-flash",
    )

    context_override = GitReviewContext(
        repo_path=".",
        base_revision="main",
        head_revision="HEAD",
        base_commit="abc123456789",
        head_commit="def987654321",
        raw_diff="diff --git a/src/crypto.py b/src/crypto.py\n+ def verify(): pass",
        changed_files={},
        related_files={"src/crypto.py": "def verify():\n    pass\n"},
    )

    report = engine.review(req, context_override=context_override)
    assert report.model_info is not None
    assert report.model_info.provider == "deepseek"
    assert report.model_info.model_name == "deepseek-flash"
    assert report.resource_usage.tokens_used == 400
    assert report.resource_usage.prompt_tokens == 300
    assert report.resource_usage.output_tokens == 100


# ---------------------------------------------------------------------------
# 9. Missing Package & Client Initialization Tests
# ---------------------------------------------------------------------------


def test_anthropic_missing_package() -> None:
    adapter = AnthropicReviewerAdapter(api_key="dummy-key")
    with (
        patch.dict(sys.modules, {"anthropic": None}),
        pytest.raises(ProviderConfigurationError, match="The 'anthropic' package is required"),
    ):
        adapter._get_client()


def test_anthropic_client_init_error_redacts() -> None:
    adapter = AnthropicReviewerAdapter(api_key="sk-ant-secret123456789")
    mock_anthropic = MagicMock()
    mock_anthropic.Anthropic.side_effect = ValueError("Failed with key sk-ant-secret123456789")
    with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
        with pytest.raises(ProviderConfigurationError) as exc_info:
            adapter._get_client()
        assert "sk-ant-secret123456789" not in str(exc_info.value)
        assert "[REDACTED]" in str(exc_info.value)


def test_anthropic_timeout_error() -> None:
    adapter = AnthropicReviewerAdapter(api_key="dummy-key")
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("Connection timed out after 30s")
    adapter._client = mock_client
    with pytest.raises(ProviderCallError, match="timed out after"):
        adapter.review("Prompt", timeout_seconds=30.0)


def test_bedrock_missing_package() -> None:
    adapter = BedrockReviewerAdapter()
    with (
        patch.dict(sys.modules, {"boto3": None}),
        pytest.raises(ProviderConfigurationError, match="The 'boto3' package is required"),
    ):
        adapter._get_client()


def test_bedrock_client_init_error_redacts() -> None:
    adapter = BedrockReviewerAdapter()
    mock_boto3 = MagicMock()
    mock_boto3.Session.side_effect = ValueError("Failed with AKIA1234567890ABCDEF")
    with patch.dict(sys.modules, {"boto3": mock_boto3, "botocore.config": MagicMock()}):
        with pytest.raises(ProviderConfigurationError) as exc_info:
            adapter._get_client()
        assert "AKIA1234567890ABCDEF" not in str(exc_info.value)
        assert "[REDACTED]" in str(exc_info.value)


def test_bedrock_timeout_error() -> None:
    adapter = BedrockReviewerAdapter()
    mock_client = MagicMock()
    mock_client.converse.side_effect = RuntimeError("Read timed out on socket")
    adapter._client = mock_client
    with pytest.raises(ProviderCallError, match="timed out after"):
        adapter.review("Prompt", timeout_seconds=45.0)


def test_openai_missing_package() -> None:
    adapter = OpenAICompatibleReviewerAdapter(
        config=PROVIDER_CONFIGS["ollama"],
        model_name="llama3.1",
    )
    with (
        patch.dict(sys.modules, {"openai": None}),
        pytest.raises(ProviderConfigurationError, match="The 'openai' package is required"),
    ):
        adapter._get_client()


def test_openai_client_init_error_redacts() -> None:
    adapter = OpenAICompatibleReviewerAdapter(
        config=PROVIDER_CONFIGS["openai"],
        api_key="sk-secret1234567890abcdef",
    )
    mock_openai = MagicMock()
    mock_openai.OpenAI.side_effect = ValueError("Failed with token sk-secret1234567890abcdef")
    with patch.dict(sys.modules, {"openai": mock_openai}):
        with pytest.raises(ProviderConfigurationError) as exc_info:
            adapter._get_client()
        assert "sk-secret1234567890abcdef" not in str(exc_info.value)
        assert "[REDACTED]" in str(exc_info.value)


def test_openai_timeout_error() -> None:
    adapter = OpenAICompatibleReviewerAdapter(
        config=PROVIDER_CONFIGS["deepseek"],
        api_key="test-key",
        model_name="deepseek-flash",
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = RuntimeError("Request timed out")
    adapter._client = mock_client
    with pytest.raises(ProviderCallError, match="timed out after"):
        adapter.review("Prompt", timeout_seconds=30.0)


def test_openai_generic_api_call_failure() -> None:
    adapter = OpenAICompatibleReviewerAdapter(
        config=PROVIDER_CONFIGS["deepseek"],
        api_key="test-key",
        model_name="deepseek-flash",
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = RuntimeError("503 Service Unavailable")
    adapter._client = mock_client
    with pytest.raises(ProviderCallError, match="deepseek API call failed"):
        adapter.review("Prompt")


# ---------------------------------------------------------------------------
# 10. CLI Universal Provider Invocations
# ---------------------------------------------------------------------------


def test_cli_rejects_insecure_remote_http(capsys: pytest.CaptureFixture[str]) -> None:
    from verifycraft.cli import main

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "review",
                ".",
                "--provider",
                "openai-compatible",
                "--model",
                "remote-model",
                "--base-url",
                "http://remote-insecure.com/v1",
            ]
        )
    assert exc.value.code == 1
    _, err = capsys.readouterr()
    assert "Insecure plain HTTP endpoint" in err


def test_cli_allows_insecure_remote_http_with_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from verifycraft.cli import main

    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key")
    with patch("verifycraft.cli.ReviewEngine") as mock_engine_cls:
        mock_instance = MagicMock()
        mock_report = MagicMock()
        mock_report.has_blocking_issues = False
        mock_report.to_markdown.return_value = "# Report"
        mock_instance.review.return_value = mock_report
        mock_engine_cls.return_value = mock_instance

        with pytest.raises(SystemExit) as exc:
            main(
                [
                    "review",
                    ".",
                    "--provider",
                    "openai-compatible",
                    "--model",
                    "remote-model",
                    "--base-url",
                    "http://remote-internal.com/v1",
                    "--allow-insecure-http",
                ]
            )
        assert exc.value.code == 0
        _, err = capsys.readouterr()
        assert "Remote provider 'openai-compatible' selected" in err


# ---------------------------------------------------------------------------
# 11. Edge Cases & Branch Coverage Boost
# ---------------------------------------------------------------------------


def test_is_loopback_url_edge_cases() -> None:
    assert is_loopback_url("http://127.0.0.2:8000") is True
    assert is_loopback_url("http://0.0.0.0:8000") is True
    assert is_loopback_url("http://192.168.1.1:8000") is False
    assert is_loopback_url("http://[:::1]:8000") is False


def test_parse_json_response_token_calculation_and_fences() -> None:
    # Test token calculation when tokens_used is None
    res = ProviderResponseParser.parse_json_response(
        SAMPLE_STRUCTURED_JSON,
        tokens_used=None,
        prompt_tokens=15,
        output_tokens=25,
    )
    assert res.tokens_used == 40

    # Test fence stripping with single fence or trailing newline
    fence_json = "```json\n" + SAMPLE_STRUCTURED_JSON + "\n```"
    res2 = ProviderResponseParser.parse_json_response(fence_json)
    assert res2.summary == "Detected potential timing leak."

    # Test invalid json non-dict filtering
    weird_json = (
        '{"summary": "ok", "candidate_findings": ["not-a-dict"], "requirement_assessments": [42]}'
    )
    res3 = ProviderResponseParser.parse_json_response(weird_json)
    assert res3.summary == "ok"
    assert len(res3.candidate_findings) == 0
    assert len(res3.requirement_assessments) == 0


def test_openai_compatible_missing_model_and_supports_json_mode_false() -> None:
    with pytest.raises(ProviderConfigurationError, match="Model identifier is required"):
        OpenAICompatibleReviewerAdapter(model_name=None, config=None, api_key="dummy")

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = SAMPLE_STRUCTURED_JSON
    mock_resp.choices = [mock_choice]
    mock_resp.usage = None
    mock_client.chat.completions.create.return_value = mock_resp

    adapter = OpenAICompatibleReviewerAdapter(
        config=PROVIDER_CONFIGS["deepseek"],
        api_key="test-key",
        model_name="deepseek-flash",
        supports_json_mode=False,
    )
    adapter._client = mock_client
    adapter.review("Prompt")
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert "response_format" not in call_kwargs


def test_openai_compatible_empty_choices() -> None:
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = []
    mock_resp.usage = None
    mock_client.chat.completions.create.return_value = mock_resp

    adapter = OpenAICompatibleReviewerAdapter(
        config=PROVIDER_CONFIGS["deepseek"],
        api_key="test-key",
        model_name="deepseek-flash",
    )
    adapter._client = mock_client
    with pytest.raises(ProviderCallError, match="Model returned invalid JSON"):
        adapter.review("Prompt")


def test_openai_compatible_fallback_double_failure() -> None:
    mock_client = MagicMock()
    # First call with response_format fails, second fallback call without response_format ALSO fails
    mock_client.chat.completions.create.side_effect = [
        Exception("response_format not supported"),
        RuntimeError("Fallback network failure"),
    ]

    adapter = OpenAICompatibleReviewerAdapter(
        config=PROVIDER_CONFIGS["deepseek"],
        api_key="test-key",
        model_name="deepseek-flash",
    )
    adapter._client = mock_client
    with pytest.raises(ProviderCallError, match="Fallback network failure"):
        adapter.review("Prompt")


def test_openai_compatible_client_initialization() -> None:
    adapter = OpenAICompatibleReviewerAdapter(
        config=PROVIDER_CONFIGS["deepseek"],
        api_key="test-key",
        model_name="deepseek-flash",
    )
    with patch("openai.OpenAI") as mock_openai_cls:
        mock_instance = MagicMock()
        mock_openai_cls.return_value = mock_instance
        client = adapter._get_client()
        assert client == mock_instance

    adapter_err = OpenAICompatibleReviewerAdapter(
        config=PROVIDER_CONFIGS["deepseek"],
        api_key="test-key",
        model_name="deepseek-flash",
    )
    with (
        patch("openai.OpenAI", side_effect=ValueError("Bad init")),
        pytest.raises(ProviderConfigurationError, match="Failed to initialize OpenAI client"),
    ):
        adapter_err._get_client()


def test_anthropic_non_text_blocks_and_token_accounting() -> None:
    mock_client = MagicMock()
    mock_resp = MagicMock()
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = SAMPLE_STRUCTURED_JSON
    mock_resp.content = [tool_block, text_block]
    mock_resp.usage = None
    mock_client.messages.create.return_value = mock_resp

    adapter = AnthropicReviewerAdapter(api_key="test-key", model_name="claude-3-5-sonnet")
    adapter._client = mock_client
    res = adapter.review("Prompt")
    assert res.summary == "Detected potential timing leak."
    assert res.tokens_used is None


def test_anthropic_client_initialization_failure() -> None:
    adapter = AnthropicReviewerAdapter(api_key="test-key", model_name="claude-3-5-sonnet")
    with (
        patch("anthropic.Anthropic", side_effect=Exception("Anthropic init error")),
        pytest.raises(ProviderConfigurationError, match="Failed to initialize Anthropic client"),
    ):
        adapter._get_client()


def test_bedrock_edge_cases_tokens_and_blocks() -> None:
    mock_client = MagicMock()
    mock_client.converse.return_value = {
        "output": {
            "message": {
                "content": [
                    {"image": {"source": "data"}},  # non-text block
                    {"text": SAMPLE_STRUCTURED_JSON},
                ]
            }
        },
        "usage": {
            "inputTokens": 30,
            "outputTokens": 20,
            # totalTokens omitted to test calculation
        },
    }

    adapter = BedrockReviewerAdapter(model_name="anthropic.claude-v2")
    adapter._client = mock_client
    res = adapter.review("Prompt")
    assert res.summary == "Detected potential timing leak."
    assert res.tokens_used == 50
    assert res.prompt_tokens == 30
    assert res.output_tokens == 20


def test_bedrock_client_initialization_failure() -> None:
    adapter = BedrockReviewerAdapter(model_name="anthropic.claude-v2")
    with (
        patch("boto3.Session", side_effect=Exception("Boto3 session error")),
        pytest.raises(ProviderConfigurationError, match="Failed to initialize AWS Bedrock client"),
    ):
        adapter._get_client()


def test_registry_bedrock_and_unknown_type() -> None:
    adapter = resolve_provider_adapter("bedrock")
    assert isinstance(adapter, BedrockReviewerAdapter)

    from verifycraft.review.providers.base import ProviderConfig

    dummy_cfg = ProviderConfig(
        provider_name="weird",
        provider_type="unsupported_type",
        model="m",
        api_key_env_var=None,
    )
    with (
        patch.dict(PROVIDER_CONFIGS, {"weird": dummy_cfg}),
        pytest.raises(ProviderConfigurationError, match="Unknown provider type"),
    ):
        resolve_provider_adapter("weird")


def test_cli_resolve_review_provider_helper(capsys: pytest.CaptureFixture[str]) -> None:
    from verifycraft.cli import _resolve_review_provider

    assert _resolve_review_provider("deepseek", False) == "deepseek"
    assert _resolve_review_provider(None, True) == "none"

    with pytest.raises(SystemExit) as exc:
        _resolve_review_provider(None, False)
    assert exc.value.code == 1
    _, err = capsys.readouterr()
    assert "Explicit provider selection is required" in err


def test_openai_compatible_custom_without_config() -> None:
    adapter = OpenAICompatibleReviewerAdapter(
        model_name="custom-model",
        base_url="https://custom.endpoint.com/v1",
        api_key="custom-key",
    )
    assert adapter.supports_json_mode is True
    assert adapter.is_local is False


def test_anthropic_client_initialization_success_and_partial_usage() -> None:
    adapter = AnthropicReviewerAdapter(api_key="test-key", model_name="claude-3-5-sonnet")
    with patch("anthropic.Anthropic") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        client = adapter._get_client()
        assert client == mock_instance

    # Test partial usage where output_tokens is None
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(type="text", text=SAMPLE_STRUCTURED_JSON)]
    mock_resp.usage = MagicMock(input_tokens=100, output_tokens=None)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_resp
    adapter._client = mock_client
    res = adapter.review("Prompt")
    assert res.tokens_used is None
    assert res.prompt_tokens == 100


def test_bedrock_client_initialization_success() -> None:
    with patch("boto3.Session") as mock_sess_cls:
        mock_sess = MagicMock()
        mock_sess_cls.return_value = mock_sess
        mock_sess.client.return_value = MagicMock()
        adapter = BedrockReviewerAdapter(model_name="anthropic.claude-v2")
        assert adapter._get_client() is not None


def test_registry_gemini_resolution() -> None:
    adapter = resolve_provider_adapter("gemini", api_key="dummy-gemini-key")
    assert adapter is not None


def test_strip_markdown_fences_without_closing_fence() -> None:
    fence_no_end = "```json\n" + SAMPLE_STRUCTURED_JSON
    res = ProviderResponseParser.parse_json_response(fence_no_end)
    assert res.summary == "Detected potential timing leak."


def test_load_dotenv_if_present(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from verifycraft.review.providers.base import load_dotenv_if_present

    monkeypatch.delenv("TEST_VC_KEY", raising=False)
    load_dotenv_if_present("non_existent_file.env")
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_VC_KEY=my-secret-val\n# comment\nINVALID_LINE\n")
    load_dotenv_if_present(env_file)
    assert os.environ.get("TEST_VC_KEY") == "my-secret-val"
    monkeypatch.delenv("TEST_VC_KEY", raising=False)


def test_openai_api_key_not_leaked_to_deepseek(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-only")
    with pytest.raises(ProviderConfigurationError, match="DEEPSEEK_API_KEY"):
        OpenAICompatibleReviewerAdapter(
            config=PROVIDER_CONFIGS["deepseek"],
            model_name="deepseek-flash",
        )
