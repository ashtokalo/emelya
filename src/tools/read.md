# read

Read a text file and display its contents with line numbers.

## Parameters

- `path` (string, required) — Path to the file to read.
- `offset` (int, optional) — Line number to start reading from. Default: 1.
- `limit` (int, optional) — Maximum number of lines to return. Default: 200.

## Examples

read(path="src/main.py")
read(path="src/main.py", offset=50, limit=100)

## Notes

- Binary files are rejected; use `hex_read` for binary content.
- Adds the file to read tracking, which is required before using `edit` or `write` on it.
- When the file is longer than the returned range, output ends with: `[Lines X-Y of Z, N more — use offset=Y+1]`
