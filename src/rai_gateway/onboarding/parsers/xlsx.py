"""XLSX cells with formula text and cached values kept separate.

Never recalculates, runs macros or follows external links. Iteration visits only
cells that exist in the sheet, bounded by explicit caps, so a misleading
dimension record cannot trigger an enormous scan or hide content.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .contracts import COMPLETE, FAILED, NEEDS_INPUT, PARTIAL, block, result
from .ooxml import OoxmlRejected, preflight, rejected_result

NORMALIZATION = "XLSX_STORED_VALUES"


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    return str(value)


def _hidden_columns(ws: Any) -> set[int]:
    hidden: set[int] = set()
    for dim in ws.column_dimensions.values():
        if dim.hidden and dim.min and dim.max:
            hidden.update(range(dim.min, min(dim.max, 16384) + 1))
    return hidden


def parse_xlsx(path: str, options: dict[str, Any], limits: Any) -> dict[str, Any]:
    try:
        preflight(path, limits, ("[Content_Types].xml", "xl/workbook.xml"))
    except OoxmlRejected as exc:
        return rejected_result(exc.code)
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    try:
        # File objects, not paths: stored originals have opaque names without extensions,
        # and openpyxl rejects path arguments by extension.
        with open(path, "rb") as handle:
            formulas = load_workbook(handle, data_only=False, keep_links=False)
        with open(path, "rb") as handle:
            cached = load_workbook(handle, data_only=True, keep_links=False)
    except Exception:  # noqa: BLE001
        return result(FAILED, reason="MALFORMED_DOCUMENT")

    blocks: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    warnings: list[str] = []
    visited = nonempty = 0
    sheets = list(formulas.worksheets)
    total_sheets = len(sheets)
    if total_sheets > limits.xlsx_sheets:
        gaps.append({"code": "SHEET_LIMIT", "detail": f">{limits.xlsx_sheets} sheets"})
        sheets = sheets[: limits.xlsx_sheets]
    clean_sheets = 0
    stop = False
    for index, ws in enumerate(sheets, 1):
        sheet_gap = False
        wsv = cached[ws.title]
        merged = {r.coord.split(":")[0]: r.coord for r in ws.merged_cells.ranges}
        hidden_cols = _hidden_columns(ws)
        for (row, col), cell in sorted(ws._cells.items()):
            visited += 1
            if visited > limits.cells_visited:
                gaps.append({"code": "CELL_VISIT_LIMIT", "detail": ws.title})
                sheet_gap = stop = True
                break
            if cell.value is None or cell.value == "":
                continue
            if row > limits.max_rows:
                if not sheet_gap:
                    gaps.append({"code": "ROW_LIMIT", "detail": ws.title})
                sheet_gap = True
                continue
            if col > limits.max_columns:
                if not sheet_gap:
                    gaps.append({"code": "COLUMN_LIMIT", "detail": ws.title})
                sheet_gap = True
                continue
            nonempty += 1
            if nonempty > limits.nonempty_cells:
                gaps.append({"code": "NONEMPTY_CELL_LIMIT", "detail": ws.title})
                sheet_gap = stop = True
                break
            coord = f"{get_column_letter(col)}{row}"
            is_formula = cell.data_type == "f"
            data: dict[str, Any] = {
                "sheet_index": index,
                "sheet_name": ws.title,
                "sheet_visibility": ws.sheet_state,
                "cell": coord,
                "data_type": cell.data_type,
                "number_format": cell.number_format,
                "merge_range": merged.get(coord),
                "hidden_row": bool(ws.row_dimensions[row].hidden)
                if row in ws.row_dimensions
                else False,
                "hidden_column": col in hidden_cols,
            }
            if is_formula:
                formula = (
                    cell.value
                    if isinstance(cell.value, str)
                    else getattr(cell.value, "text", str(cell.value))
                )
                cv_cell = wsv._cells.get((row, col))
                cv = None if cv_cell is None else _plain(cv_cell.value)
                data.update(
                    formula=formula,
                    cached_value=cv,
                    cache_state="MISSING" if cv is None else "PRESENT_UNVERIFIED",
                )
                text = formula if cv is None else str(cv)
            else:
                data["value"] = _plain(cell.value)
                text = str(_plain(cell.value))
            if ws.sheet_state != "visible":
                warnings.append("HIDDEN_SHEET_CONTENT")
            blocks.append(
                block(
                    "TABLE_CELL",
                    text,
                    {
                        "type": "XLSX_CELL",
                        "sheet_index": index,
                        "sheet_name": ws.title,
                        "cell": coord,
                    },
                    cell_data=data,
                )
            )
        if not sheet_gap:
            clean_sheets += 1
        if stop:
            break
    formulas.close()
    cached.close()
    if not blocks:
        return result(
            NEEDS_INPUT,
            reason="NO_USABLE_TEXT",
            unit="sheet",
            total=total_sheets,
            extracted=0,
            gaps=gaps,
            warnings=warnings,
            normalization=NORMALIZATION,
        )
    return result(
        PARTIAL if gaps else COMPLETE,
        reason=gaps[0]["code"] if gaps else None,
        blocks=blocks,
        unit="sheet",
        total=total_sheets,
        extracted=clean_sheets,
        gaps=gaps,
        warnings=warnings,
        normalization=NORMALIZATION,
    )
