"""Tests for PR #10 review quality, defect reproductions, and quality gates."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from verifycraft.contracts.review import (
    ConventionalLabel,
    EvidenceStatus,
    FindingSeverity,
    ReviewFinding,
)
from verifycraft.review.engine import ReviewEngine
from verifycraft.review.git import ChangedFile, DiffHunk, GitContextCollector
from verifycraft.review.providers.anthropic import AnthropicReviewerAdapter
from verifycraft.review.providers.base import (
    ProviderCallError,
    ProviderConfigurationError,
    ProviderResponseParser,
    is_loopback_url,
    load_dotenv_if_present,
    validate_endpoint_url,
)
from verifycraft.review.providers.openai_compatible import OpenAICompatibleReviewerAdapter
from verifycraft.review.providers.registry import (
    PROVIDER_CONFIGS,
    is_local_provider,
    resolve_provider_adapter,
)

# ==============================================================================
# 1. Defect Reproductions: Networking & Endpoint Validation
# ==============================================================================


def test_unspecified_addresses_are_not_loopback() -> None:
    """0.0.0.0 and :: (unspecified) are bind wildcards, NOT secure loopback interfaces."""
    assert not is_loopback_url("http://0.0.0.0:8000")
    assert not is_loopback_url("http://0.0.0.0")
    assert not is_loopback_url("http://[::]:8000")
    assert not is_loopback_url("http://[0:0:0:0:0:0:0:0]:8000")

    # Legitimate loopback addresses must still pass
    assert is_loopback_url("http://localhost:8000")
    assert is_loopback_url("http://127.0.0.1:8000")
    assert is_loopback_url("http://127.0.0.2:8000")
    assert is_loopback_url("http://[::1]:8000")

    # Plain HTTP to 0.0.0.0 must fail without allow_insecure_http
    with pytest.raises(ProviderConfigurationError) as exc_info:
        validate_endpoint_url("http://0.0.0.0:8000", allow_insecure_http=False)
    assert "rejected" in str(exc_info.value).lower()

    # Plain HTTP to 0.0.0.0 succeeds when explicitly permitted
    validate_endpoint_url("http://0.0.0.0:8000", allow_insecure_http=True)


def test_anthropic_custom_base_url_scheme_validation() -> None:
    """Anthropic adapter must validate custom base_url and reject plain HTTP to non-loopback."""
    # Plain HTTP to remote endpoint must raise ProviderConfigurationError
    with pytest.raises(ProviderConfigurationError) as exc_info:
        AnthropicReviewerAdapter(
            api_key="test-key",
            base_url="http://insecure-remote-anthropic-proxy.corp:8080",
            allow_insecure_http=False,
        )
    assert "insecure plain http endpoint" in str(exc_info.value).lower()

    # When allow_insecure_http is True, it is permitted
    adapter = AnthropicReviewerAdapter(
        api_key="test-key",
        base_url="http://insecure-remote-anthropic-proxy.corp:8080",
        allow_insecure_http=True,
    )
    assert adapter.base_url == "http://insecure-remote-anthropic-proxy.corp:8080"

    # resolve_provider_adapter must forward allow_insecure_http to Anthropic
    adapter_res = resolve_provider_adapter(
        "anthropic",
        api_key="test-key",
        base_url="http://insecure-remote-anthropic-proxy.corp:8080",
        allow_insecure_http=True,
    )
    assert adapter_res is not None


def test_openai_compatible_provider_locality_invariant() -> None:
    """openai-compatible must not be marked unconditionally as local."""
    assert not PROVIDER_CONFIGS["openai-compatible"].is_local
    assert is_local_provider("openai-compatible", base_url="http://127.0.0.1:8000")
    assert not is_local_provider("openai-compatible", base_url="https://api.external.com/v1")


# ==============================================================================
# 2. Defect Reproductions: .env Loading Invariant
# ==============================================================================


def test_dotenv_loading_is_opt_in_and_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Automatic unconstrained .env loading from CWD is unsafe and must be opt-in."""
    monkeypatch.delenv("INJECTED_TEST_VAR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("INJECTED_TEST_VAR=malicious_val\nINVALID_SYNTAX\n", encoding="utf-8")

    # By default, without opt_in or target, it does nothing if opt_in=False
    load_dotenv_if_present(env_path=env_file, opt_in=False)
    assert "INJECTED_TEST_VAR" not in os.environ

    # With opt_in=True, it loads allowed variables safely
    load_dotenv_if_present(env_path=env_file, opt_in=True)
    assert os.environ.get("INJECTED_TEST_VAR") == "malicious_val"


# ==============================================================================
# 3. Defect Reproductions: Provider Retries & Parsing
# ==============================================================================


def test_openai_compatible_json_retry_only_on_unsupported_format() -> None:
    """Only retry when response_format is genuinely unsupported, not on auth or rate limits."""
    adapter = OpenAICompatibleReviewerAdapter(
        model_name="test-model",
        base_url="https://api.example.com/v1",
        api_key="sk-test",
    )

    mock_client = MagicMock()
    # Simulate a 401 Unauthorized whose message happens to mention response_format
    auth_error = Exception("401 Unauthorized: invalid api key for response_format json_object")
    mock_client.chat.completions.create.side_effect = auth_error

    with pytest.raises(ProviderCallError) as exc_info:
        adapter._call_completion(
            mock_client,
            {"model": "m", "response_format": {"type": "json_object"}},
            10.0,
        )

    # It must NOT retry completion call with fallback
    assert mock_client.chat.completions.create.call_count == 1
    assert "401 unauthorized" in str(exc_info.value).lower()


def test_openai_compatible_empty_content_handling() -> None:
    """Empty response or reasoning_content without JSON must raise clear ProviderCallError."""
    # When text is empty
    with pytest.raises(ProviderCallError) as exc_info:
        ProviderResponseParser.parse_json_response("   \n  ")
    assert "empty response" in str(exc_info.value).lower()

    # When text is chain-of-thought prose rather than JSON
    with pytest.raises(ProviderCallError) as exc_info2:
        ProviderResponseParser.parse_json_response(
            "I think the user wants to check loopback addresses."
        )
    assert "invalid json" in str(exc_info2.value).lower()


# ==============================================================================
# 4. Semantic Anchor Grounding & Verification
# ==============================================================================


def test_anchor_validation_remaps_or_downgrades() -> None:
    """Anchor validation must semantically ground to cited symbols/code or downgrade."""
    collector = GitContextCollector(".")
    source_content = (
        "def helper():\n"
        "    pass\n"
        "\n"
        "LOOPBACK_HOSTS = frozenset({'localhost', '127.0.0.1'})\n"
        "\n"
        "def is_loopback(host):\n"
        "    return host in LOOPBACK_HOSTS\n"
        "\n"
        "def some_other_func():\n"
        "    # line 10\n"
        "    return 42\n"
    )

    cfile = ChangedFile(
        path="src/network.py",
        status="modified",
        changed_lines={4, 5, 6, 7, 8, 9, 10},
        hunks=[DiffHunk(old_start=1, old_lines=10, new_start=1, new_lines=10, content="...")],
        content=source_content,
    )
    changed_files = {"src/network.py": cfile}

    # Case A: Model cited line 10, but finding is about 'LOOPBACK_HOSTS' (which is at line 4)
    line, reason = collector.validate_semantic_anchor(
        file_path="src/network.py",
        line=10,
        subject="LOOPBACK_HOSTS contains invalid entries",
        description="Check LOOPBACK_HOSTS initialization",
        changed_files=changed_files,
    )
    assert line == 4  # Remapped to actual definition of cited symbol!
    assert reason is None or "remapped" in reason.lower()

    # Case B: Model cited an arbitrary line and completely unanchored concept
    line_unanchored, reason_unanchored = collector.validate_semantic_anchor(
        file_path="src/network.py",
        line=10,
        subject="Non-existent database connection failure",
        description="Missing pooling on postgres engine",
        changed_files=changed_files,
    )
    # Cannot verify against source code or symbol; must downgrade to file-level
    assert line_unanchored is None
    assert reason_unanchored is not None
    assert "unanchored" in reason_unanchored.lower() or "downgraded" in reason_unanchored.lower()


# ==============================================================================
# 5. Completeness & Bounded Context Acquisition
# ==============================================================================


def test_context_acquisition_preserves_hunk_boundaries() -> None:
    """Context acquisition must never split a diff hunk in half."""
    diff_text = (
        "diff --git a/file1.py b/file1.py\n"
        "--- a/file1.py\n"
        "+++ b/file1.py\n"
        "@@ -1,5 +1,5 @@\n"
        "+line1\n"
        "+line2\n"
        "+line3\n"
        "+line4\n"
        "+line5\n"
        "diff --git a/file2.py b/file2.py\n"
        "--- a/file2.py\n"
        "+++ b/file2.py\n"
        "@@ -1,5 +1,5 @@\n"
        "+file2_line1\n"
        "+file2_line2\n"
        "+file2_line3\n"
        "+file2_line4\n"
        "+file2_line5\n"
    )

    collector = GitContextCollector(".")
    omitted_scope: list[str] = []
    # Budget of 9 lines: file1 has 9 lines. file2 hunk cannot fit entirely.
    res_diff = collector._bounded_diff_acquisition(
        diff_text=diff_text,
        max_diff_lines=9,
        omitted_scope=omitted_scope,
    )

    # Must contain file1 completely, and NOT cut file2's hunk in half
    assert "file1.py" in res_diff
    assert "file2_line3" not in res_diff  # file2 wasn't cut in half
    assert any("file2.py" in msg for msg in omitted_scope)


# ==============================================================================
# 6. Finding Deduplication Across Labels
# ==============================================================================


def test_semantic_deduplication_across_labels() -> None:
    """Deduplicate findings that describe the same core defect under different labels."""
    engine = ReviewEngine()
    findings = [
        ReviewFinding(
            id="rev-001",
            file_path="src/config.py",
            line=15,
            label=ConventionalLabel.ISSUE,
            subject="load_dotenv silently mutates environment",
            description="The function mutates os.environ directly.",
            severity=FindingSeverity.HIGH,
            evidence_status=EvidenceStatus.MODEL_INFERRED,
        ),
        ReviewFinding(
            id="rev-002",
            file_path="src/config.py",
            line=15,
            label=ConventionalLabel.QUESTION,
            subject="Should load_dotenv be opt-in rather than automatic?",
            description="Is automatic loading intended or should it be gated?",
            severity=FindingSeverity.MEDIUM,
            evidence_status=EvidenceStatus.MODEL_INFERRED,
        ),
    ]

    deduped = engine._deduplicate_findings(findings)
    assert len(deduped) == 1
    # Preserved the higher-severity / actionable issue
    assert deduped[0].label == ConventionalLabel.ISSUE
