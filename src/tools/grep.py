"""Grep tool — search file contents with regex."""

import os
import re
from pathlib import Path

DEFINITION = {
    "type": "function",
    "function": {
        "name": "grep",
        "description": "Search file contents using regular expressions.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression pattern",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search (default: working directory)",
                },
                "include": {
                    "type": "string",
                    "description": "Glob pattern to filter files (e.g. '*.py')",
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "Case-insensitive search (default: false)",
                },
                "context": {
                    "type": "integer",
                    "description": "Number of context lines before and after match (default: 0)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of matches (default: 50)",
                },
            },
            "required": ["pattern"],
        },
    },
}

EXCLUDED_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}


def _is_binary(path, check_bytes=8192):
    try:
        with open(path, "rb") as f:
            return b"\x00" in f.read(check_bytes)
    except OSError:
        return True


def _collect_files(base: Path, include: str = None) -> list:
    """Collect files to search, respecting exclusions."""
    if base.is_file():
        return [base]

    files = []
    for root, dirs, filenames in os.walk(base):
        # Prune excluded dirs
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith(".")]

        for fname in filenames:
            if fname.startswith("."):
                continue
            if include:
                from fnmatch import fnmatch
                if not fnmatch(fname, include):
                    continue
            files.append(Path(root) / fname)
    return sorted(files)


def execute(args: dict, ctx) -> str:
    pattern_str = args["pattern"]
    ignore_case = args.get("ignore_case", False)
    context_lines = args.get("context", 0)
    max_results = args.get("max_results", 50)
    include = args.get("include")

    flags = re.IGNORECASE if ignore_case else 0
    try:
        regex = re.compile(pattern_str, flags)
    except re.error as e:
        return f"Error: invalid regex: {e}"

    if "path" in args and args["path"]:
        base = ctx.sandbox.validate(args["path"])
        if not base.exists():
            hint = ctx.sandbox.suggest_path(args["path"])
            if hint:
                return f"Error: path not found: {args['path']}. Did you mean: {hint}?"
            return f"Error: path not found: {args['path']}"
    else:
        base = ctx.work_dir

    files = _collect_files(base, include)
    matches = []
    total_matches = 0

    for fpath in files:
        if _is_binary(fpath):
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        lines = text.splitlines()
        try:
            rel = fpath.relative_to(ctx.work_dir)
        except ValueError:
            rel = fpath

        for i, line in enumerate(lines):
            if regex.search(line):
                total_matches += 1
                if total_matches > max_results:
                    continue  # keep counting but don't collect

                if context_lines > 0:
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    block = []
                    for j in range(start, end):
                        prefix = ">" if j == i else " "
                        block.append(f"{rel}:{j + 1}:{prefix} {lines[j]}")
                    matches.append("\n".join(block))
                else:
                    matches.append(f"{rel}:{i + 1}: {line}")

    if not matches:
        return "No matches found"

    separator = "\n--\n" if context_lines > 0 else "\n"
    result = separator.join(matches)

    if total_matches > max_results:
        result += f"\n[truncated, showing {max_results} of {total_matches} matches]"

    return result
