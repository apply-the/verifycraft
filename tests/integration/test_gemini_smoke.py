"""Integration smoke test for Google Gemini reviewer adapter.

This test is opt-in and only runs if GEMINI_API_KEY or GOOGLE_API_KEY is present
in the environment. When credentials are not provided, it is skipped automatically.
"""

import os

import pytest

from verifycraft.review.providers.base import ProviderConfigurationError
from verifycraft.review.providers.gemini import GeminiReviewerAdapter


def test_gemini_missing_credentials_raises() -> None:
    """Verifies that missing credentials raise ProviderConfigurationError immediately."""
    # Temporarily hide credentials if set
    old_gemini = os.environ.pop("GEMINI_API_KEY", None)
    old_google = os.environ.pop("GOOGLE_API_KEY", None)
    try:
        with pytest.raises(ProviderConfigurationError) as exc_info:
            GeminiReviewerAdapter(api_key=None)
        assert "requires GEMINI_API_KEY or GOOGLE_API_KEY" in str(exc_info.value)
    finally:
        if old_gemini:
            os.environ["GEMINI_API_KEY"] = old_gemini
        if old_google:
            os.environ["GOOGLE_API_KEY"] = old_google


@pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"),
    reason="Requires GEMINI_API_KEY or GOOGLE_API_KEY in environment to execute live smoke test",
)
def test_gemini_live_smoke() -> None:
    """Live smoke test verifying actual Gemini API call, JSON generation, and parsing."""
    adapter = GeminiReviewerAdapter()
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
    assert response.summary
    assert isinstance(response.candidate_findings, list)
    assert isinstance(response.requirement_assessments, list)
