"""Spreadsheet reader — read xlsx, ods, csv with pagination."""

CATEGORY = "documents"

import csv
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

DEFINITION = {
    "type": "function",
    "function": {
        "name": "sheet_read",
        "description": (
            "Read a spreadsheet (xlsx, ods, csv). "
            "Returns sheet list and column headers on first call. "
            "Use offset/limit for pagination on large files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path",
                },
                "sheet": {
                    "type": "string",
                    "description": "Sheet name or index (default: first sheet)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Start from this row (0-based, 0 = first data row after header, default: 0)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of rows to return (default: 30)",
                },
            },
            "required": ["path"],
        },
    },
}

# XML namespaces
_XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_ODS_TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_ODS_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"


def _col_index(col_str: str) -> int:
    """Convert column letter (A, B, ..., AA) to 0-based index."""
    result = 0
    for c in col_str.upper():
        result = result * 26 + (ord(c) - ord("A") + 1)
    return result - 1


def _read_xlsx(path: Path, sheet_name: str = None) -> dict:
    """Read xlsx using stdlib. Returns {sheets, headers, rows, sheet_name}."""
    try:
        with zipfile.ZipFile(path) as z:
            # Read shared strings
            shared = []
            if "xl/sharedStrings.xml" in z.namelist():
                ss_tree = ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
                for si in ss_tree.findall(f".//{{{_XLSX_NS}}}si"):
                    texts = []
                    for t in si.iter(f"{{{_XLSX_NS}}}t"):
                        if t.text:
                            texts.append(t.text)
                    shared.append("".join(texts))

            # Get sheet list from workbook
            wb_tree = ElementTree.fromstring(z.read("xl/workbook.xml"))
            sheets = []
            for s in wb_tree.findall(f".//{{{_XLSX_NS}}}sheet"):
                sheets.append(s.get("name", f"Sheet{len(sheets) + 1}"))

            # Determine which sheet to read
            sheet_idx = 0
            if sheet_name:
                if sheet_name.isdigit():
                    sheet_idx = int(sheet_name)
                else:
                    for i, name in enumerate(sheets):
                        if name.lower() == sheet_name.lower():
                            sheet_idx = i
                            break

            actual_name = sheets[sheet_idx] if sheet_idx < len(sheets) else f"Sheet{sheet_idx + 1}"

            # Read sheet XML
            sheet_file = f"xl/worksheets/sheet{sheet_idx + 1}.xml"
            if sheet_file not in z.namelist():
                return {"error": f"Sheet not found: {sheet_file}"}

            sheet_tree = ElementTree.fromstring(z.read(sheet_file))

            # Parse rows
            rows = []
            for row_el in sheet_tree.findall(f".//{{{_XLSX_NS}}}row"):
                row_data = {}
                for cell in row_el.findall(f"{{{_XLSX_NS}}}c"):
                    ref = cell.get("r", "")
                    col_letter = re.match(r"([A-Z]+)", ref)
                    if not col_letter:
                        continue
                    col_idx = _col_index(col_letter.group(1))
                    val_el = cell.find(f"{{{_XLSX_NS}}}v")
                    val = val_el.text if val_el is not None and val_el.text else ""
                    if cell.get("t") == "s" and val.isdigit():
                        idx = int(val)
                        val = shared[idx] if idx < len(shared) else val
                    row_data[col_idx] = val
                if row_data:
                    max_col = max(row_data.keys()) + 1
                    row = [row_data.get(i, "") for i in range(max_col)]
                    rows.append(row)

            return {"sheets": sheets, "rows": rows, "sheet_name": actual_name}

    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as e:
        return {"error": f"Error reading xlsx: {e}"}


def _read_ods(path: Path, sheet_name: str = None) -> dict:
    """Read ods using stdlib."""
    try:
        with zipfile.ZipFile(path) as z:
            tree = ElementTree.fromstring(z.read("content.xml"))

        tables = tree.findall(f".//{{{_ODS_TABLE_NS}}}table")
        sheets = [t.get(f"{{{_ODS_TABLE_NS}}}name", f"Sheet{i+1}") for i, t in enumerate(tables)]

        sheet_idx = 0
        if sheet_name:
            if sheet_name.isdigit():
                sheet_idx = int(sheet_name)
            else:
                for i, name in enumerate(sheets):
                    if name.lower() == sheet_name.lower():
                        sheet_idx = i
                        break

        if sheet_idx >= len(tables):
            return {"error": f"Sheet index {sheet_idx} out of range"}

        table = tables[sheet_idx]
        rows = []
        for row_el in table.findall(f"{{{_ODS_TABLE_NS}}}table-row"):
            row = []
            for cell in row_el.findall(f"{{{_ODS_TABLE_NS}}}table-cell"):
                repeat = int(cell.get(f"{{{_ODS_TABLE_NS}}}number-columns-repeated", "1"))
                text = "".join(cell.itertext()).strip()
                row.extend([text] * min(repeat, 100))  # cap repeats
            # Trim trailing empty cells
            while row and not row[-1]:
                row.pop()
            if row:
                rows.append(row)

        return {"sheets": sheets, "rows": rows, "sheet_name": sheets[sheet_idx]}

    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as e:
        return {"error": f"Error reading ods: {e}"}


def _read_csv(path: Path) -> dict:
    """Read CSV file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"error": str(e)}

    # Detect delimiter
    sniffer = csv.Sniffer()
    try:
        dialect = sniffer.sniff(text[:4096])
    except csv.Error:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(text), dialect)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    return {"sheets": ["CSV"], "rows": rows, "sheet_name": "CSV"}


READERS = {
    ".xlsx": _read_xlsx,
    ".ods": _read_ods,
    ".csv": _read_csv,
}


def _format_table(headers: list, rows: list) -> str:
    """Format rows as aligned text table."""
    if not rows:
        return "(no data)"

    # Calculate column widths
    all_rows = [headers] + rows if headers else rows
    n_cols = max(len(r) for r in all_rows)
    widths = [0] * n_cols
    for row in all_rows:
        for i, cell in enumerate(row):
            if i < n_cols:
                widths[i] = max(widths[i], min(len(str(cell)), 30))

    def fmt_row(row):
        parts = []
        for i in range(n_cols):
            val = str(row[i]) if i < len(row) else ""
            if len(val) > 30:
                val = val[:27] + "..."
            parts.append(val.ljust(widths[i]))
        return " | ".join(parts)

    lines = []
    if headers:
        lines.append(fmt_row(headers))
        lines.append("-+-".join("-" * w for w in widths))
    for row in rows:
        lines.append(fmt_row(row))
    return "\n".join(lines)


def execute(args: dict, ctx) -> str:
    path = ctx.sandbox.validate(args["path"])
    sheet_name = args.get("sheet")
    offset = args.get("offset", 0)
    limit = args.get("limit", 30)

    if not path.exists():
        return f"Error: file not found: {args['path']}"

    ext = path.suffix.lower()
    reader = READERS.get(ext)
    if not reader:
        supported = ", ".join(READERS.keys())
        return f"Error: unsupported format '{ext}'. Supported: {supported}"

    if ext == ".csv":
        data = reader(path)
    else:
        data = reader(path, sheet_name)

    if "error" in data:
        return data["error"]

    sheets = data["sheets"]
    rows = data["rows"]
    current_sheet = data.get("sheet_name", "Sheet1")

    if not rows:
        return f"Sheet '{current_sheet}' is empty"

    # First row is header
    headers = rows[0] if rows else []
    data_rows = rows[1:]
    total = len(data_rows)

    # Metadata
    size_kb = path.stat().st_size / 1024
    meta_parts = [f"[{ext} spreadsheet, {size_kb:.0f}KB]"]
    if len(sheets) > 1:
        meta_parts.append(f"Sheets: {', '.join(sheets)}")
    meta_parts.append(f"Sheet: {current_sheet} ({total} rows, {len(headers)} columns)")
    meta = "\n".join(meta_parts)

    # Paginate
    end = min(offset + limit, total)
    if offset >= total and total > 0:
        return f"{meta}\nError: offset {offset} is beyond end ({total} rows)"

    page_rows = data_rows[offset:end]
    table = _format_table(headers, page_rows)

    return f"{meta}\n\n{table}\n\n[Rows {offset + 1}-{end} of {total}]"
