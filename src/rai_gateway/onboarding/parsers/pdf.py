"""PDF text via pypdf, page by page. No OCR, no password handling, no layout claims."""

from __future__ import annotations

from typing import Any

from .contracts import (
    COMPLETE,
    FAILED,
    NEEDS_INPUT,
    NEEDS_OCR,
    PARTIAL,
    UNSUPPORTED,
    block,
    result,
    segment_lines,
)

NORMALIZATION = "NEWLINE_LF;PYPDF_TEXT_AS_OBSERVED"


def _has_images(page: Any) -> bool:
    """True when the page content actually draws an image (not merely lists one)."""
    try:
        contents = page.get_contents()
        if contents is None:
            return False
        xobjects = None
        resources = page.get("/Resources")
        if resources is not None:
            xobjects = resources.get_object().get("/XObject")
        for operands, operator in contents.operations:
            if operator == b"BI":
                return True
            if operator == b"Do" and operands and xobjects is not None:
                target = xobjects.get_object().get(operands[0])
                if (
                    target is not None
                    and target.get_object().get("/Subtype") == "/Image"
                ):
                    return True
    except Exception:  # noqa: BLE001 - unreadable content: treat as no image
        return False
    return False


def parse_pdf(path: str, options: dict[str, Any], limits: Any) -> dict[str, Any]:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            return result(
                UNSUPPORTED, reason="PASSWORD_REQUIRED", normalization=NORMALIZATION
            )
        total = len(reader.pages)
    except PyPdfError:
        return result(FAILED, reason="MALFORMED_DOCUMENT", normalization=NORMALIZATION)
    except Exception:  # noqa: BLE001
        return result(FAILED, reason="MALFORMED_DOCUMENT", normalization=NORMALIZATION)

    blocks: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    warnings: list[str] = []
    text_pages = scanned = blank = 0
    limit = min(total, limits.pdf_pages)
    if total > limit:
        gaps.append({"code": "PAGE_LIMIT", "detail": f"pages {limit + 1}-{total}"})
    for number in range(1, limit + 1):
        try:
            page = reader.pages[number - 1]
            text = (page.extract_text() or "").replace("\r\n", "\n").replace("\r", "\n")
        except Exception:  # noqa: BLE001 - one bad page must not hide the others
            gaps.append({"code": "PAGE_EXTRACT_ERROR", "detail": f"page {number}"})
            continue
        if not text.strip():
            if _has_images(page):
                scanned += 1
                gaps.append({"code": "OCR_REQUIRED", "detail": f"page {number}"})
            else:
                blank += 1
                warnings.append("BLANK_PAGE")
            continue
        text_pages += 1
        lines = text.split("\n")
        for segment, (_s, _e, chunk) in enumerate(
            segment_lines(lines, max_chars=6000), 1
        ):
            if chunk.strip():
                blocks.append(
                    block(
                        "PAGE_TEXT",
                        chunk,
                        {"type": "PDF_PAGE", "page": number, "segment": segment},
                    )
                )
    cov = {
        "unit": "page",
        "total": total,
        "extracted": text_pages,
        "gaps": gaps,
        "warnings": warnings,
        "normalization": NORMALIZATION,
    }
    if total == 0 or (text_pages == 0 and scanned == 0 and not gaps):
        return result(
            NEEDS_INPUT,
            reason="NO_USABLE_TEXT",
            **{
                k: cov[k]
                for k in (
                    "unit",
                    "total",
                    "extracted",
                    "gaps",
                    "warnings",
                    "normalization",
                )
            },
        )
    if text_pages == 0:
        reason = "SCANNED_IMAGE_ONLY" if scanned else gaps[0]["code"]
        outcome = NEEDS_OCR if scanned else FAILED
        return result(
            outcome,
            reason=reason,
            **{
                k: cov[k]
                for k in (
                    "unit",
                    "total",
                    "extracted",
                    "gaps",
                    "warnings",
                    "normalization",
                )
            },
        )
    outcome = PARTIAL if gaps else COMPLETE
    reason = gaps[0]["code"] if gaps else None
    return result(
        outcome,
        reason=reason,
        blocks=blocks,
        unit="page",
        total=total,
        extracted=text_pages,
        gaps=gaps,
        warnings=warnings,
        normalization=NORMALIZATION,
    )
