# sheet_read

Read data from spreadsheet files.

## Parameters

- `path` (string, required) — Path to the spreadsheet file.
- `sheet` (string, optional) — Sheet name or index to read. Defaults to the first sheet.
- `offset` (int, optional) — Starting row number. Default: 0.
- `limit` (int, optional) — Maximum number of rows to return. Default: 30.

## Examples

sheet_read(path="data.xlsx")
sheet_read(path="data.xlsx", sheet="Summary", offset=10, limit=50)
sheet_read(path="report.csv", limit=100)

## Notes

- xlsx and ods files are read via the standard library.
- CSV files have their delimiter auto-detected.
