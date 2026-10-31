"""Ground-truth adjudication framework for VerifyCraft benchmark evaluations.

This module provides versioned, auditable ground-truth adjudication to distinguish:
1. Known True Positives (matches predefined ground truth expected defect)
2. Adjudicated Additional True Positives (independently verified real defects outside baseline GT)
3. Unsupported Claims (unwarranted assumptions about caller schemas/contracts)
4. Duplicate Findings (redundant reports of an already-credited defect)
5. False Positives (incorrect, harmless, or contradicted claims)

All adjudications record reviewer identity, rationale, evidence, and timestamp.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class AdjudicationDecision(StrEnum):
    """Explicit decision categories for adjudicating review findings."""

    KNOWN_TRUE_POSITIVE = "known_true_positive"
    ADJUDICATED_TRUE_POSITIVE = "adjudicated_true_positive"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    DUPLICATE_FINDING = "duplicate_finding"
    FALSE_POSITIVE = "false_positive"


def compute_finding_fingerprint(
    case_id: str,
    file_path: str,
    line: int | None,
    subject: str,
) -> str:
    """Compute a deterministic, normalized fingerprint for a review finding."""
    norm_subj = re.sub(r"\s+", " ", subject.strip().lower())
    payload = f"{case_id.strip()}::{file_path.strip()}::{line}::{norm_subj}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class AdjudicationRecord:
    """An auditable, permanent record of an adjudicated finding."""

    case_id: str
    file_path: str
    line: int | None
    subject: str
    decision: AdjudicationDecision
    rationale: str
    evidence: str
    reviewer: str
    adjudicated_at: str
    fingerprint: str = ""
    version: str = "1.0.0"

    @property
    def counts_as_false_positive(self) -> bool:
        return self.decision in (
            AdjudicationDecision.UNSUPPORTED_CLAIM,
            AdjudicationDecision.FALSE_POSITIVE,
        )

    @property
    def counts_as_true_positive(self) -> bool:
        return self.decision in (
            AdjudicationDecision.KNOWN_TRUE_POSITIVE,
            AdjudicationDecision.ADJUDICATED_TRUE_POSITIVE,
        )

    def __post_init__(self) -> None:
        if not self.fingerprint:
            self.fingerprint = compute_finding_fingerprint(
                self.case_id, self.file_path, self.line, self.subject
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "fingerprint": self.fingerprint,
            "file_path": self.file_path,
            "line": self.line,
            "subject": self.subject,
            "decision": self.decision.value,
            "rationale": self.rationale,
            "evidence": self.evidence,
            "reviewer": self.reviewer,
            "adjudicated_at": self.adjudicated_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdjudicationRecord:
        return cls(
            case_id=data["case_id"],
            file_path=data.get("file_path", ""),
            line=data.get("line"),
            subject=data.get("subject", ""),
            decision=AdjudicationDecision(data["decision"]),
            rationale=data["rationale"],
            evidence=data.get("evidence", ""),
            reviewer=data.get("reviewer", "evaluator"),
            adjudicated_at=data.get("adjudicated_at", "2026-10-30T00:00:00Z"),
            fingerprint=data.get("fingerprint", ""),
            version=data.get("version", "1.0.0"),
        )


class AdjudicationCatalog:
    """Catalog of auditable ground truth adjudications loaded from versioned storage."""

    def __init__(
        self,
        records: list[AdjudicationRecord] | None = None,
        version: str = "1.0.0",
    ) -> None:
        self.version = version
        self.records: list[AdjudicationRecord] = records or []
        self._by_fingerprint: dict[str, AdjudicationRecord] = {}
        for r in self.records:
            self._register_record(r)

    def _register_record(self, record: AdjudicationRecord) -> None:
        if record.fingerprint:
            self._by_fingerprint[record.fingerprint] = record
        canon_fp = compute_finding_fingerprint(
            record.case_id, record.file_path, record.line, record.subject
        )
        self._by_fingerprint[canon_fp] = record

    def add_record(self, record: AdjudicationRecord) -> None:
        self.records.append(record)
        self._register_record(record)

    def is_compatible_with(self, target_version: str | None) -> bool:
        """Validate semver major version compatibility with target dataset or evaluation."""
        if not target_version:
            return True
        catalog_major = self.version.split(".")[0]
        target_major = str(target_version).split(".")[0]
        return catalog_major == target_major

    def find_adjudication(
        self,
        case_id: str,
        file_path: str,
        line: int | None,
        subject: str,
        target_version: str | None = None,
    ) -> AdjudicationRecord | None:
        """Find an adjudication record matching a finding with strict identity.

        Requires exact case_id, file_path, line coordinate, and fingerprint identity.
        Rejects ambiguous findings or mismatched versions without guessing.
        """
        if target_version and not self.is_compatible_with(target_version):
            return None

        fp = compute_finding_fingerprint(case_id, file_path, line, subject)
        if fp in self._by_fingerprint:
            rec = self._by_fingerprint[fp]
            if rec.case_id == case_id and rec.file_path == file_path and rec.line == line:
                return rec

        candidates = [
            r
            for r in self.records
            if r.case_id == case_id and r.file_path == file_path and r.line == line
        ]
        if not candidates:
            return None

        norm_subject = re.sub(r"\s+", " ", subject.strip().lower())
        matched_candidates = [
            c for c in candidates if re.sub(r"\s+", " ", c.subject.strip().lower()) == norm_subject
        ]
        if len(matched_candidates) == 1:
            return matched_candidates[0]

        # Ambiguous or non-matching subject on this coordinate: reject without guessing
        return None

    @classmethod
    def load(cls, path: Path | str) -> AdjudicationCatalog:
        p = Path(path)
        if not p.exists():
            return cls([])
        raw = json.loads(p.read_text(encoding="utf-8"))
        version = raw.get("version", "1.0.0") if isinstance(raw, dict) else "1.0.0"
        records_data = raw.get("adjudications", raw) if isinstance(raw, dict) else raw
        records = [AdjudicationRecord.from_dict(d) for d in records_data]
        return cls(records, version=version)

    @classmethod
    def from_json_file(cls, path: Path | str) -> AdjudicationCatalog:
        return cls.load(path)

    def save(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": "1.0.0",
            "adjudications": [r.to_dict() for r in self.records],
        }
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
