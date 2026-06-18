"""Read tool — read text files with line numbers."""

from emelya.tools import is_binary

DEFINITION = {
    "type": "function",
    "function": {
        "name": "read",
        "description": "Read a text file. Returns numbered lines. Read the file before editing it.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path (relative to working directory)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Start from this line number (1-based, default: 1)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of lines to read (default: 200)",
                },
            },
            "required": ["path"],
        },
    },
}


def execute(args: dict, ctx) -> str:
    path = ctx.sandbox.validate(args["path"])

    if not path.exists():
        hint = ctx.sandbox.suggest_path(args["path"])
        if hint:
            return f"Error: file not found: {args['path']}. Did you mean: {hint}?"
        return f"Error: file not found: {args['path']}"
    if path.is_dir():
        return f"Error: '{args['path']}' is a directory, not a file"

    if is_binary(path):
        return (
            f"Error: '{args['path']}' is a binary file. "
            "Use bash with xxd or hexdump to inspect binary content."
        )

    offset = max(1, args.get("offset", 1))
    limit = args.get("limit", 200)

    # Try UTF-8, fallback to latin-1
    for encoding in ("utf-8", "latin-1"):
        try:
            text = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return f"Error: cannot decode '{args['path']}'"

    lines = text.splitlines(keepends=True)
    total = len(lines)

    start = offset - 1  # 0-based
    end = min(start + limit, total)

    if start >= total:
        return f"Error: offset {offset} is beyond end of file ({total} lines)"

    result_lines = []
    for i in range(start, end):
        line_content = lines[i].rstrip("\n").rstrip("\r")
        result_lines.append(f"{i + 1}\t{line_content}")

    remaining = total - end
    if remaining > 0:
        result_lines.append(f"[Lines {start + 1}-{end} of {total}, {remaining} more — use offset={end + 1} to continue]")
    else:
        result_lines.append(f"[Lines {start + 1}-{end} of {total}]")

    ctx.files_read.add(str(path))
    return "\n".join(result_lines)
