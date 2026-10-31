"""Hex write tool — write/patch binary files using hex data."""

CATEGORY = "binary"

from pathlib import Path

DEFINITION = {
    "type": "function",
    "function": {
        "name": "hex_write",
        "description": "Write hex bytes to a binary file. Can create new files or patch existing ones at a specific offset.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path",
                },
                "hex_data": {
                    "type": "string",
                    "description": "Hex string to write (e.g. '4d5a9000' or '4d 5a 90 00')",
                },
                "offset": {
                    "type": "integer",
                    "description": "Byte offset to write at (default: 0). For existing files, patches in place. For new files, pads with zeros.",
                },
            },
            "required": ["path", "hex_data"],
        },
    },
}


def _parse_hex(hex_str: str) -> bytes:
    """Parse hex string (with or without spaces) to bytes."""
    cleaned = hex_str.replace(" ", "").replace("\n", "").replace("0x", "").replace(",", "")
    if len(cleaned) % 2 != 0:
        raise ValueError("Hex string must have even number of digits")
    return bytes.fromhex(cleaned)


def execute(args: dict, ctx) -> str:
    path = ctx.sandbox.validate(args["path"])
    hex_data = args.get("hex_data", "")
    offset = args.get("offset", 0)

    if not hex_data:
        return "Error: hex_data is required"

    try:
        data = _parse_hex(hex_data)
    except ValueError as e:
        return f"Error: invalid hex data: {e}"

    if ctx.confirm and path.exists():
        if not ctx.confirm(f"Patch {args['path']}", f"{len(data)} bytes at offset {offset:#x}"):
            return ctx.cancellation_result()

    ctx.backup_file(path)

    if path.exists():
        # Patch existing file
        try:
            content = bytearray(path.read_bytes())
            # Extend if needed
            if offset + len(data) > len(content):
                content.extend(b"\x00" * (offset + len(data) - len(content)))
            content[offset:offset + len(data)] = data
            path.write_bytes(bytes(content))
            return f"Patched {len(data)} bytes at offset {offset:#x} in {args['path']}"
        except OSError as e:
            return f"Error: {e}"
    else:
        # Create new file
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            content = b"\x00" * offset + data
            path.write_bytes(content)
            return f"Created {args['path']} ({len(content)} bytes)"
        except OSError as e:
            return f"Error: {e}"
