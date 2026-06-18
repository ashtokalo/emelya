"""Hex read tool — read binary files as hex dump with ASCII preview."""

CATEGORY = "binary"

from pathlib import Path

DEFINITION = {
    "type": "function",
    "function": {
        "name": "hex_read",
        "description": "Read binary file as hex dump with ASCII preview. Use for inspecting binary formats, headers, magic bytes.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path",
                },
                "offset": {
                    "type": "integer",
                    "description": "Byte offset to start from (default: 0)",
                },
                "length": {
                    "type": "integer",
                    "description": "Number of bytes to read (default: 256, max: 4096)",
                },
            },
            "required": ["path"],
        },
    },
}

MAX_LENGTH = 4096


def _hex_dump(data: bytes, base_offset: int = 0) -> str:
    """Format bytes as hex dump: offset | hex | ascii."""
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        addr = f"{base_offset + i:08x}"
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        hex_part = hex_part.ljust(48)  # 16 bytes * 3 chars
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{addr}  {hex_part}  |{ascii_part}|")
    return "\n".join(lines)


def execute(args: dict, ctx) -> str:
    path = ctx.sandbox.validate(args["path"])
    offset = args.get("offset", 0)
    length = min(args.get("length", 256), MAX_LENGTH)

    if not path.exists():
        return f"Error: file not found: {args['path']}"
    if path.is_dir():
        return f"Error: '{args['path']}' is a directory"

    file_size = path.stat().st_size

    if offset >= file_size:
        return f"Error: offset {offset} is beyond end of file ({file_size} bytes)"

    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read(length)
    except OSError as e:
        return f"Error: {e}"

    actual_end = offset + len(data)
    header = f"[{args['path']}, {file_size} bytes, showing {offset:#x}-{actual_end:#x}]"
    dump = _hex_dump(data, offset)

    return f"{header}\n{dump}"
