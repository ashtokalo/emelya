"""Load tools — dynamically load additional tool categories."""

DEFINITION = {
    "type": "function",
    "function": {
        "name": "load_tools",
        "description": "Load additional tools by category.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Category to load",
                },
            },
            "required": ["category"],
        },
    },
}


def execute(args: dict, ctx) -> str:
    category = args.get("category", "")
    if not category:
        return "Error: category is required"
    if not ctx.tool_registry:
        return "Error: tool registry not available"
    return ctx.tool_registry.load_category(category)
