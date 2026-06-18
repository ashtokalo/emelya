"""Write tool — create or overwrite files."""

DEFINITION = {
    "type": "function",
    "function": {
        "name": "write",
        "description": (
            "Create a new file or overwrite an existing file. "
            "You MUST read the file first before overwriting. "
            "Use edit for partial changes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path (relative to working directory)",
                },
                "content": {
                    "type": "string",
                    "description": "Complete file content",
                },
            },
            "required": ["path", "content"],
        },
    },
}


def execute(args: dict, ctx) -> str:
    path = ctx.sandbox.validate(args["path"])
    content = args["content"]

    if path.exists():
        if str(path) not in ctx.files_read:
            return (
                "Error: file exists but was not read first. "
                "Use read to see current content before overwriting."
            )
        if ctx.confirm and not ctx.confirm(
            f"Overwrite {args['path']}",
            f"{len(content.splitlines())} lines",
        ):
            return "Cancelled by user"

    # Create parent directories
    path.parent.mkdir(parents=True, exist_ok=True)

    ctx.backup_file(path)
    path.write_text(content, encoding="utf-8")
    n_lines = len(content.splitlines())
    return f"Written {n_lines} lines to {args['path']}"
