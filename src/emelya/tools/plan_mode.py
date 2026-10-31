"""Plan mode tools — structured planning before execution."""

import sys

DEFINITION = {
    "type": "function",
    "function": {
        "name": "plan_mode",
        "description": "Enter/exit planning mode. In plan mode only read-only tools work. Use for complex tasks.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["enter", "exit"],
                    "description": "enter or exit",
                },
                "plan": {
                    "type": "string",
                    "description": "The plan text (required for exit)",
                },
            },
            "required": ["action"],
        },
    },
}

# State
_in_plan_mode = False
_plan_text = ""


def is_active():
    return _in_plan_mode


def get_plan():
    return _plan_text


def reset():
    global _in_plan_mode, _plan_text
    _in_plan_mode = False
    _plan_text = ""


# Tools blocked in plan mode
BLOCKED_TOOLS = {"edit", "bash"}


def execute(args: dict, ctx) -> str:
    global _in_plan_mode, _plan_text

    try:
        from emelya.theme import C
    except ImportError:
        class C:
            info = warning = muted = reset = ""

    action = args.get("action", "")

    if action == "enter":
        _in_plan_mode = True
        _plan_text = ""
        print(f"\n  {C.info}📋 Plan mode: ON (read-only — edit/bash blocked){C.reset}")
        return (
            "Plan mode activated. You can only use read-only tools: "
            "read, glob, grep, list_dir, web_fetch, web_search, man, skill, agent. "
            "edit/bash are blocked. "
            "When ready, call plan_mode(action='exit', plan='your plan here')."
        )

    if action == "exit":
        plan = args.get("plan", "")
        if not plan:
            return "Error: plan text is required to exit plan mode"
        _in_plan_mode = False
        _plan_text = plan
        print(f"\n  {C.info}📋 Plan mode: OFF{C.reset}")
        print(f"\n  {C.info}Proposed plan:{C.reset}")
        for line in plan.split("\n"):
            print(f"  {line}")
        return f"Plan submitted for approval. The plan will be shown to the user."

    return f"Error: unknown action '{action}'. Use: enter, exit"
