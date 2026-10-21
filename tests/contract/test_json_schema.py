"""Contract tests ensuring JSON schema stability and specification adherence."""

from verifycraft.contracts import (
    Evidence,
    VerificationResult,
)


def test_verification_result_schema_fields() -> None:
    schema = VerificationResult.model_json_schema()

    assert "schema_version" in schema["properties"]
    assert "verdict" in schema["properties"]
    assert "findings" in schema["properties"]
    assert "evidence" in schema["properties"]
    assert "resource_usage" in schema["properties"]
    assert "fingerprint" in schema["properties"]
    assert "created_at" in schema["properties"]

    # Verify verdict enum values in schema
    verdict_schema = schema["$defs"]["Verdict"]
    enum_values = verdict_schema["enum"]
    assert sorted(enum_values) == ["blocked", "inconclusive", "refuted", "supported"]


def test_evidence_schema_fields() -> None:
    schema = Evidence.model_json_schema()
    required = schema["required"]
    assert "evidence_id" in required
    assert "check_id" in required
    assert "evidence_type" in required
    assert "exit_code" in required
    assert "fingerprint" in required
