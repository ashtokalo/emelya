"""Plan step tool — adds a step to the current plan (used by planner agent)."""

CATEGORY = "planning"

DEFINITION = {
    "type": "function",
    "function": {
        "name": "plan_step",
        "description": "Add a step to the execution plan",
        "parameters": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "What this step does",
                },
                "agent": {
                    "type": "string",
                    "description": "Agent name to run (e.g. explore_dir, review_file)",
                },
                "tool": {
                    "type": "string",
                    "description": "Tool name if not using agent (e.g. read, write, grep)",
                },
                "args": {
                    "type": "object",
                    "description": "Arguments for the agent/tool",
                },
                "output": {
                    "type": "string",
                    "description": "Filename to save result (e.g. 'structure.md')",
                },
                "uses": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Output files from previous steps this step needs",
                },
            },
            "required": ["description"],
        },
    },
}

# Collected steps — reset by plan executor before planner runs
_steps = []


def reset():
    """Clear collected steps."""
    _steps.clear()


def get_steps():
    """Return collected steps."""
    return list(_steps)


def execute(args: dict, ctx) -> str:
    step = {
        "description": args.get("description", ""),
        "agent": args.get("agent"),
        "tool": args.get("tool"),
        "args": args.get("args", {}),
        "output": args.get("output"),
        "uses": args.get("uses", []),
    }
    _steps.append(step)
    n = len(_steps)
    return f"Step {n} added: {step['description']}"
