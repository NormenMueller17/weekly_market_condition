from __future__ import annotations

from typing import Dict, Optional
from openpyxl.worksheet.worksheet import Worksheet


def _build_header_map(ws: Worksheet, header_row: int = 1) -> Dict[str, int]:
    """Map header name -> column index (1-based). Ignores empty headers."""
    header_map: Dict[str, int] = {}
    for col in range(1, ws.max_column + 1):
        val = ws.cell(row=header_row, column=col).value
        if isinstance(val, str) and val.strip():
            header_map[val.strip()] = col
    return header_map


def autofit_columns_by_header(
    ws: Worksheet,
    header_row: int = 1,
    min_width: int = 10,
    padding: int = 2,
    max_width: int = 60,
) -> None:
    """Set column widths based on header text length (not data)."""
    for col in range(1, ws.max_column + 1):
        header_val = ws.cell(row=header_row, column=col).value
        header_txt = (str(header_val) if header_val is not None else "").strip()
        if not header_txt:
            continue
        width = min(max(len(header_txt) + padding, min_width), max_width)
        col_letter = ws.cell(row=header_row, column=col).column_letter
        ws.column_dimensions[col_letter].width = width


def apply_number_formats(
    ws: Worksheet,
    formats_by_colname: Dict[str, str],
    header_row: int = 1,
    data_start_row: int = 2,
) -> None:
    """Apply Excel number formats to columns by header name."""
    header_map = _build_header_map(ws, header_row=header_row)

    for col_name, fmt in formats_by_colname.items():
        col_idx = header_map.get(col_name)
        if not col_idx:
            continue
        for r in range(data_start_row, ws.max_row + 1):
            cell = ws.cell(row=r, column=col_idx)
            if isinstance(cell.value, (int, float)):
                cell.number_format = fmt


def sort_sheet_by_column(
    ws: Worksheet,
    sort_colname: str,
    header_row: int = 1,
    data_start_row: int = 2,
    ascending: bool = True,
) -> None:
    """Sort the sheet rows (in-place) by a column name."""
    header_map = _build_header_map(ws, header_row=header_row)
    sort_col_idx = header_map.get(sort_colname)
    if not sort_col_idx:
        return

    rows = []
    for r in range(data_start_row, ws.max_row + 1):
        row_values = [ws.cell(row=r, column=c).value for c in range(1, ws.max_column + 1)]
        sort_key = ws.cell(row=r, column=sort_col_idx).value
        rows.append((sort_key, row_values))

    def _key(item):
        k = item[0]
        return (k is None, k)

    rows.sort(key=_key, reverse=not ascending)

    for i, (_, row_values) in enumerate(rows, start=data_start_row):
        for c, v in enumerate(row_values, start=1):
            ws.cell(row=i, column=c).value = v


def format_sheet(
    ws: Worksheet,
    formats_by_colname: Optional[Dict[str, str]] = None,
    autofit_headers: bool = True,
    sort_by: Optional[str] = None,
    sort_ascending: bool = True,
    header_row: int = 1,
    data_start_row: int = 2,
) -> None:
    """High-level helper: optional sort, optional number formats, optional autofit."""
    if sort_by:
        sort_sheet_by_column(
            ws,
            sort_colname=sort_by,
            header_row=header_row,
            data_start_row=data_start_row,
            ascending=sort_ascending,
        )

    if formats_by_colname:
        apply_number_formats(
            ws,
            formats_by_colname=formats_by_colname,
            header_row=header_row,
            data_start_row=data_start_row,
        )

    if autofit_headers:
        autofit_columns_by_header(ws, header_row=header_row)
