"""TXT / Markdown / prompt / pasted text: strict decode, deterministic segmentation."""

from __future__ import annotations

from typing import Any

from .contracts import (
    COMPLETE,
    NEEDS_INPUT,
    block,
    result,
    segment_lines,
)

NORMALIZATION = "NEWLINE_LF;BOM_STRIPPED;UNICODE_UNCHANGED"


def decode_strict(data: bytes, encoding: str) -> str:
    if encoding == "utf-8":
        text = data.decode("utf-8")  # strict; BOM handled below
        return text[1:] if text.startswith("﻿") else text
    return data.decode(encoding)


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_text(data: bytes, options: dict[str, Any], limits: Any) -> dict[str, Any]:
    try:
        text = decode_strict(data, options.get("encoding", "utf-8"))
    except UnicodeDecodeError:
        return result(NEEDS_INPUT, reason="ENCODING_ERROR", normalization=NORMALIZATION)
    text = normalize_newlines(text)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    blocks = []
    for start, end, chunk in segment_lines(lines):
        if not chunk.strip():
            continue
        blocks.append(
            block(
                "TEXT",
                chunk,
                {"type": "TEXT_LINES", "start_line": start, "end_line": end},
            )
        )
    if not blocks:
        return result(NEEDS_INPUT, reason="NO_USABLE_TEXT", normalization=NORMALIZATION)
    return result(
        COMPLETE,
        blocks=blocks,
        unit="line",
        total=len(lines),
        extracted=len(lines),
        normalization=NORMALIZATION,
    )
