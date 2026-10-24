"""Requirements parsing and representation for code review."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ParsedRequirement:
    """An explicit parsed requirement item."""

    id: str
    text: str
    source_file: str | None
    line_number: int | None = None


class RequirementsParser:
    """Parses requirements from Markdown or text documents."""

    REQ_ID_PATTERNS = [
        re.compile(r"\[([A-Z0-9_\-]+)\][:\s]+(.*)", re.IGNORECASE),
        re.compile(r"\b([A-Z]{2,}[-_][A-Z0-9_\-]+)\b[:\s]+(.*)", re.IGNORECASE),
        re.compile(r"Requirement\s+([A-Z0-9_\-]+)[:\s]+(.*)", re.IGNORECASE),
        re.compile(r"REQ-(\d+)[:\s]+(.*)", re.IGNORECASE),
    ]

    def parse_text(self, text: str, source_file: str | None = None) -> list[ParsedRequirement]:
        """Parse requirements from raw text."""
        requirements: list[ParsedRequirement] = []
        counter = 1

        lines = text.splitlines()
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("<!--"):
                continue

            is_heading = stripped.startswith("#")

            # Strip leading markdown list markers or headings
            clean = re.sub(r"^(#+\s+|\-\s+|\*\s+|\d+\.\s+)", "", stripped).strip()
            clean = re.sub(r"^\*\*(.*?)\*\*", r"\1", clean).strip()

            matched = False
            for pat in self.REQ_ID_PATTERNS:
                m = pat.search(clean)
                if m:
                    req_id = m.group(1).upper()
                    # Ensure prefix is clean
                    if not req_id.startswith("REQ"):
                        req_id = f"REQ-{req_id}"
                    req_text = m.group(2).strip() or clean
                    requirements.append(
                        ParsedRequirement(
                            id=req_id,
                            text=req_text,
                            source_file=source_file,
                            line_number=idx,
                        )
                    )
                    matched = True
                    break

            if (
                not matched
                and not is_heading
                and re.search(r"\b(must|shall|should|is required to)\b", clean, re.IGNORECASE)
            ):
                # Auto-assign deterministic ID for explicit requirement sentences
                req_id = f"REQ-{counter:02d}"
                requirements.append(
                    ParsedRequirement(
                        id=req_id,
                        text=clean,
                        source_file=source_file,
                        line_number=idx,
                    )
                )
                counter += 1

        return requirements

    def parse_file(self, file_path: str, repo_root: str | None = None) -> list[ParsedRequirement]:
        """Parse requirements from a local file path."""
        target = file_path
        if repo_root and not os.path.isabs(file_path):
            target = os.path.join(repo_root, file_path)

        if not os.path.exists(target):
            raise FileNotFoundError(f"Requirements file not found: {file_path}")

        try:
            with open(target, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            raise OSError(f"Could not read requirements file {file_path}: {e}") from e

        rel_source = file_path
        if repo_root:
            try:
                rel_source = str(Path(target).relative_to(Path(repo_root).resolve()))
            except ValueError:
                rel_source = file_path

        return self.parse_text(content, source_file=rel_source)

    def parse_all(
        self, sources: list[str], repo_root: str | None = None
    ) -> list[ParsedRequirement]:
        """Parse multiple files or inline requirement strings."""
        all_reqs: list[ParsedRequirement] = []
        for src in sources:
            src_clean = src.strip()
            # Check if src is an existing file
            target = src_clean
            if repo_root and not os.path.isabs(src_clean):
                target = os.path.join(repo_root, src_clean)

            if os.path.isfile(target):
                all_reqs.extend(self.parse_file(src_clean, repo_root=repo_root))
            else:
                # Treat as raw requirement text
                all_reqs.extend(self.parse_text(src_clean, source_file="inline"))

        # Deduplicate by ID
        seen: set[str] = set()
        deduped: list[ParsedRequirement] = []
        for req in all_reqs:
            if req.id not in seen:
                seen.add(req.id)
                deduped.append(req)

        return deduped
