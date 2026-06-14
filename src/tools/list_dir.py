"""List directory tool — list files with metadata."""

import os
from datetime import datetime
from pathlib import Path

DEFINITION = {
    "type": "function",
    "function": {
        "name": "list_dir",
        "description": "List directory contents with file sizes and types. Use this instead of bash ls.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path (default: working directory)",
                },
                "show_hidden": {
                    "type": "boolean",
                    "description": "Show hidden files (default: false)",
                },
            },
        },
    },
}


def _format_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def execute(args: dict, ctx) -> str:
    path_arg = args.get("path", "")
    show_hidden = args.get("show_hidden", False)

    if path_arg:
        base = ctx.sandbox.validate(path_arg)
    else:
        base = ctx.work_dir

    if not base.is_dir():
        return f"Error: '{path_arg or '.'}' is not a directory"

    entries = []
    try:
        items = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return f"Error: permission denied: {path_arg or '.'}"

    for item in items:
        name = item.name
        if not show_hidden and name.startswith("."):
            continue

        try:
            stat = item.stat()
        except OSError:
            continue

        if item.is_dir():
            entries.append(f"  {name}/")
        else:
            size = _format_size(stat.st_size)
            entries.append(f"  {name}  ({size})")

    if not entries:
        return "(empty directory)"

    # Summary
    dirs = sum(1 for e in entries if e.rstrip().endswith("/"))
    files = len(entries) - dirs
    header = path_arg or "."
    entries.append(f"[{files} files, {dirs} dirs]")

    return "\n".join(entries)
