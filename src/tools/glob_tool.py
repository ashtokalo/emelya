"""Glob tool — find files by name pattern."""

from pathlib import Path

DEFINITION = {
    "type": "function",
    "function": {
        "name": "glob",
        "description": "Find files by name pattern. Supports ** for recursive matching.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern (e.g. '**/*.py', 'src/**/*.js', '*.md')",
                },
                "path": {
                    "type": "string",
                    "description": "Base directory (default: working directory)",
                },
            },
            "required": ["pattern"],
        },
    },
}

MAX_RESULTS = 500
EXCLUDED_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}


def _should_skip(p: Path) -> bool:
    """Check if path contains excluded directory."""
    for part in p.parts:
        if part in EXCLUDED_DIRS:
            return True
    return False


def execute(args: dict, ctx) -> str:
    pattern = args["pattern"]

    if "path" in args and args["path"]:
        base = ctx.sandbox.validate(args["path"])
    else:
        base = ctx.work_dir

    if not base.is_dir():
        return f"Error: '{args.get('path', '.')}' is not a directory"

    matches = []
    total = 0
    for p in sorted(base.glob(pattern)):
        if _should_skip(p.relative_to(ctx.work_dir)):
            continue
        # Skip hidden files unless pattern explicitly starts with .
        if not pattern.startswith(".") and p.name.startswith("."):
            continue
        total += 1
        if total <= MAX_RESULTS:
            try:
                rel = p.relative_to(ctx.work_dir)
            except ValueError:
                rel = p
            matches.append(str(rel))

    if not matches:
        return "No matches found"

    result = "\n".join(matches)
    if total > MAX_RESULTS:
        result += f"\n[truncated, {total} total matches]"
    return result
