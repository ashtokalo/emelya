# hex_write

Write or patch binary data in a file.

## Parameters

- `path` (string, required) — Path to the file to write or patch.
- `hex_data` (string, required) — Hex-encoded bytes to write, e.g. `"4d5a9000"`.
- `offset` (int, optional) — Byte offset at which to write. Default: 0.

## Examples

hex_write(path="output.bin", hex_data="4d5a9000")
hex_write(path="firmware.bin", hex_data="ff00", offset=1024)

## Notes

- Patches an existing file at the given offset, or creates a new file if it does not exist.
- Supports undo.
