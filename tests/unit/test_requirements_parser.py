"""Unit tests for RequirementsParser."""

from pathlib import Path

import pytest

from verifycraft.review.requirements import RequirementsParser

SAMPLE_DOC = """# System Requirements

## Authentication
- **[REQ-AUTH-01]**: Access tokens must expire after 15 minutes of inactivity.
- **REQ-AUTH-02**: Passwords must contain at least 12 characters.

## Data Processing
Requirement REQ-DATA-01: Exported records shall be serialized to JSON.
All incoming HTTP requests must include a valid CSRF token.
"""


def test_requirements_parser_text() -> None:
    parser = RequirementsParser()
    reqs = parser.parse_text(SAMPLE_DOC, source_file="docs/auth.md")

    assert len(reqs) == 4
    assert reqs[0].id == "REQ-AUTH-01"
    assert "expire after 15 minutes" in reqs[0].text
    assert reqs[0].source_file == "docs/auth.md"

    assert reqs[1].id == "REQ-AUTH-02"
    assert "at least 12 characters" in reqs[1].text

    assert reqs[2].id == "REQ-DATA-01"
    assert "serialized to JSON" in reqs[2].text

    # Auto-assigned ID for sentence with 'must'
    assert reqs[3].id == "REQ-01"
    assert "CSRF token" in reqs[3].text


def test_requirements_parser_file(tmp_path: Path) -> None:
    req_file = tmp_path / "spec.md"
    req_file.write_text("- **[REQ-01]**: Rate limiting must be enforced.", encoding="utf-8")

    parser = RequirementsParser()
    reqs = parser.parse_file(str(req_file), repo_root=str(tmp_path))
    assert len(reqs) == 1
    assert reqs[0].id == "REQ-01"


def test_requirements_parser_missing_file() -> None:
    parser = RequirementsParser()
    with pytest.raises(FileNotFoundError):
        parser.parse_file("nonexistent_reqs.md")
