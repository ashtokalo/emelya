# hex_read

Read a file as a hex dump.

## Parameters

- `path` (string, required) — Path to the file to read.
- `offset` (int, optional) — Byte offset to start reading from. Default: 0.
- `length` (int, optional) — Number of bytes to read. Default: 256. Max: 4096.

## Examples

hex_read(path="firmware.bin")
hex_read(path="data.bin", offset=512, length=128)

## Notes

- Output format: `offset | hex bytes | ascii representation`.
