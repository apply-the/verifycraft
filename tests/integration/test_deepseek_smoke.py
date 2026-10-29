"""Integration smoke test for DeepSeek reviewer adapter.

This test is opt-in and only runs if DEEPSEEK_API_KEY is present in the environment.
When credentials are not provided, it is skipped automatically without failing.
"""

from __future__ import annotations

import os

import pytest

from verifycraft.review.providers.base import (
    ProviderConfigurationError,
    load_dotenv_if_present,
)
from verifycraft.review.providers.registry import resolve_provider_adapter

load_dotenv_if_present()


def test_deepseek_missing_credentials_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that missing credentials raise ProviderConfigurationError immediately."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderConfigurationError) as exc_info:
        resolve_provider_adapter("deepseek")
    assert "DEEPSEEK_API_KEY" in str(exc_info.value)


@pytest.mark.skipif(
    not os.environ.get("DEEPSEEK_API_KEY"),
    reason="Requires DEEPSEEK_API_KEY in environment to execute live DeepSeek smoke test",
)
def test_deepseek_live_smoke() -> None:
    """Live smoke test verifying actual DeepSeek API call, JSON generation, and parsing."""
    adapter = resolve_provider_adapter("deepseek", model="deepseek-chat")
    assert adapter is not None
    sample_prompt = (
        "# Code Review Request\n\n"
        "## Unified Diff\n"
        "```diff\n"
        "--- a/math_ops.py\n"
        "+++ b/math_ops.py\n"
        "@@ -1,3 +1,3 @@\n"
        " def divide(a, b):\n"
        "-    return a / b\n"
        "+    return a / (b + 1)\n"
        "```\n"
    )
    response = adapter.review(sample_prompt, timeout_seconds=30.0)
    assert response.summary and isinstance(response.summary, str)
    assert isinstance(response.candidate_findings, list)
    assert isinstance(response.requirement_assessments, list)
    for finding in response.candidate_findings:
        assert finding.file_path and isinstance(finding.file_path, str)
        assert finding.subject and isinstance(finding.subject, str)
        if finding.line is not None:
            assert isinstance(finding.line, int) and finding.line >= 1
