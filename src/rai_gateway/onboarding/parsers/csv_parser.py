"""CSV via the stdlib reader: every record preserved, values stay strings."""

from __future__ import annotations

import csv
import io
from typing import Any

from .contracts import COMPLETE, NEEDS_INPUT, PARTIAL, block, result
from .text import NORMALIZATION, decode_strict

_CANDIDATES = (",", ";", "\t", "|")


def _sniff(sample: str) -> tuple[str | None, bool]:
    """Return (delimiter, ambiguous). Only a single clear winner is accepted."""
    lines = [ln for ln in sample.split("\n")[:20] if ln.strip()]
    if not lines:
        return ",", False
    plausible = []
    for cand in _CANDIDATES:
        try:
            rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=cand))
        except csv.Error:
            continue
        widths = {len(r) for r in rows if r}
        if len(widths) == 1 and next(iter(widths)) > 1:
            plausible.append(cand)
    if len(plausible) == 1:
        return plausible[0], False
    if len(plausible) > 1:
        return None, True
    return ",", False  # no delimiter at all: single column


def parse_csv(data: bytes, options: dict[str, Any], limits: Any) -> dict[str, Any]:
    try:
        text = decode_strict(data, options.get("encoding", "utf-8"))
    except UnicodeDecodeError:
        return result(NEEDS_INPUT, reason="ENCODING_ERROR", normalization=NORMALIZATION)
    delimiter = options.get("delimiter", "auto")
    if delimiter == "auto":
        delimiter, ambiguous = _sniff(text)
        if ambiguous:
            return result(
                NEEDS_INPUT, reason="AMBIGUOUS_DELIMITER", normalization=NORMALIZATION
            )
    header_first = options.get("header_mode") == "first_row_header"
    csv.field_size_limit(min(limits.output_bytes, 1 << 24))
    reader = csv.reader(
        io.StringIO(text, newline=""), delimiter=delimiter, strict=False
    )
    blocks: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    prev_end = 0
    records = 0
    try:
        for row in reader:
            end_line = reader.line_num
            start_line = prev_end + 1
            prev_end = end_line
            if not row:
                continue  # blank physical line
            records += 1
            if records > limits.max_rows:
                gaps.append(
                    {"code": "ROW_LIMIT", "detail": f">{limits.max_rows} records"}
                )
                break
            cols = row
            if len(cols) > limits.max_columns:
                gaps.append({"code": "COLUMN_LIMIT", "detail": f"record {records}"})
                cols = cols[: limits.max_columns]
            kind = "TABLE_HEADER" if (header_first and records == 1) else "TABLE_ROW"
            blocks.append(
                block(
                    kind,
                    " | ".join(cols),
                    {
                        "type": "CSV_RECORD",
                        "record": records,
                        "column_count": len(cols),
                        "start_line": start_line,
                        "end_line": end_line,
                    },
                    cell_data={"cells": cols, "delimiter": delimiter},
                )
            )
    except csv.Error:
        return result(NEEDS_INPUT, reason="MALFORMED_CSV", normalization=NORMALIZATION)
    if not blocks:
        return result(NEEDS_INPUT, reason="NO_USABLE_TEXT", normalization=NORMALIZATION)
    return result(
        PARTIAL if gaps else COMPLETE,
        reason=gaps[0]["code"] if gaps else None,
        blocks=blocks,
        unit="record",
        total=records,
        extracted=len(blocks),
        gaps=gaps,
        normalization=NORMALIZATION,
    )
