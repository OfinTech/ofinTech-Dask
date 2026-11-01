"""Writing text that came from outside into spreadsheets and CSV files without it becoming a formula.

Excel, LibreOffice and Google Sheets treat a cell that starts with ``=``, ``+``, ``-`` or ``@`` as a formula, and a
leading tab or carriage return hides such a prefix from a reader. Ledger descriptions, party names and notes are
typed by people in other systems and imported here, so every writer that puts such text into a file passes it
through one of these two helpers. Formulas the engine builds itself (``files/resolve.py``) never come this way.
"""

from __future__ import annotations

from typing import Any

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: Any) -> Any:
    """A value for ``csv.writer``: text that a spreadsheet would read as a formula gets a leading apostrophe, which
    spreadsheets show as the text itself. Numbers and ``None`` pass unchanged."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def set_cell(cell: Any, value: Any) -> Any:
    """Sets an openpyxl cell so that a string is always stored as text, whatever it starts with (openpyxl would
    otherwise store any ``=…`` string as a formula). Returns the cell."""
    cell.value = value
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        cell.data_type = "s"
    return cell
