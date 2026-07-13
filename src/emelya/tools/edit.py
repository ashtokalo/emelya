"""Edit tool — find and replace text in files."""

DEFINITION = {
    "type": "function",
    "function": {
        "name": "edit",
        "description": (
            "Find and replace exact text in a file. "
            "The old_string must match exactly (including whitespace and indentation). "
            "You MUST read the file first. "
            "Prefer small edits under 30 lines. For larger changes use write."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path (relative to working directory)",
                },
                "old_string": {
                    "type": "string",
                    "description": "Exact text to find (must be unique in the file)",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement text",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences (default: false)",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
}


def execute(args: dict, ctx) -> str:
    path = ctx.sandbox.validate(args["path"])
    old_string = args["old_string"]
    new_string = args["new_string"]
    replace_all = args.get("replace_all", False)

    if not path.exists():
        hint = ctx.sandbox.suggest_path(args["path"])
        if hint:
            return f"Error: file not found: {args['path']}. Did you mean: {hint}?"
        return f"Error: file not found: {args['path']}"

    if str(path) not in ctx.files_read:
        return (
            "Error: file was not read first. "
            "Use read to see current content before editing."
        )

    # Read current content
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="latin-1")

    count = content.count(old_string)

    if count == 0:
        return (
            "Error: old_string not found in file. "
            "Make sure it matches exactly, including whitespace."
        )

    if count > 1 and not replace_all:
        return (
            f"Error: found {count} occurrences. "
            "Provide more context to make old_string unique, or use replace_all."
        )

    # Confirm with user
    if ctx.confirm:
        detail = f"{count} occurrence(s)"
        if not ctx.confirm(f"Edit {args['path']}", detail):
            return "Cancelled by user"

    # PreWrite hook — may block the edit
    hook_runner = getattr(ctx, "hook_runner", None)
    if hook_runner:
        hr = hook_runner.run("PreWrite", {
            "path": str(path),
            "old_string": old_string,
            "new_string": new_string,
            "tool": "edit",
        })
        if hr.block:
            return f"Blocked by hook: {hr.reason or hr.message or 'hook'}"

    ctx.backup_file(path)

    if replace_all:
        new_content = content.replace(old_string, new_string)
    else:
        new_content = content.replace(old_string, new_string, 1)

    path.write_text(new_content, encoding="utf-8")
    return f"Replaced {count} occurrence(s) in {args['path']}"
