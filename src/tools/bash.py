"""Bash tool — execute shell commands with confirmation."""

import subprocess

DEFINITION = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": (
            "Execute a shell command. Use for: ls, git, running scripts, "
            "and any operation not covered by other tools. "
            "Prefer read/write/edit/glob/grep for file operations."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "description": {
                    "type": "string",
                    "description": "What this command does (shown to user)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 30, max: 300)",
                },
            },
            "required": ["command"],
        },
    },
}

# Commands considered read-only (no confirmation in confirm_write mode)
READONLY_COMMANDS = {
    "ls", "cat", "grep", "find", "pwd", "which", "file", "wc",
    "head", "tail", "sort", "uniq", "du", "df", "echo", "env",
    "date", "uname", "whoami", "hostname", "id", "tree",
    "git status", "git log", "git diff", "git branch", "git show",
    "git remote", "git tag",
}

MAX_OUTPUT = 10000


def _is_readonly(command: str) -> bool:
    """Check if command is read-only."""
    cmd = command.strip()
    for ro in READONLY_COMMANDS:
        if cmd == ro or cmd.startswith(ro + " "):
            return True
    return False


def _needs_confirm(command: str, confirm_mode: str) -> bool:
    """Determine if command needs user confirmation."""
    if confirm_mode == "no_confirm":
        return False
    if confirm_mode == "confirm_write":
        return not _is_readonly(command)
    return True  # confirm_all


def execute(args: dict, ctx) -> str:
    command = args.get("command", "")
    description = args.get("description", command)
    timeout = min(args.get("timeout", 30), 300)

    if not command.strip():
        return "Error: empty command"

    confirm_mode = getattr(ctx.config, "confirm_mode", "confirm_all") if ctx.config else "confirm_all"

    if _needs_confirm(command, confirm_mode) and ctx.confirm:
        if not ctx.confirm(f"bash: {description}", f"$ {command}"):
            return "Cancelled by user"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(ctx.work_dir),
        )
        output = result.stdout
        if result.stderr:
            output += ("\n" if output else "") + result.stderr

        output = output.strip()

        if len(output) > MAX_OUTPUT:
            output = output[:MAX_OUTPUT] + "\n[truncated]"

        if result.returncode != 0:
            return f"Exit code {result.returncode}\n{output}" if output else f"Exit code {result.returncode}"

        return output if output else "(no output)"

    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"
