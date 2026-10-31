# bash

Execute a shell command and return its output.

## Parameters

- `command` (string, required) — The shell command to execute.
- `description` (string, optional) — Human-readable description shown to the user for confirmation.
- `timeout` (int, optional) — Maximum execution time in seconds. Default: 30. Max: 300.

## Examples

bash(command="git status", description="Check git status")
bash(command="python3 test.py", timeout=60)
bash(command="ls -la /tmp")

## Notes

- Requires user confirmation before execution.
- Output is truncated at 10000 characters.
- Use for git, running scripts, and system commands.
- Prefer `read`, `edit`, `glob_tool`, and `grep` for file operations instead of shell equivalents.
