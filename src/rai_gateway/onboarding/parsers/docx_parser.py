"""DOCX in document order, recursing into tables; reports unsupported content."""

from __future__ import annotations

import zipfile
from typing import Any

from defusedxml import ElementTree as SafeET

from .contracts import COMPLETE, FAILED, NEEDS_INPUT, PARTIAL, block, result
from .ooxml import OoxmlRejected, preflight, read_bounded, rejected_result

NORMALIZATION = "NEWLINE_LF;DOCX_PARAGRAPH_TEXT"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _scan_unsupported(
    path: str, names: set[str], limits: Any
) -> tuple[list[dict], list[str]]:
    """Detect content in parts this parser does not extract."""
    gaps: list[dict[str, Any]] = []
    warnings: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for part, tag, code in (
            ("word/footnotes.xml", "footnote", "FOOTNOTES"),
            ("word/endnotes.xml", "endnote", "ENDNOTES"),
        ):
            if part in names:
                root = SafeET.fromstring(
                    read_bounded(archive, part, limits.zip_member_bytes)
                )
                for note in root.iter(f"{W}{tag}"):
                    if note.get(f"{W}id") in ("-1", "0"):
                        continue  # separators
                    if any((t.text or "").strip() for t in note.iter(f"{W}t")):
                        gaps.append({"code": code, "detail": part})
                        break
        if "word/comments.xml" in names:
            root = SafeET.fromstring(
                read_bounded(archive, "word/comments.xml", limits.zip_member_bytes)
            )
            if any(True for _ in root.iter(f"{W}comment")):
                gaps.append({"code": "COMMENTS", "detail": "word/comments.xml"})
        doc = SafeET.fromstring(
            read_bounded(archive, "word/document.xml", limits.zip_member_bytes)
        )
        if any(True for _ in doc.iter(f"{W}txbxContent")):
            gaps.append({"code": "TEXT_BOXES", "detail": "word/document.xml"})
        if any(True for _ in doc.iter(f"{W}ins")) or any(
            True for _ in doc.iter(f"{W}del")
        ):
            gaps.append(
                {
                    "code": "TRACKED_CHANGES",
                    "detail": "inserted/deleted runs not extracted",
                }
            )
            warnings.append("TRACKED_CHANGES")
    return gaps, warnings


def parse_docx(path: str, options: dict[str, Any], limits: Any) -> dict[str, Any]:
    try:
        names = preflight(path, limits, ("[Content_Types].xml", "word/document.xml"))
    except OoxmlRejected as exc:
        return rejected_result(exc.code)
    if "word/document.xml" not in names:
        return result(FAILED, reason="MALFORMED_DOCUMENT")
    try:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        document = Document(path)
        gaps, warnings = _scan_unsupported(path, names, limits)
    except OoxmlRejected as exc:
        return rejected_result(exc.code)
    except Exception:  # noqa: BLE001
        return result(FAILED, reason="MALFORMED_DOCUMENT")

    blocks: list[dict[str, Any]] = []

    def emit(par: Any, part: str, path_: str, in_cell: bool) -> None:
        text = par.text
        if text.strip():
            blocks.append(
                block(
                    "TABLE_CELL" if in_cell else "PARAGRAPH",
                    text,
                    {"type": "DOCX_PATH", "part": part, "path": path_},
                )
            )

    def walk(element: Any, parent: Any, part: str, prefix: str, in_cell: bool) -> None:
        p_i = t_i = 0
        for child in element.iterchildren():
            if child.tag == qn("w:p"):
                p_i += 1
                emit(Paragraph(child, parent), part, f"{prefix}/p[{p_i}]", in_cell)
            elif child.tag == qn("w:tbl"):
                t_i += 1
                table = Table(child, parent)
                seen: set[int] = set()  # merged cells repeat the same underlying tc
                for r_i, row in enumerate(table.rows, 1):
                    for c_i, cell in enumerate(row.cells, 1):
                        if id(cell._tc) in seen:
                            continue
                        seen.add(id(cell._tc))
                        walk(
                            cell._tc,
                            cell,
                            part,
                            f"{prefix}/tbl[{t_i}]/r[{r_i}]/c[{c_i}]",
                            True,
                        )

    walk(document.element.body, document, "document", "body", False)
    parts = 1
    seen_parts: set[str] = set()
    for section in document.sections:
        for attr in (
            "header",
            "first_page_header",
            "even_page_header",
            "footer",
            "first_page_footer",
            "even_page_footer",
        ):
            hf = getattr(section, attr)
            if hf.is_linked_to_previous:
                continue
            partname = str(hf.part.partname)
            if partname in seen_parts:
                continue
            seen_parts.add(partname)
            parts += 1
            walk(hf._element, hf, partname, "hdrftr", False)

    total = parts + len(gaps)
    if not blocks:
        return result(
            NEEDS_INPUT,
            reason="NO_USABLE_TEXT",
            gaps=gaps,
            warnings=warnings,
            unit="part",
            total=total,
            extracted=0,
            normalization=NORMALIZATION,
        )
    return result(
        PARTIAL if gaps else COMPLETE,
        reason=gaps[0]["code"] if gaps else None,
        blocks=blocks,
        unit="part",
        total=total,
        extracted=parts,
        gaps=gaps,
        warnings=warnings,
        normalization=NORMALIZATION,
    )
