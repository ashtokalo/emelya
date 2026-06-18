# plan_mode

Enter/exit planning mode. In plan mode write/edit/bash are blocked — only read-only tools work.

## Parameters

- `action` (string, required) — "enter" or "exit"
- `plan` (string) — The plan text (required for exit)

## Examples

plan_mode(action="enter")
# ... research with read, glob, grep, man ...
plan_mode(action="exit", plan="1. Create main.py with bot logic\n2. Add config.py\n3. Write tests")

## Notes

- Use for complex tasks to plan before executing.
- In plan mode: read, glob, grep, list_dir, web_fetch, web_search, man, skill, agent work normally.
- In plan mode: write, edit, bash are BLOCKED.
- When exiting, the plan is shown to the user for approval.
- If rejected, modify the plan and try again.
