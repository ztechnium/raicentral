"""Shared parser contracts. Stdlib only: this package runs in a sandboxed child."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from typing import Any

MiB = 1024 * 1024

# Source outcomes (what happened to a source).
COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
NEEDS_OCR = "NEEDS_OCR"
NEEDS_INPUT = "NEEDS_INPUT"
UNSUPPORTED = "UNSUPPORTED"
FAILED = "FAILED"
NOT_PROCESSED = "NOT_PROCESSED"  # run-only; never cached on an extraction
EXTRACTION_OUTCOMES = (COMPLETE, PARTIAL, NEEDS_OCR, NEEDS_INPUT, UNSUPPORTED, FAILED)
ATTENTION_OUTCOMES = frozenset(
    {PARTIAL, NEEDS_OCR, NEEDS_INPUT, UNSUPPORTED, FAILED, NOT_PROCESSED}
)

ALLOWED_ENCODINGS = ("utf-8", "windows-1256")
ALLOWED_DELIMITERS = ("auto", ",", ";", "\t", "|")
ALLOWED_HEADER_MODES = ("preserve_all", "first_row_header")
TEXT_FAMILIES = frozenset({"CSV", "TXT", "MARKDOWN"})

PARSER_BUILD_VERSION = "onb-parsers-1"
EXTRACTION_SCHEMA_VERSION = 1
NORMALIZATION_VERSION = 1


class OptionsError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    wall_seconds: float = 120.0
    memory_bytes: int = 512 * MiB
    pdf_pages: int = 300
    zip_entries: int = 10_000
    zip_total_bytes: int = 100 * MiB
    zip_member_bytes: int = 25 * MiB
    zip_ratio: int = 100
    xlsx_sheets: int = 50
    max_rows: int = 10_000
    max_columns: int = 200
    cells_visited: int = 200_000
    nonempty_cells: int = 50_000
    output_bytes: int = 16 * MiB
    run_output_bytes: int = 64 * MiB

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Limits":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})

    def digest(self) -> str:
        return digest_of(self.to_dict())


def digest_of(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def validate_options(
    kind: str, family: str, options: dict[str, Any] | None
) -> dict[str, Any]:
    """Validate user overrides for one source. Returns the normalized override dict.

    Only bounded text/CSV options exist. TEXT sources (prompts, pasted text) are
    always exact UTF-8 and accept no options at all.
    """
    options = dict(options or {})
    if not options:
        return {}
    if kind != "FILE" or family not in TEXT_FAMILIES:
        raise OptionsError(
            "Parsing options apply only to uploaded text, Markdown and CSV files"
        )
    unknown = set(options) - {"encoding", "delimiter", "header_mode"}
    if unknown:
        raise OptionsError("Unsupported parsing option")
    out: dict[str, Any] = {}
    if "encoding" in options:
        if options["encoding"] not in ALLOWED_ENCODINGS:
            raise OptionsError("Unsupported encoding")
        out["encoding"] = options["encoding"]
    if "delimiter" in options:
        if family != "CSV":
            raise OptionsError("Delimiter applies only to CSV files")
        if options["delimiter"] not in ALLOWED_DELIMITERS:
            raise OptionsError("Unsupported delimiter")
        out["delimiter"] = options["delimiter"]
    if "header_mode" in options:
        if family != "CSV":
            raise OptionsError("Header mode applies only to CSV files")
        if options["header_mode"] not in ALLOWED_HEADER_MODES:
            raise OptionsError("Unsupported header mode")
        out["header_mode"] = options["header_mode"]
    return out


def effective_options(
    family: str, kind: str, overrides: dict[str, Any] | None
) -> dict[str, Any]:
    """Defaults plus overrides: this whole dict is part of the extraction identity."""
    eff: dict[str, Any] = {"parser_mode": "TEXT_ONLY"}
    overrides = overrides or {}
    if kind == "TEXT" or family in TEXT_FAMILIES:
        eff["encoding"] = (
            "utf-8" if kind == "TEXT" else overrides.get("encoding", "utf-8")
        )
        eff["newlines"] = "LF"
    if family == "CSV" and kind == "FILE":
        eff["delimiter"] = overrides.get("delimiter", "auto")
        eff["header_mode"] = overrides.get("header_mode", "preserve_all")
    if family == "XLSX":
        eff["hidden_sheets"] = "INCLUDE"
        eff["formulas"] = "TEXT_AND_CACHED_VALUE"
    if family == "DOCX":
        eff["story_parts"] = "BODY_HEADERS_FOOTERS"
    return eff


def block(
    kind: str,
    text: str,
    locator: dict[str, Any],
    *,
    cell_data: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "text": text,
        "locator": locator,
        "cell_data": cell_data,
        "warnings": warnings or [],
    }


def result(
    outcome: str,
    *,
    reason: str | None = None,
    blocks: list[dict[str, Any]] | None = None,
    unit: str = "document",
    total: int = 1,
    extracted: int = 0,
    gaps: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    normalization: str = "",
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "reason": reason,
        "blocks": blocks or [],
        "coverage": {
            "unit": unit,
            "total": total,
            "extracted": extracted,
            "gaps": (gaps or [])[:50],
        },
        "warnings": sorted(set(warnings or []))[:50],
        "normalization": normalization,
    }


def segment_lines(lines: list[str], *, max_chars: int = 4000, max_lines: int = 200):
    """Yield (start_line, end_line, text) groups, 1-based, nothing lost or repeated."""
    start = 0
    buf: list[str] = []
    size = 0
    for index, line in enumerate(lines):
        # A single enormous line is split into exact slices on its own.
        if len(line) > max_chars:
            if buf:
                yield start + 1, index, "\n".join(buf)
                buf, size = [], 0
            for offset in range(0, len(line), max_chars):
                yield index + 1, index + 1, line[offset : offset + max_chars]
            start = index + 1
            continue
        if buf and (size + len(line) + 1 > max_chars or len(buf) >= max_lines):
            yield start + 1, index, "\n".join(buf)
            buf, size, start = [], 0, index
        buf.append(line)
        size += len(line) + 1
    if buf:
        yield start + 1, start + len(buf), "\n".join(buf)
