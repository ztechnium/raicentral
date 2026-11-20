"""Small synthetic fixtures with documented expected output (no customer data)."""

from __future__ import annotations

import io
import re
import zipfile

from docx import Document
from docx.oxml import parse_xml
from openpyxl import Workbook


def _esc(text: str) -> bytes:
    return (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .encode("latin-1")
    )


def make_pdf(pages: list) -> bytes:
    """Each page: a text string, "" (blank) or "IMAGE" (image only, needs OCR)."""
    objs: dict[int, bytes] = {
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        4: (
            b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray "
            b"/BitsPerComponent 8 /Length 1 >>\nstream\n\x00\nendstream"
        ),
    }
    kids = []
    for i, page in enumerate(pages):
        page_no, content_no = 5 + 2 * i, 6 + 2 * i
        kids.append(f"{page_no} 0 R")
        if page == "IMAGE":
            stream = b"q 100 0 0 100 10 10 cm /Im1 Do Q"
        elif page == "":
            stream = b" "
        else:
            lines = page.split("\n")
            stream = b"BT /F1 12 Tf 72 720 Td 14 TL "
            for line in lines:
                stream += b"(" + _esc(line) + b") Tj T* "
            stream += b"ET"
        objs[content_no] = (
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        )
        objs[page_no] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_no} 0 R "
            "/Resources << /Font << /F1 3 0 R >> /XObject << /Im1 4 0 R >> >> >>"
        ).encode()
    objs[2] = (
        f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
    )
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for number in sorted(objs):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objs[number] + b"\nendobj\n"
    xref = len(out)
    count = max(objs) + 1
    out += f"xref\n0 {count}\n".encode() + b"0000000000 65535 f \n"
    for number in range(1, count):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def encrypt_pdf(pdf: bytes) -> bytes:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    writer.encrypt("secret", algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def docx_bytes(build) -> bytes:
    document = Document()
    build(document)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def add_zip_member(data: bytes, name: str, content: bytes) -> bytes:
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as src,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst,
    ):
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        dst.writestr(name, content)
    return out.getvalue()


def replace_member(data: bytes, name: str, transform) -> bytes:
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as src,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst,
    ):
        for item in src.infolist():
            body = src.read(item.filename)
            if item.filename == name:
                body = transform(body)
            dst.writestr(item, body)
    return out.getvalue()


def xlsx_bytes(build) -> bytes:
    workbook = Workbook()
    build(workbook)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def with_cached_values(data: bytes, sheet: str, values: dict[str, str]) -> bytes:
    """Give formula cells a cached value, as Excel would have saved them."""

    def transform(xml: bytes) -> bytes:
        text = xml.decode("utf-8")
        for cell, value in values.items():
            pattern = rf'(<c r="{cell}"[^>]*>)(<f>.*?</f>)<v\s*/>|(<c r="{cell}"[^>]*>)(<f>.*?</f>)<v></v>'
            text, n = re.subn(
                pattern,
                lambda m: (
                    (m.group(1) or m.group(3))
                    + (m.group(2) or m.group(4))
                    + f"<v>{value}</v>"
                ),
                text,
            )
            assert n == 1, (cell, text[:400])
        return text.encode("utf-8")

    return replace_member(data, sheet, transform)


def tracked_change_paragraph(document, text: str) -> None:
    paragraph = document.add_paragraph("Before ")
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    paragraph._p.append(
        parse_xml(
            f'<w:ins {ns} w:id="1" w:author="a"><w:r><w:t>{text}</w:t></w:r></w:ins>'
        )
    )
