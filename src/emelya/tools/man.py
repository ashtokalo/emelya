"""Man tool — show detailed help for any tool."""

from pathlib import Path

DEFINITION = {
    "type": "function",
    "function": {
        "name": "man",
        "description": "Show detailed help for a tool: parameters, defaults, examples",
        "parameters": {
            "type": "object",
            "properties": {
                "tool": {
                    "type": "string",
                    "description": "Tool name",
                },
            },
            "required": ["tool"],
        },
    },
}

_TOOLS_DIR = Path(__file__).parent / "man"


def execute(args: dict, ctx) -> str:
    name = args.get("tool", "")
    if not name:
        return "Error: tool name is required"

    # Look for .md file next to tool .py files
    md_path = _TOOLS_DIR / f"{name}.md"
    if not md_path.exists():
        # Try with _tool suffix (glob_tool.py → glob_tool.md or glob.md)
        for candidate in _TOOLS_DIR.glob("*.md"):
            if candidate.stem == name or candidate.stem.replace("_tool", "") == name:
                md_path = candidate
                break

    if not md_path.exists():
        # List available tools
        available = sorted(
            p.stem.replace("_tool", "")
            for p in _TOOLS_DIR.glob("*.md")
        )
        return f"No manual for '{name}'. Available: {', '.join(available)}"

    try:
        return md_path.read_text(encoding="utf-8").strip()
    except Exception as e:
        return f"Error reading manual: {e}"
