"""REPL /command handlers assembled into CommandsMixin."""

from emelya.commands.agent import _cmd_agent
from emelya.commands.branch import _cmd_branch
from emelya.commands.clear import _cmd_clear
from emelya.commands.compact import _cmd_compact
from emelya.commands.context import _cmd_context
from emelya.commands.custom import (
    _cmd_list_commands,
    _extract_cmd_desc,
    _run_custom_command,
    _scan_custom_commands,
)
from emelya.commands.drop import _cmd_drop
from emelya.commands.export import _cmd_export
from emelya.commands.help import _print_banner, _print_help
from emelya.commands.history import _print_full_context
from emelya.commands.model import _cmd_model
from emelya.commands.rename import _cmd_rename
from emelya.commands.resume import _cmd_resume
from emelya.commands.skill import _cmd_skill
from emelya.commands.stats import _print_stats
from emelya.commands.tools import _cmd_tools
from emelya.commands.man import _cmd_man
from emelya.commands.undo import _cmd_undo


class CommandsMixin:
    """Command handling and UI methods for REPL.

    Expects self to have: config, context, registry, llm, log,
    tool_ctx, session_name, sessions_dir, app_dir, _model_display,
    input_history, usage_tracker, and methods from ModelMixin and MCPMixin.
    """

    _print_help = _print_help
    _print_banner = _print_banner
    _print_stats = _print_stats
    _print_full_context = _print_full_context
    _cmd_clear = _cmd_clear
    _cmd_undo = _cmd_undo
    _cmd_compact = _cmd_compact
    _cmd_drop = _cmd_drop
    _cmd_rename = _cmd_rename
    _cmd_resume = _cmd_resume
    _cmd_branch = _cmd_branch
    _cmd_export = _cmd_export
    _cmd_model = _cmd_model
    _cmd_skill = _cmd_skill
    _cmd_agent = _cmd_agent
    _cmd_tools = _cmd_tools
    _cmd_context = _cmd_context
    _cmd_man = _cmd_man
    _cmd_list_commands = _cmd_list_commands
    _scan_custom_commands = _scan_custom_commands
    _run_custom_command = _run_custom_command

    def _handle_command(self, command: str) -> bool:
        """Handle /commands. Returns True if handled."""
        if command == "/help":
            self._print_help()
            return True

        if command == "/clear":
            self._cmd_clear()
            return True

        if command == "/undo":
            self._cmd_undo()
            return True

        if command == "/compact":
            self._cmd_compact()
            return True

        if command == "/tools":
            self._cmd_tools()
            return True

        if command == "/context":
            self._cmd_context()
            return True

        if command == "/history":
            self._print_full_context()
            return True

        if command == "/stats":
            self._print_stats()
            return True

        if command == "/commands":
            self._cmd_list_commands()
            return True

        parts = command.split(None, 1)
        cmd = parts[0]
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/drop":
            self._cmd_drop(arg)
            return True

        if cmd == "/rename":
            self._cmd_rename(arg)
            return True

        if cmd == "/resume":
            self._cmd_resume(arg)
            return True

        if cmd == "/branch":
            self._cmd_branch(arg)
            return True

        if cmd == "/export":
            self._cmd_export(arg)
            return True

        if cmd == "/mcp":
            self._cmd_mcp(arg)
            return True

        if cmd in ("/man", "/help"):
            self._cmd_man(arg)
            return True

        if cmd == "/model":
            self._cmd_model(arg)
            return True

        if cmd == "/skill":
            self._cmd_skill(arg)
            return True

        if cmd == "/agent":
            self._cmd_agent(arg)
            return True

        if cmd in self._custom_commands:
            self._run_custom_command(cmd[1:], arg)
            return True

        return False
