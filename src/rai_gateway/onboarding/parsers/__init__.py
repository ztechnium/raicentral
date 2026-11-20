"""Pure parser registry. No provider calls, no domain writes, no settings access."""

from __future__ import annotations

from typing import Any

from .contracts import (
    TEXT_FAMILIES,
    Limits,
    OptionsError,
    effective_options,
    validate_options,
)

__all__ = [
    "Limits",
    "OptionsError",
    "effective_options",
    "parse_file",
    "validate_options",
]


def parse_file(
    path: str, family: str, kind: str, options: dict[str, Any], limits: Limits
) -> dict[str, Any]:
    """Parse one authorized original with frozen effective options."""
    if family == "PDF":
        from .pdf import parse_pdf

        return parse_pdf(path, options, limits)
    if family == "DOCX":
        from .docx_parser import parse_docx

        return parse_docx(path, options, limits)
    if family == "XLSX":
        from .xlsx import parse_xlsx

        return parse_xlsx(path, options, limits)
    if family in TEXT_FAMILIES or family == "TEXT":
        with open(path, "rb") as handle:
            data = handle.read()
        if family == "CSV":
            from .csv_parser import parse_csv

            return parse_csv(data, options, limits)
        from .text import parse_text

        return parse_text(data, options, limits)
    from .contracts import UNSUPPORTED, result

    return result(UNSUPPORTED, reason="UNSUPPORTED_FORMAT")
