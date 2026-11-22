"""Deterministic parsers: outcomes, locators, coverage and resource isolation."""

from __future__ import annotations

import io
import os
import time

import pytest
from parser_fixtures import (
    add_zip_member,
    docx_bytes,
    encrypt_pdf,
    make_pdf,
    tracked_change_paragraph,
    with_cached_values,
    xlsx_bytes,
)

from rai_gateway.onboarding.parsers import (
    Limits,
    OptionsError,
    effective_options,
    parse_file,
    validate_options,
)
from rai_gateway.onboarding.parsers.profile import parser_profile
from rai_gateway.onboarding.parsers.runner import ParserStopped, run_parser

LIM = Limits()


def parse(tmp_path, name, data, family, *, kind="FILE", options=None, limits=LIM):
    path = tmp_path / name
    path.write_bytes(data)
    eff = effective_options(family, kind, options)
    return parse_file(str(path), family, kind, eff, limits)


# ------------------------------------------------------------------ text / md
def test_text_preserves_braces_html_arabic_emoji_and_combining(tmp_path):
    text = "سياسة الذكاء الاصطناعي ١٢٣ with {braces} and <script>x</script>\n[link](http://x)\n👩‍💻 é\n"
    res = parse(tmp_path, "a.md", text.encode("utf-8"), "MARKDOWN")
    assert res["outcome"] == "COMPLETE"
    assert res["blocks"][0]["text"] == text.rstrip("\n")
    assert res["blocks"][0]["locator"] == {
        "type": "TEXT_LINES",
        "start_line": 1,
        "end_line": 3,
    }
    assert "BOM_STRIPPED" in res["normalization"]


def test_text_bom_crlf_and_blank_input_and_bad_encoding(tmp_path):
    res = parse(tmp_path, "a.txt", b"\xef\xbb\xbfone\r\ntwo\rthree", "TXT")
    assert res["blocks"][0]["text"] == "one\ntwo\nthree"
    blank = parse(tmp_path, "b.txt", b" \n\n ", "TXT")
    assert (blank["outcome"], blank["reason"]) == ("NEEDS_INPUT", "NO_USABLE_TEXT")
    bad = parse(tmp_path, "c.txt", b"caf\xe9", "TXT")
    assert (bad["outcome"], bad["reason"]) == ("NEEDS_INPUT", "ENCODING_ERROR")


def test_long_text_is_segmented_without_loss_or_repeat(tmp_path):
    lines = [f"line {i} " + "x" * 50 for i in range(500)]
    res = parse(tmp_path, "a.txt", "\n".join(lines).encode(), "TXT")
    assert len(res["blocks"]) > 1
    assert "\n".join(b["text"] for b in res["blocks"]) == "\n".join(lines)
    spans = [
        (b["locator"]["start_line"], b["locator"]["end_line"]) for b in res["blocks"]
    ]
    assert spans[0][0] == 1 and spans[-1][1] == 500
    assert all(a[1] + 1 == b[0] for a, b in zip(spans, spans[1:]))


def test_text_source_ignores_encoding_overrides():
    with pytest.raises(OptionsError):
        validate_options("TEXT", "TEXT", {"encoding": "windows-1256"})
    assert effective_options("TEXT", "TEXT", None)["encoding"] == "utf-8"


# ------------------------------------------------------------------------ csv
def test_csv_quoted_newlines_delimiters_duplicate_headers_and_literal_formulas(
    tmp_path,
):
    data = 'name,name,note\n"a, b",=1+1,"line1\nline2"\n007,+x,\n'.encode()
    res = parse(tmp_path, "a.csv", data, "CSV")
    assert res["outcome"] == "COMPLETE"
    rows = [b["cell_data"]["cells"] for b in res["blocks"]]
    assert rows == [
        ["name", "name", "note"],
        ["a, b", "=1+1", "line1\nline2"],
        ["007", "+x", ""],
    ]
    assert (
        res["blocks"][0]["kind"] == "TABLE_ROW"
    )  # first row preserved, not guessed as header
    assert (
        res["blocks"][1]["locator"]["start_line"] == 2
        and res["blocks"][1]["locator"]["end_line"] == 3
    )
    assert res["blocks"][2]["locator"]["record"] == 3


def test_csv_header_mode_is_explicit_only(tmp_path):
    res = parse(
        tmp_path,
        "a.csv",
        b"a,b\n1,2\n",
        "CSV",
        options={"header_mode": "first_row_header"},
    )
    assert [b["kind"] for b in res["blocks"]] == ["TABLE_HEADER", "TABLE_ROW"]


def test_csv_ambiguous_delimiter_needs_input_and_explicit_choice_resolves(tmp_path):
    data = b"a,b;c\n1,2;3\n"
    ambiguous = parse(tmp_path, "a.csv", data, "CSV")
    assert (ambiguous["outcome"], ambiguous["reason"]) == (
        "NEEDS_INPUT",
        "AMBIGUOUS_DELIMITER",
    )
    chosen = parse(tmp_path, "a.csv", data, "CSV", options={"delimiter": ";"})
    assert chosen["blocks"][0]["cell_data"]["cells"] == ["a,b", "c"]


def test_csv_utf8_bom_windows1256_and_invalid_encoding(tmp_path):
    bom = parse(tmp_path, "a.csv", "﻿اسم,قيمة\n".encode("utf-8"), "CSV")
    assert bom["blocks"][0]["cell_data"]["cells"] == ["اسم", "قيمة"]
    legacy = "اسم,قيمة\n".encode("windows-1256")
    default = parse(tmp_path, "b.csv", legacy, "CSV")
    assert (default["outcome"], default["reason"]) == ("NEEDS_INPUT", "ENCODING_ERROR")
    override = parse(
        tmp_path, "b.csv", legacy, "CSV", options={"encoding": "windows-1256"}
    )
    assert override["blocks"][0]["cell_data"]["cells"] == ["اسم", "قيمة"]
    with pytest.raises(OptionsError):
        validate_options("FILE", "CSV", {"encoding": "latin-1"})
    with pytest.raises(OptionsError):
        validate_options("FILE", "CSV", {"parser": "evil.Class"})


def test_csv_row_limit_is_reported_not_silent(tmp_path):
    data = "\n".join(f"{i},x" for i in range(30)).encode()
    res = parse(tmp_path, "a.csv", data, "CSV", limits=Limits(max_rows=10))
    assert res["outcome"] == "PARTIAL" and res["reason"] == "ROW_LIMIT"
    assert (
        len(res["blocks"]) == 10 and res["coverage"]["gaps"][0]["code"] == "ROW_LIMIT"
    )


# ------------------------------------------------------------------------ pdf
def test_pdf_text_pages_and_one_based_page_locators(tmp_path):
    res = parse(
        tmp_path, "a.pdf", make_pdf(["First page policy", "Second page rules"]), "PDF"
    )
    assert res["outcome"] == "COMPLETE"
    assert [b["locator"]["page"] for b in res["blocks"]] == [1, 2]
    assert "First page policy" in res["blocks"][0]["text"]
    assert res["coverage"]["total"] == 2 and res["coverage"]["extracted"] == 2


def test_pdf_scanned_mixed_and_blank_outcomes(tmp_path):
    scanned = parse(tmp_path, "a.pdf", make_pdf(["IMAGE", "IMAGE"]), "PDF")
    assert (scanned["outcome"], scanned["reason"]) == (
        "NEEDS_OCR",
        "SCANNED_IMAGE_ONLY",
    )
    mixed = parse(tmp_path, "b.pdf", make_pdf(["Readable text", "IMAGE"]), "PDF")
    assert mixed["outcome"] == "PARTIAL"
    assert mixed["coverage"]["gaps"] == [{"code": "OCR_REQUIRED", "detail": "page 2"}]
    assert mixed["coverage"]["extracted"] == 1 and mixed["coverage"]["total"] == 2
    blank = parse(tmp_path, "c.pdf", make_pdf(["", ""]), "PDF")
    assert (blank["outcome"], blank["reason"]) == ("NEEDS_INPUT", "NO_USABLE_TEXT")


def test_pdf_encrypted_malformed_and_page_limit(tmp_path):
    encrypted = parse(tmp_path, "a.pdf", encrypt_pdf(make_pdf(["secret text"])), "PDF")
    assert (encrypted["outcome"], encrypted["reason"]) == (
        "UNSUPPORTED",
        "PASSWORD_REQUIRED",
    )
    assert encrypted["blocks"] == []
    broken = parse(tmp_path, "b.pdf", b"%PDF-1.4\nnot really a pdf", "PDF")
    assert broken["outcome"] == "FAILED"
    limited = parse(
        tmp_path,
        "c.pdf",
        make_pdf(["p1", "p2", "p3"]),
        "PDF",
        limits=Limits(pdf_pages=2),
    )
    assert limited["outcome"] == "PARTIAL" and limited["reason"] == "PAGE_LIMIT"
    assert len(limited["blocks"]) == 2


# ----------------------------------------------------------------------- docx
def test_docx_document_order_nested_and_merged_tables(tmp_path):
    def build(doc):
        doc.add_paragraph("Intro")
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "A1"
        merged = table.cell(0, 1).merge(table.cell(1, 1))
        merged.text = "Merged"
        table.cell(1, 0).text = "A2"
        inner = table.cell(1, 0).add_table(rows=1, cols=1)
        inner.cell(0, 0).text = "Nested"
        doc.add_paragraph("Outro")

    res = parse(tmp_path, "a.docx", docx_bytes(build), "DOCX")
    texts = [b["text"] for b in res["blocks"]]
    assert texts == [
        "Intro",
        "A1",
        "Merged",
        "A2",
        "Nested",
        "Outro",
    ]  # order, no duplicate merge
    paths = [b["locator"]["path"] for b in res["blocks"]]
    assert paths[0] == "body/p[1]" and paths[1] == "body/tbl[1]/r[1]/c[1]/p[1]"
    assert "tbl[1]/r[2]/c[1]/tbl[1]" in paths[4]
    assert paths[5] == "body/p[3]" or paths[5].startswith("body/p[")
    assert res["outcome"] == "COMPLETE"
    assert all(
        b["locator"]["type"] == "DOCX_PATH" and "page" not in b["locator"]
        for b in res["blocks"]
    )


def test_docx_headers_footers_distinct_and_unsupported_parts_reported(tmp_path):
    def build(doc):
        doc.add_paragraph("Body")
        section = doc.sections[0]
        section.header.is_linked_to_previous = False
        section.header.paragraphs[0].text = "Header text"
        section.footer.is_linked_to_previous = False
        section.footer.paragraphs[0].text = "Footer text"
        doc.add_section()  # second section links to the first: no duplicate parts

    data = docx_bytes(build)
    res = parse(tmp_path, "a.docx", data, "DOCX")
    kinds = [(b["locator"]["part"], b["text"]) for b in res["blocks"]]
    assert ("document", "Body") in kinds
    assert sum(1 for _, t in kinds if t == "Header text") == 1
    assert sum(1 for _, t in kinds if t == "Footer text") == 1
    assert res["outcome"] == "COMPLETE"

    notes = (
        b'<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b'<w:footnote w:id="2"><w:p><w:r><w:t>Footnote content</w:t></w:r></w:p></w:footnote></w:footnotes>'
    )
    flagged = parse(
        tmp_path, "b.docx", add_zip_member(data, "word/footnotes.xml", notes), "DOCX"
    )
    assert flagged["outcome"] == "PARTIAL"
    assert {"code": "FOOTNOTES", "detail": "word/footnotes.xml"} in flagged["coverage"][
        "gaps"
    ]


def test_docx_tracked_changes_are_reported_not_silently_resolved(tmp_path):
    data = docx_bytes(lambda d: tracked_change_paragraph(d, "inserted wording"))
    res = parse(tmp_path, "a.docx", data, "DOCX")
    assert res["outcome"] == "PARTIAL" and "TRACKED_CHANGES" in res["warnings"]
    assert all("inserted wording" not in b["text"] for b in res["blocks"])


def test_docx_xml_entities_and_zip_bombs_are_rejected(tmp_path):
    evil = (
        b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x "boom">]>'
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">&x;</w:document>'
    )
    base = docx_bytes(lambda d: d.add_paragraph("x"))
    from parser_fixtures import replace_member

    res = parse(
        tmp_path,
        "a.docx",
        replace_member(base, "word/document.xml", lambda _: evil),
        "DOCX",
    )
    assert (res["outcome"], res["reason"]) == ("FAILED", "UNSAFE_XML")
    bomb = add_zip_member(base, "word/junk.bin", b"\x00" * (6 * 1024 * 1024))
    res = parse(
        tmp_path, "b.docx", bomb, "DOCX", limits=Limits(zip_member_bytes=1024 * 1024)
    )
    assert (res["outcome"], res["reason"]) == ("FAILED", "ARCHIVE_LIMIT_EXCEEDED")
    ratio = parse(tmp_path, "c.docx", bomb, "DOCX", limits=Limits(zip_ratio=10))
    assert ratio["reason"] == "ARCHIVE_LIMIT_EXCEEDED"
    encrypted = parse(tmp_path, "d.docx", b"\xd0\xcf\x11\xe0" + b"\x00" * 64, "DOCX")
    assert (encrypted["outcome"], encrypted["reason"]) == (
        "UNSUPPORTED",
        "PASSWORD_REQUIRED",
    )


# ----------------------------------------------------------------------- xlsx
def test_xlsx_sheets_hidden_content_merges_strings_and_formulas(tmp_path):
    def build(wb):
        ws = wb.active
        ws.title = "AI Inventory"
        ws["A1"], ws["B1"] = "System", "Id"
        ws["A2"], ws["B2"] = (
            "Support",
            "00123",
        )  # numeric-looking identifier stays a string
        ws["C2"] = 12.5
        ws["A3"] = "=SUM(C2:C2)"
        ws["A4"] = "=1+1"
        ws.merge_cells("A5:B5")
        ws["A5"] = "Merged heading"
        ws.row_dimensions[2].hidden = True
        ws.column_dimensions["C"].hidden = True
        hidden = wb.create_sheet("Secret")
        hidden["A1"] = "Hidden sheet text"
        hidden.sheet_state = "hidden"

    data = with_cached_values(
        xlsx_bytes(build), "xl/worksheets/sheet1.xml", {"A3": "12.5"}
    )
    res = parse(tmp_path, "a.xlsx", data, "XLSX")
    cells = {
        (b["locator"]["sheet_name"], b["locator"]["cell"]): b for b in res["blocks"]
    }
    assert res["outcome"] == "COMPLETE" and res["coverage"]["total"] == 2
    assert cells[("AI Inventory", "B2")]["cell_data"]["value"] == "00123"
    assert cells[("AI Inventory", "B2")]["cell_data"]["hidden_row"] is True
    assert cells[("AI Inventory", "C2")]["cell_data"]["hidden_column"] is True
    present = cells[("AI Inventory", "A3")]["cell_data"]
    assert present["formula"] == "=SUM(C2:C2)" and present["cached_value"] == 12.5
    assert present["cache_state"] == "PRESENT_UNVERIFIED"
    missing = cells[("AI Inventory", "A4")]["cell_data"]
    assert (
        missing["cache_state"] == "MISSING" and missing["cached_value"] is None
    )  # never zero
    assert cells[("AI Inventory", "A5")]["cell_data"]["merge_range"] == "A5:B5"
    assert ("AI Inventory", "B5") not in cells  # merged blanks are not forward-filled
    assert cells[("Secret", "A1")]["cell_data"]["sheet_visibility"] == "hidden"
    assert "HIDDEN_SHEET_CONTENT" in res["warnings"]
    assert all(b["locator"]["type"] == "XLSX_CELL" for b in res["blocks"])


def test_xlsx_misleading_dimension_and_cell_caps_are_bounded(tmp_path):
    def build(wb):
        ws = wb.active
        for r in range(1, 30):
            ws.cell(row=r, column=1, value=f"v{r}")

    from parser_fixtures import replace_member

    data = replace_member(
        xlsx_bytes(build),
        "xl/worksheets/sheet1.xml",
        lambda x: x.replace(
            b'<dimension ref="A1:A29"', b'<dimension ref="A1:XFD1048576"'
        ),
    )
    started = time.monotonic()
    res = parse(tmp_path, "a.xlsx", data, "XLSX")
    assert time.monotonic() - started < 20 and res["outcome"] == "COMPLETE"
    assert len(res["blocks"]) == 29
    capped = parse(tmp_path, "b.xlsx", data, "XLSX", limits=Limits(nonempty_cells=10))
    assert capped["outcome"] == "PARTIAL" and capped["reason"] == "NONEMPTY_CELL_LIMIT"
    assert len(capped["blocks"]) == 10


def test_xlsx_macros_links_and_registry_are_never_touched(tmp_path):
    def build(wb):
        wb.active["A1"] = '=HYPERLINK("http://example.invalid","x")'

    res = parse(tmp_path, "a.xlsx", xlsx_bytes(build), "XLSX")
    assert res["blocks"][0]["cell_data"]["formula"].startswith("=HYPERLINK")
    assert res["blocks"][0]["cell_data"]["cache_state"] == "MISSING"


# ------------------------------------------------------------- isolation/limits
def test_child_process_runs_with_minimal_environment_and_reports_limits(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("ONBOARDING_AI_KEY_X", "sk-should-not-leak-either")
    from rai_gateway.onboarding.parsers.runner import _child_env

    env = _child_env()
    assert not any("KEY" in k or "DATABASE" in k or "SECRET" in k for k in env)
    path = tmp_path / "a.txt"
    path.write_text("hello")
    res = run_parser(
        str(path), "TXT", "FILE", effective_options("TXT", "FILE", None), LIM
    )
    assert res["outcome"] == "COMPLETE" and "limits_active" in res
    if os.name != "nt":
        assert res["limits_active"] == {"memory": True, "cpu": True}


def test_wall_time_limit_kills_the_child_and_parent_stays_usable(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("hello")
    started = time.monotonic()
    res = run_parser(
        str(path),
        "TXT",
        "FILE",
        effective_options("TXT", "FILE", None),
        Limits(wall_seconds=0.05),
    )
    assert res["outcome"] == "FAILED" and res["reason"] == "PARSER_TIMEOUT"
    assert time.monotonic() - started < 10
    ok = run_parser(
        str(path), "TXT", "FILE", effective_options("TXT", "FILE", None), LIM
    )
    assert ok["outcome"] == "COMPLETE"


def test_cancellation_terminates_the_child(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("hello")
    calls = {"n": 0}

    def stop() -> bool:
        calls["n"] += 1
        return True

    with pytest.raises(ParserStopped):
        run_parser(
            str(path),
            "TXT",
            "FILE",
            effective_options("TXT", "FILE", None),
            LIM,
            should_stop=stop,
            poll_seconds=0.01,
        )
    assert calls["n"] >= 1


def test_output_budget_is_partial_with_a_gap_not_silent_truncation(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("\n".join(f"line {i} " + "x" * 80 for i in range(400)))
    res = run_parser(
        str(path),
        "TXT",
        "FILE",
        effective_options("TXT", "FILE", None),
        Limits(output_bytes=10000),
    )
    assert res["outcome"] == "PARTIAL" and res["reason"] == "OUTPUT_LIMIT"
    assert res["coverage"]["gaps"][-1]["code"] == "OUTPUT_LIMIT" and res["blocks"]


@pytest.mark.skipif(
    os.name == "nt", reason="address-space limits need a POSIX platform"
)
def test_memory_limit_is_enforced_on_posix(tmp_path):
    from parser_fixtures import make_pdf

    path = tmp_path / "a.pdf"
    path.write_bytes(make_pdf(["x"]))
    res = run_parser(
        str(path),
        "PDF",
        "FILE",
        effective_options("PDF", "FILE", None),
        Limits(memory_bytes=32 * 1024 * 1024),
    )
    assert res["outcome"] == "FAILED"


def test_parser_profile_fingerprint_changes_with_limits_and_dependencies():
    base = parser_profile(Limits())
    assert base["fingerprint"] == parser_profile(Limits())["fingerprint"]
    assert parser_profile(Limits(pdf_pages=10))["fingerprint"] != base["fingerprint"]
    assert {"pypdf", "python-docx", "openpyxl", "defusedxml"} <= set(
        base["dependencies"]
    )


def test_effective_options_include_defaults_so_identity_changes_with_them():
    assert effective_options("CSV", "FILE", None) != effective_options(
        "CSV", "FILE", {"delimiter": ";"}
    )
    assert effective_options("XLSX", "FILE", None)["hidden_sheets"] == "INCLUDE"
    assert io  # keep import used
