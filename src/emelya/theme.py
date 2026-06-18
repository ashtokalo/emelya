"""Color theme for CLI output.

Uses basic ANSI codes (works in all modern terminals).
Respects NO_COLOR env variable.

Usage:
    from emelya.theme import C
    print(f"{C.user}You:{C.reset} text")
"""

import os

_no_color = os.environ.get("NO_COLOR") is not None


class _Theme:
    """ANSI color codes for UI elements."""

    reset = "\033[0m"
    bold = "\033[1m"
    dim = "\033[2m"
    italic = "\033[3m"

    # User input
    user_prompt = "\033[1;32m"     # bold green — "You:"
    user_text = "\033[0m"          # default — user's message

    # Agent response
    agent_prefix = "\033[1;36m"    # bold cyan — "Emelya (model, tok, time):"
    agent_text = "\033[0m"         # default — response text

    # Tool calls (intermediate operations)
    tool_call = "\033[33m"         # yellow — tool name and args
    tool_result = "\033[2m"        # dim — tool output
    tool_error = "\033[31m"        # red — errors

    # System messages
    info = "\033[36m"              # cyan — info, status
    warning = "\033[33m"           # yellow — warnings
    error = "\033[1;31m"           # bold red — errors
    success = "\033[32m"           # green — success
    muted = "\033[2m"              # dim — secondary info

    # Spinner
    spinner = "\033[33m"           # yellow

    # Commands
    command = "\033[36m"           # cyan — /commands output


C = _Theme()

if _no_color:
    for attr in dir(C):
        if not attr.startswith("_"):
            setattr(C, attr, "")
