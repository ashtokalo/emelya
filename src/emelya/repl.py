"""REPL — main interactive loop with agent cycle."""

import json
import html
import platform
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from emelya.config import Config
from emelya.i18n import t
from emelya.theme import C
from emelya.context import Context
from emelya.input_history import InputHistory
from emelya.line_editor import LineEditor, strip_markers
from emelya.llm import LLMClient, LLMError
from emelya.log import SessionLog, UsageTracker
from emelya.memory import FileMemoryStore
from emelya.sandbox import Sandbox
from emelya.task_store import FileTaskStore
from emelya.terminal_markdown import render_terminal_markdown, TerminalMarkdownStream
from emelya.tools import ToolContext, ToolRegistry
from emelya.tools.bash import _command_family
from emelya.tools.skill import scan_skills
from emelya.tools.agent import scan_agents, _detect_task_language

from emelya.models import ModelMixin
from emelya.mcp_manager import MCPMixin
from emelya.commands import CommandsMixin
from emelya.hooks import HookRunner


def _first_heading(text: str) -> str:
    """Return text of first # heading in a markdown file, or empty string."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _resource_root() -> Path:
    """Return bundled resource root for source and installed layouts."""
    installed = Path(sys.prefix) / "share" / "emelya"
    if installed.is_dir():
        return installed
    return Path(__file__).parent.parent.parent


def _find_git_root(path: Path) -> Path | None:
    """Find the nearest Git worktree root without invoking Git."""
    current = path.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


_EXTERNAL_TOOLS = {"web_search", "web_fetch"}
_MAX_EXTERNAL_CALLS = 6
_MAX_EXTERNAL_FAILURES = 3
_INTERACTIVE_SHELL_COMMANDS = {
    "mc", "vim", "vi", "nvim", "nano", "emacs", "less", "more",
    "top", "htop", "btop", "watch", "tmux", "screen", "ssh", "telnet",
}


def _failed_tool_result(result: str) -> bool:
    """Return whether a tool result is unusable evidence."""
    text = (result or "").strip().lower()
    return text.startswith("error:") or text in {"", "(empty response)"}


class _WaitSpinner:
    """Animated spinner with timer, shown while waiting for LLM response."""

    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    _DELAY = 1.0  # seconds before spinner appears

    def __init__(self, label: str):
        self._label = label
        self._running = False
        self._thread = None
        self._start_time = 0

    def start(self):
        self._running = True
        self._start_time = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        if not self._running:
            return
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None
        # Clear spinner line
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    def _run(self):
        # Wait before showing spinner
        deadline = self._start_time + self._DELAY
        while self._running and time.monotonic() < deadline:
            time.sleep(0.05)
        # Animate
        frame_idx = 0
        hint_shown = False
        while self._running:
            elapsed = time.monotonic() - self._start_time
            frame = self._FRAMES[frame_idx % len(self._FRAMES)]
            hint = "  Ctrl+C to cancel" if elapsed > 30 and not hint_shown else ""
            if elapsed > 30:
                hint_shown = True
            sys.stdout.write(f"\r\033[K  {C.spinner}{frame}{C.reset} {C.muted}{self._label} [{elapsed:.0f}s]{hint}{C.reset}")
            sys.stdout.flush()
            frame_idx += 1
            time.sleep(0.1)


class REPL(ModelMixin, MCPMixin, CommandsMixin):
    """Interactive REPL with tool-calling agent loop."""

    def __init__(self, config: Config):
        self.config = config
        self.context = Context(context_limit=config.context_limit)
        self._external_access_lock = threading.Lock()
        self._last_cancellation_comment = ""
        global_memory = Path.home() / ".emelya" / "memory"
        extra_allowed = [global_memory] if global_memory.is_dir() else []
        self.sandbox = Sandbox(
            config.work_dir,
            extra_allowed=extra_allowed,
            request_access=self._request_external_access,
        )
        self.registry = ToolRegistry()

        src_dir = Path(__file__).parent          # src/emelya/
        self.app_dir = _resource_root()
        # Skills and agents search order: builtin → global → local
        # Later entries override earlier ones for the same name.
        global_emelya = Path.home() / ".emelya"
        local_emelya = config.work_dir / ".emelya"
        self.skills_dirs = [
            self.app_dir / "skills",
            global_emelya / "skills",
            local_emelya / "skills",
        ]
        self.agents_dirs = [
            self.app_dir / "agents",
            global_emelya / "agents",
            local_emelya / "agents",
        ]
        self.sessions_dir = config.work_dir / ".emelya" / "sessions"

        # Models management
        self._models_path = config.work_dir / ".emelya" / "models.json"
        self._models = self._load_models()
        self.active_model = config.model_name or "local"
        self._model_display = self.active_model  # updated from API response
        self._manual_model = False  # True = routing disabled, use self.llm directly
        self.llm = self._create_llm_client()

        # Full conversation log for export (includes tool calls, not just final text)
        self.conversation_log = []
        self._session_command_permissions = set()

        # Session management
        self.session_name = datetime.now().strftime("tmp%Y%m%d%H%M%S")
        self.log = self._create_log()
        self.usage_tracker = UsageTracker(config.work_dir / ".emelya" / "usage.jsonl")
        self.memory_store = FileMemoryStore(
            lambda: self._session_dir() / "memory"
        )
        self.task_store = FileTaskStore(
            lambda: self._session_dir() / "tasks"
        )

        # Tool context shared across calls
        self.tool_ctx = ToolContext(
            work_dir=config.work_dir,
            sandbox=self.sandbox,
            confirm=self._confirm,
            get_cancellation_result=self._get_cancellation_result,
            skills_dirs=self.skills_dirs,
            agents_dirs=self.agents_dirs,
            llm_client=self.llm,
            config=config,
            session_log=self.log,
            usage_tracker=self.usage_tracker,
            get_routed_client=self._resolve_client_for_operation,
            memory_store=self.memory_store,
            task_store=self.task_store,
        )

        # Hooks system
        self.hook_runner = HookRunner(config.work_dir, llm_getter=self._get_llm_for_hook)
        self.tool_ctx.hook_runner = self.hook_runner

        # Load tools
        self.registry.load_builtin(src_dir)
        self.tool_ctx.tool_registry = self.registry

        # Load MCP servers (register as extension categories)
        self._mcp_clients = {}
        self._mcp_startup_info = []  # collected during init, printed after banner
        self._init_mcp()

        # Scan custom commands from .emelya/commands/
        self._custom_commands = self._scan_custom_commands()

        # Update skill/agent tool descriptions with available items
        self._update_dynamic_descriptions()

        # Build system prompt
        self._init_system_prompt()

    def _get_llm_for_hook(self, model_name: str):
        """Return an LLMClient for a hook's prompt call. 'default' uses current client."""
        if model_name == "default":
            return self.llm
        cfg = self._models.get(model_name)
        if cfg:
            try:
                return self._create_llm_client(model_name)
            except Exception:
                pass
        return self.llm

    def _session_dir(self, name: str = None) -> Path:
        return self.sessions_dir / (name or self.session_name)

    def _create_log(self) -> SessionLog:
        log_path = self._session_dir() / "log.txt"
        return SessionLog(log_path)

    # --- Setup ---

    def _update_dynamic_descriptions(self):
        self.registry.update_description(
            "bash",
            "Execute a shell command in the configured working directory "
            f"({self.config.work_dir}). The cwd is already set: never use cd, git -C, "
            "or guessed-path fallback chains. Use relative paths.",
        )
        skills = scan_skills(self.skills_dirs)
        if skills:
            skills_list = ", ".join(f"{k} ({v})" for k, v in skills.items())
            self.registry.update_description(
                "skill",
                f"Load a skill with specialized instructions. Available: {skills_list}",
            )

        agents = scan_agents(self.agents_dirs)
        if agents:
            agents_list = ", ".join(f"{k} ({v})" for k, v in agents.items())
            self.registry.update_description(
                "agent",
                f"Run a sub-agent for a focused task. Returns only the result. Available: {agents_list}",
            )

        extensions = self.registry.get_extension_summary()
        if extensions:
            parts = []
            for cat, tools in extensions.items():
                tool_names = ", ".join(tools.keys())
                # For MCP categories, include server description from config
                if cat.startswith("mcp:"):
                    srv_name = cat[4:]
                    mcp_cfg = getattr(self, "_mcp_config", {})
                    srv_desc = mcp_cfg.get(srv_name, {}).get("description", "")
                    if srv_desc:
                        parts.append(f"{cat}: {srv_desc} — tools: {tool_names}")
                    else:
                        parts.append(f"{cat} ({tool_names})")
                else:
                    parts.append(f"{cat} ({tool_names})")
            self.registry.update_description(
                "load_tools",
                f"Load additional tools by category. Available: {'; '.join(parts)}",
            )

    def _init_system_prompt(self):
        prompt_path = self.app_dir / "prompts" / "system.md"
        try:
            template = prompt_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            template = "You are a helpful AI assistant."

        now = datetime.now()
        prompt = template.format_map({
            "work_dir": str(self.config.work_dir),
            "datetime": now.strftime("%Y-%m-%d %H:%M"),
            "timezone": now.astimezone().tzname() or "UTC",
            "os": platform.platform(),
        })
        git_root = _find_git_root(self.config.work_dir)
        prompt += (
            "\n\nExecution environment (authoritative):"
            f"\n- work_dir/cwd: {self.config.work_dir}"
            "\n- All relative tool and shell paths resolve from work_dir."
            "\n- bash already starts in work_dir; do not use cd, git -C, or probe guessed paths."
        )
        if git_root is not None:
            prompt += f"\n- Git repository root: {git_root}"

        # Append project config if exists (emelya.md)
        if self.config.config_file.exists():
            try:
                custom = self.config.config_file.read_text(encoding="utf-8").strip()
                if custom:
                    prompt += f"\n\nProject instructions ({self.config.config_file.name}):\n{custom}"
            except Exception:
                pass

        # Append local config if exists (.emelya/config.md — personal, not in repo)
        local_config = self.config.work_dir / ".emelya" / "config.md"
        if local_config.exists():
            try:
                local = local_config.read_text(encoding="utf-8").strip()
                if local:
                    prompt += f"\n\nLocal instructions ({local_config.name}):\n{local}"
            except Exception:
                pass

        # Build memory index: global ~/.emelya/memory/ first, then local .emelya/memory/
        index_lines = []
        global_memory_dir = Path.home() / ".emelya" / "memory"
        if global_memory_dir.is_dir():
            for f in sorted(global_memory_dir.glob("*.md")):
                try:
                    content = f.read_text(encoding="utf-8")
                    desc = _first_heading(content) or f.stem
                    index_lines.append(f"- [{desc}]({f})")
                except Exception:
                    pass
        local_memory_dir = self.config.work_dir / ".emelya" / "memory"
        if local_memory_dir.is_dir():
            for f in sorted(local_memory_dir.glob("*.md")):
                try:
                    content = f.read_text(encoding="utf-8")
                    desc = _first_heading(content) or f.stem
                    index_lines.append(f"- [{desc}](.emelya/memory/{f.name})")
                except Exception:
                    pass
        if index_lines:
            index = "\n".join(index_lines)
            prompt += f"\n\nMemory:\n{index}\n\nUse read tool to load specific memory files when relevant to the task."

        self.context.set_system(prompt)

    # --- Agent loop ---

    @staticmethod
    def _notify_attention():
        """Ring the terminal bell before a blocking user decision."""
        if getattr(sys.stdout, "isatty", lambda: False)():
            sys.stdout.write("\a")
            sys.stdout.flush()

    def _request_external_access(self, path: Path) -> str:
        """Ask whether an out-of-sandbox path may be used."""
        # Read-only tools may execute in parallel; never overlap terminal prompts.
        with self._external_access_lock:
            print(f"\n  {C.warning}{t('external_access', path=str(path))}{C.reset}")
            try:
                self._notify_attention()
                answer = input(f"  {C.warning}{t('external_access_choices')}{C.reset} ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return "deny"
        if answer in ("y", "yes", "д", "да"):
            return "once"
        if answer in ("a", "always", "в", "всегда"):
            return "session"
        return "deny"

    def _confirm(self, description: str, detail: str = "") -> bool:
        """Ask user to allow, deny, or deny with model-facing feedback."""
        self._last_cancellation_comment = ""
        if self.config.confirm_mode == "no_confirm":
            return True
        permission = ""
        if description.startswith("bash:") and detail.startswith("$ "):
            permission = _command_family(detail[2:])
            if permission in getattr(self, "_session_command_permissions", set()):
                return True
        try:
            self._notify_attention()
            if permission.startswith("exact:"):
                prompt = "confirm_choices_session_exact"
            else:
                prompt = "confirm_choices_session" if permission else "confirm_choices"
            answer = input(f"  {C.warning}{t(prompt)}{C.reset} ").strip().lower()
            if answer in ("y", "yes", "д", "да"):
                return True
            if permission and answer in ("a", "always", "в", "всегда"):
                if not hasattr(self, "_session_command_permissions"):
                    self._session_command_permissions = set()
                self._session_command_permissions.add(permission)
                return True
            if answer in ("c", "comment", "к", "комментарий"):
                self._last_cancellation_comment = input(
                    f"  {C.warning}{t('cancel_comment_prompt')}{C.reset} "
                ).strip()
            return False
        except (EOFError, KeyboardInterrupt):
            return False

    def _get_cancellation_result(self) -> str:
        base = (
            "Error: operation cancelled by user. "
            "The command was NOT executed and no changes were made."
        )
        if self._last_cancellation_comment:
            return base + " User comment: " + self._last_cancellation_comment
        return base

    def _last_assistant_text(self) -> str:
        """Return the latest model text exactly as stored in conversation."""
        for message in reversed(getattr(self.context, "messages", [])):
            if message.get("role") != "assistant":
                continue
            content = message.get("content")
            if isinstance(content, str) and content:
                return content
            if isinstance(content, list):
                parts = [
                    item.get("text", "") for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
                text = "".join(parts)
                if text:
                    return text
        return ""

    def _run_shell_escape(self, command: str, stdin_text: str | None = None) -> int:
        """Run an explicit user command, optionally feeding model text to stdin."""
        if not command.strip():
            print("  Usage: !<command> or !|<command>")
            return 2
        try:
            try:
                words = shlex.split(command)
            except ValueError:
                words = command.split()
            executable = Path(words[0]).name if words else ""
            interactive = executable in _INTERACTIVE_SHELL_COMMANDS
            if interactive and stdin_text is None:
                completed = subprocess.run(
                    command, shell=True, cwd=str(self.config.work_dir),
                )
                stdout = stderr = ""
            else:
                completed = subprocess.run(
                    command, shell=True, cwd=str(self.config.work_dir),
                    input=stdin_text, capture_output=True, text=True, errors="replace",
                )
                stdout, stderr = completed.stdout or "", completed.stderr or ""
                if stdout:
                    print(stdout, end="" if stdout.endswith("\n") else "\n")
                if stderr:
                    print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr)
            if completed.returncode != 0:
                print(f"  [exit {completed.returncode}]")
            self._record_shell_escape(
                command, completed.returncode, stdout, stderr,
                interactive and stdin_text is None,
            )
            return completed.returncode
        except KeyboardInterrupt:
            print(f"\n  {t('interrupted')}")
            self._record_shell_escape(command, 130, interactive=True)
            return 130
        except OSError as e:
            print(f"  Error: {e}")
            self._record_shell_escape(command, 1, stderr=str(e))
            return 1

    def _record_shell_escape(self, command: str, returncode: int,
                             stdout: str = "", stderr: str = "",
                             interactive: bool = False):
        """Store shell output in memory and only a reference in conversation."""
        memory_id = ""
        store = getattr(self, "memory_store", None)
        if store is not None:
            if interactive:
                output = "Interactive terminal output was not captured."
            else:
                output = ""
                if stdout:
                    output += "STDOUT:\n" + stdout
                if stderr:
                    output += ("\n" if output else "") + "STDERR:\n" + stderr
                if not output:
                    output = "(no output)"
            details = (
                f"Command: {command}\nExit code: {returncode}\n\n{output}"
            )[:199000]
            try:
                record = store.put(
                    summary=f"Shell command: {command[:300]}",
                    kind="artifact",
                    scope="shell",
                    details=details,
                    tags=["shell", "user-command"],
                    status="verified",
                    author="user",
                )
                memory_id = record["id"]
            except (ValueError, OSError, TypeError, KeyError):
                pass

        hint = (
            f" Output is stored in memory {memory_id}; use memory(action=get, id={memory_id}) if needed."
            if memory_id else " Output was displayed directly and no memory record was created."
        )
        content = (
            "<user_shell_command>\n"
            f"<command>{html.escape(command)}</command>\n"
            f"<result>Exit code: {returncode}.{hint}</result>\n"
            "</user_shell_command>"
        )
        if hasattr(self, "context"):
            self.context.add_user(content)
        if hasattr(self, "conversation_log"):
            self.conversation_log.append({"type": "user", "content": content})
        if hasattr(self, "_autosave"):
            self._autosave()

    def _short_path(self, path: str) -> str:
        """Strip work_dir prefix from path for cleaner display."""
        wd = str(self.config.work_dir)
        if path.startswith(wd + "/"):
            return path[len(wd) + 1:]
        return path

    def _print_tool_call(self, tc: dict):
        """Display tool call to user."""
        name = tc["name"]
        args = tc["args"]
        if name == "bash":
            desc = args.get("description", args.get("command", ""))
            cmd = args.get("command", "")
            print(f"\n  {C.tool_call}● {t('tool_bash', desc=desc)}{C.reset}")
            print(f"  {C.muted}  $ {cmd}{C.reset}")
        elif name == "read":
            print(f"\n  {C.tool_call}● {t('tool_read', path=self._short_path(args.get('path', '')))}{C.reset}")
        elif name == "edit":
            self._print_edit_diff(args)
        elif name == "glob":
            print(f"\n  {C.tool_call}● {t('tool_glob', pattern=args.get('pattern', ''))}{C.reset}")
        elif name == "grep":
            pattern = args.get("pattern", "")
            include = args.get("include", "")
            if include:
                pattern = f"{pattern} ({include})"
            print(f"\n  {C.tool_call}● {t('tool_grep', pattern=pattern)}{C.reset}")
        elif name == "list_dir":
            print(f"\n  {C.tool_call}● {t('tool_list_dir', path=self._short_path(args.get('path', '.')))}{C.reset}")
        elif name == "web_fetch":
            print(f"\n  {C.tool_call}● {t('tool_web_fetch', url=args.get('url', ''))}{C.reset}")
        elif name == "web_search":
            print(f"\n  {C.tool_call}● {t('tool_web_search', query=args.get('query', ''))}{C.reset}")
        elif name == "load_tools":
            print(f"\n  {C.tool_call}● {t('tool_load_tools', category=args.get('category', ''))}{C.reset}")
        elif name == "skill":
            print(f"\n  {C.tool_call}● {t('tool_skill', name=args.get('name', ''))}{C.reset}")
        elif name == "agent":
            print(f"\n  {C.tool_call}● {t('tool_agent', name=args.get('name', ''))}{C.reset}")
        else:
            detail = str(args)[:80] if args else ""
            print(f"\n  {C.tool_call}● {t('tool_other', name=name)}({detail}){C.reset}")

    def _print_edit_diff(self, args: dict):
        """Show a bounded summary for atomic structured edits."""
        import difflib
        changes = args.get("changes", [])
        print(f"\n  {C.tool_call}● {t('tool_edit', path=f'{len(changes)} change(s)')}{C.reset}")
        for change in changes[:10]:
            operations = [name for name in ("update", "write", "create", "delete")
                          if name in change]
            op = operations[0] if operations else "?"
            path = self._short_path(change.get(op, ""))
            if op == "update":
                old_lines = change.get("old_string", "").splitlines()
                new_lines = change.get("new_string", "").splitlines()
                diff = list(difflib.ndiff(old_lines, new_lines))
                added = sum(1 for line in diff if line.startswith("+ "))
                removed = sum(1 for line in diff if line.startswith("- "))
                detail = f"+{added} -{removed}"
            elif op in ("write", "create"):
                detail = f"+{len(change.get('content', '').splitlines())}"
            else:
                detail = "delete"
            print(f"  {C.muted}  {path} ({detail}){C.reset}")
        if len(changes) > 10:
            print(f"  {C.muted}  {t('tool_more_lines', n=len(changes) - 10)}{C.reset}")

    def _print_tool_result(self, name: str, result: str):
        """Display tool result (abbreviated). Limits lines, chars per line, and total chars."""
        # Suppress redundant edit success — the preview is already shown
        if not result.startswith("Error:") and not result.startswith("Cancel"):
            if name == "edit" and result.startswith("Applied edits:"):
                return
            # For read — strip [Lines ...] footer, show content lines only
            if name == "read" and not result.startswith("Error:"):
                lines = result.split("\n")
                content_lines = [l for l in lines if not l.startswith("[Lines ")]
                total_info = next((l for l in lines if l.startswith("[Lines ")), "")
                max_show = 10
                for line in content_lines[:max_show]:
                    if len(line) > 200:
                        line = line[:200] + "…"
                    print(f"  {C.tool_result}{line}{C.reset}")
                remaining = len(content_lines) - max_show
                if remaining > 0:
                    print(f"  {C.muted}  {t('tool_more_lines', n=remaining)}{C.reset}")
                return

        is_error = result.startswith("Error:")
        color = C.tool_error if is_error else C.tool_result
        max_total = 2000   # max total chars to display
        max_lines = 10     # max lines to display
        max_line_len = 200 # max chars per line
        lines = result.split("\n")
        shown_chars = 0
        shown_lines = 0

        for line in lines:
            if shown_lines >= max_lines or shown_chars >= max_total:
                break
            if len(line) > max_line_len:
                line = line[:max_line_len] + "…"
            print(f"  {color}{line}{C.reset}")
            shown_chars += len(line)
            shown_lines += 1

        if shown_lines < len(lines) or shown_chars < len(result):
            print(f"  {C.muted}... ({len(lines)} lines, {len(result)} chars total){C.reset}")

    def _llm_call(self, messages, tools, operation="default"):
        """Single LLM call with streaming, spinner, and routing support."""
        url = f"{self.llm.base_url}/v1/chat/completions"
        self.log.request(url, messages, tools)

        tokens = self.context.estimate_tokens()
        _name = t('emelya')
        label = f"{_name} ({self._model_display}, {tokens} tok)"

        def _make_prefix(elapsed):
            return f"\n{C.agent_prefix}{_name}{C.reset} ({C.muted}{self._model_display}, {tokens} tok, {elapsed:.1f}s{C.reset}): "

        spinner = _WaitSpinner(label)
        spinner.start()
        t0 = time.monotonic()

        # Use routing if configured and not manually overridden
        has_routing = (not self._manual_model
                       and (operation in self._routing or "default" in self._routing))
        call_kwargs = dict(temperature=self.config.temperature, max_tokens=self.config.max_tokens)

        if self.config.stream and not has_routing:
            prefix_printed = False

            def _emit_content(text):
                nonlocal prefix_printed
                if not prefix_printed:
                    spinner.stop()
                    elapsed = time.monotonic() - t0
                    prefix = _make_prefix(elapsed)
                    suffix = "\n" if text.startswith(("┌", "│")) else ""
                    print(prefix + suffix, end="", flush=True)
                    prefix_printed = True
                print(text, end="", flush=True)

            renderer = TerminalMarkdownStream(_emit_content)

            def _on_content(text):
                renderer.feed(text)

            try:
                response = self.llm.stream_chat(
                    messages=messages, tools=tools,
                    on_content=_on_content, **call_kwargs,
                )
            finally:
                spinner.stop()
            renderer.finish()
            if prefix_printed:
                print()
        elif has_routing:
            try:
                response = self._call_with_routing(
                    operation, messages, tools, **call_kwargs,
                )
            finally:
                spinner.stop()
            if response.get("content"):
                elapsed = time.monotonic() - t0
                prefix = _make_prefix(elapsed)
                content = render_terminal_markdown(response["content"])
                separator = "\n" if content.startswith("┌") else ""
                print(f"{prefix}{separator}{content}")
        else:
            try:
                response = self.llm.chat(
                    messages=messages, tools=tools, **call_kwargs,
                )
            finally:
                spinner.stop()
            elapsed = time.monotonic() - t0
            if response.get("content"):
                prefix = _make_prefix(elapsed)
                content = render_terminal_markdown(response["content"])
                separator = "\n" if content.startswith("┌") else ""
                print(f"{prefix}{separator}{content}")

        # Update display name from every API response
        mid = response.get("model_id")
        if mid:
            from emelya.models import strip_model_suffix
            self._model_display = strip_model_suffix(mid)

        model_name = mid or self._model_display
        usage = response.get("usage")
        self.log.response(
            response.get("content"), response.get("tool_calls"),
            usage=usage, model=model_name,
        )
        # Write to project-wide usage tracker
        if usage:
            self.usage_tracker.record(
                model=model_name,
                prompt=usage.get("prompt_tokens", 0),
                completion=usage.get("completion_tokens", 0),
                total=usage.get("total_tokens", 0),
                session=self.session_name,
            )
        return response

    def _exec_tool_single(self, tc, work_messages, _seen_calls):
        """Execute a single tool call. Returns 'duplicate' string if skipped, else None."""
        self._print_tool_call(tc)

        # Duplicate detection
        _call_key = (tc["name"], json.dumps(tc["args"], sort_keys=True))
        _seen_calls[_call_key] = _seen_calls.get(_call_key, 0) + 1
        if _seen_calls[_call_key] > 1:
            result = (
                f"Error: you already called {tc['name']} with the same arguments. "
                "Use a different approach or provide your answer."
            )
            self.log.tool_exec(tc["name"], tc["args"], result, 0)
            self._print_tool_result(tc["name"], result)
            work_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })
            return "duplicate"

        # PreToolUse hook — may block tool execution
        if self.tool_ctx.hook_runner:
            hr = self.tool_ctx.hook_runner.run("PreToolUse", {
                "tool": tc["name"],
                "args": tc["args"],
            })
            if hr.block:
                result = f"Blocked by hook: {hr.reason or hr.message or 'hook'}"
                if hr.message:
                    print(f"\n  {C.warning}Hook: {hr.message}{C.reset}")
                self.log.tool_exec(tc["name"], tc["args"], result, 0)
                self._print_tool_result(tc["name"], result)
                self.conversation_log.append({"type": "tool_call", "name": tc["name"], "args": tc["args"]})
                self.conversation_log.append({"type": "tool_result", "name": tc["name"], "content": result})
                work_messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                return None

        t0 = time.monotonic()
        try:
            result = self.registry.execute(tc["name"], tc["args"], self.tool_ctx)
        except KeyboardInterrupt:
            result = "Cancelled by user"
        elapsed = int((time.monotonic() - t0) * 1000)

        self.log.tool_exec(tc["name"], tc["args"], result, elapsed)

        # Intercept plan_mode exit
        if tc["name"] == "plan_mode" and tc["args"].get("action") == "exit":
            try:
                self._notify_attention()
                answer = input(f"\n  {C.warning}{t('allow')}{C.reset} ").strip().lower()
                if answer not in ("y", "yes", "д", "да"):
                    from emelya.tools.plan_mode import reset as plan_reset
                    plan_reset()
                    result = "Plan rejected by user. Ask what to change."
            except (EOFError, KeyboardInterrupt):
                from emelya.tools.plan_mode import reset as plan_reset
                plan_reset()
                result = "Plan rejected."

        # Intercept planner result
        if (tc["name"] == "agent"
                and tc["args"].get("name") == "planner"
                and '{"plan":' in result[:50]):
            try:
                plan_data = json.loads(result)
                plan_result = self._execute_plan(plan_data.get("plan", []))
                result = plan_result
            except (json.JSONDecodeError, Exception) as e:
                result = f"Plan execution error: {e}\nRaw plan: {result[:500]}"

        # PostToolUse hook — may substitute the result
        if self.tool_ctx.hook_runner:
            hr = self.tool_ctx.hook_runner.run("PostToolUse", {
                "tool": tc["name"],
                "args": tc["args"],
                "result": result,
            })
            if hr.result is not None:
                result = hr.result
            if hr.message:
                print(f"\n  {C.warning}Hook: {hr.message}{C.reset}")

        self._print_tool_result(tc["name"], result)
        self.conversation_log.append({
            "type": "tool_call", "name": tc["name"], "args": tc["args"],
        })
        self.conversation_log.append({
            "type": "tool_result", "name": tc["name"], "content": result,
        })
        work_messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": result,
        })
        return None

    def _exec_tools_parallel(self, tool_calls, work_messages, _seen_calls, consecutive_errors):
        """Execute multiple read-only tool calls in parallel."""
        from concurrent.futures import ThreadPoolExecutor

        # Print all calls first
        for tc in tool_calls:
            self._print_tool_call(tc)

        # Check duplicates before executing
        tasks = []
        for tc in tool_calls:
            _call_key = (tc["name"], json.dumps(tc["args"], sort_keys=True))
            _seen_calls[_call_key] = _seen_calls.get(_call_key, 0) + 1
            if _seen_calls[_call_key] > 1:
                tasks.append((tc, None))  # mark as duplicate
            else:
                tasks.append((tc, True))  # needs execution

        def _run(tc):
            t0 = time.monotonic()
            try:
                result = self.registry.execute(tc["name"], tc["args"], self.tool_ctx)
            except Exception as e:
                result = f"Error: {e}"
            elapsed = int((time.monotonic() - t0) * 1000)
            return result, elapsed

        # Execute non-duplicate calls in parallel
        results = {}
        to_execute = [(tc, idx) for idx, (tc, needs) in enumerate(tasks) if needs]
        if to_execute:
            with ThreadPoolExecutor(max_workers=min(4, len(to_execute))) as pool:
                futures = {pool.submit(_run, tc): (tc, idx) for tc, idx in to_execute}
                for future in futures:
                    tc, idx = futures[future]
                    results[idx] = future.result()

        # Assemble results in order
        for idx, (tc, needs) in enumerate(tasks):
            if needs is None:
                result = (
                    f"Error: you already called {tc['name']} with the same arguments. "
                    "Use a different approach or provide your answer."
                )
                self.log.tool_exec(tc["name"], tc["args"], result, 0)
            else:
                result, elapsed = results[idx]
                self.log.tool_exec(tc["name"], tc["args"], result, elapsed)
            self._print_tool_result(tc["name"], result)
            self.conversation_log.append({
                "type": "tool_call", "name": tc["name"], "args": tc["args"],
            })
            self.conversation_log.append({
                "type": "tool_result", "name": tc["name"], "content": result,
            })
            work_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

    def _agent_loop(self, user_input: str):
        """Run the agent loop with work context isolation.

        Tool calls and results live in a temporary work context.
        Only user input and final text response go into the main context.
        """
        self.tool_ctx.user_language = _detect_task_language(user_input)
        self.context.add_user(user_input)
        self.conversation_log.append({"type": "user", "content": user_input})
        # Persist the user's request before any network/model work so a crash or
        # second Ctrl+C cannot erase an unfinished turn.
        self._autosave()

        # First call — from main context (update usage for accurate token tracking)
        tools = self.registry.get_tools(compact=self._use_compact_tools)
        try:
            response = self._llm_call(self.context.get_messages(), tools)
            self.context.update_usage(response.get("usage"))
        except (LLMError, KeyboardInterrupt) as e:
            # Keep the request in session history, but balance the conversation
            # so a resumed session can accept the next user message normally.
            marker = f"({t('interrupted')})" if isinstance(e, KeyboardInterrupt) else f"(Error: {e})"
            self.context.add_assistant_text(marker)
            self.conversation_log.append({"type": "assistant", "content": marker})
            self._autosave()
            if isinstance(e, KeyboardInterrupt):
                print(f"\n  {t('interrupted')}")
                self.log.event("Interrupted by user")
            else:
                print(f"\nError: {e}")
                self.log.event(f"Error: {e}")
            return

        tool_calls = response.get("tool_calls")
        if not tool_calls:
            # Simple text response — save directly to main context
            text = response.get("content") or ""
            self.context.add_assistant_text(text)
            self.conversation_log.append({"type": "assistant", "content": text})
            self._autosave()
            return

        # Has tool calls — switch to work context
        work_messages = list(self.context.get_messages())
        work_messages.append(response["raw"])
        final_text = response.get("content") or ""
        consecutive_errors = 0
        external_calls = 0
        external_failures = 0
        force_reason = ""
        _seen_calls = {}  # (name, args_key) -> count — duplicate detection

        _PARALLEL_SAFE = {"read", "grep", "glob", "list_dir", "man", "web_search"}

        for iteration in range(self.config.max_iterations):
            result_start = len(work_messages)
            # Check if all calls are parallel-safe read-only tools
            can_parallel = (
                len(tool_calls) > 1
                and all(tc["name"] in _PARALLEL_SAFE for tc in tool_calls)
            )

            if can_parallel:
                # Execute read-only tools in parallel
                self._exec_tools_parallel(
                    tool_calls, work_messages, _seen_calls, consecutive_errors,
                )
                # Update consecutive_errors from results
                for msg in work_messages[-len(tool_calls):]:
                    content = msg.get("content", "")
                    if content.startswith("Error:"):
                        consecutive_errors += 1
                    else:
                        consecutive_errors = 0
            else:
                # Sequential execution (with plan/mode interception)
                for tc in tool_calls:
                    result = self._exec_tool_single(
                        tc, work_messages, _seen_calls,
                    )
                    if result is not None:  # was duplicate
                        consecutive_errors += 1
                        continue

                    if work_messages[-1].get("content", "").startswith("Error:"):
                        consecutive_errors += 1
                    else:
                        consecutive_errors = 0

            result_messages = work_messages[result_start:]
            for tc, message in zip(tool_calls, result_messages):
                if tc["name"] in _EXTERNAL_TOOLS:
                    external_calls += 1
                    if _failed_tool_result(message.get("content", "")):
                        external_failures += 1

            if external_failures >= _MAX_EXTERNAL_FAILURES:
                force_reason = "external tools failed repeatedly"
                break
            if external_calls >= _MAX_EXTERNAL_CALLS:
                force_reason = "external tool budget reached"
                break

            # Break if too many consecutive errors — force answer
            if consecutive_errors >= 3:
                force_reason = "tools failed repeatedly"
                break

            # Progressive nudge — only if last message is a tool result (safe position)
            remaining = self.config.max_iterations - iteration - 1
            nudge = None
            if remaining == 3:
                nudge = "IMPORTANT: Only 3 steps left. Start composing your answer."
            elif remaining == 1:
                nudge = "LAST STEP. Give your final answer NOW. Do not call any more tools."
            # Insert nudge only if safe (last message is tool, not assistant with tool_calls)
            if nudge and work_messages and work_messages[-1].get("role") == "tool":
                work_messages.append({"role": "user", "content": nudge})

            # Next LLM call in work context (refresh tools — load_tools may have added new ones)
            if remaining == 0:
                # Last step — force text-only response (no tools)
                tools = None
            else:
                tools = self.registry.get_tools(compact=self._use_compact_tools)
            try:
                response = self._llm_call(work_messages, tools)
            except LLMError as e:
                print(f"\nError: {e}")
                self.log.event(f"Error: {e}")
                final_text = final_text or f"(Error during processing: {e})"
                break
            except KeyboardInterrupt:
                print(f"\n  {t('interrupted')}")
                self.log.event("Interrupted by user")
                break

            work_messages.append(response["raw"])
            if response.get("content"):
                final_text = response["content"]

            tool_calls = response.get("tool_calls")
            if not tool_calls:
                break  # Final text response

        # Force answer if loop ended without text response
        if force_reason or not final_text:
            print(f"\n  ({t('max_iter')})")
            work_messages.append({
                "role": "user",
                "content": (
                    f"STOP. {force_reason or 'Maximum tool call iterations reached'}. "
                    "You MUST provide your final answer RIGHT NOW based on everything you have seen. "
                    "State uncertainty briefly when evidence is incomplete. Do not call more tools."
                ),
            })
            try:
                response = self._llm_call(work_messages, None)
                if response.get("content"):
                    final_text = response["content"]
            except Exception as e:
                print(f"\nError: {e}")

        # Only the final text goes into main context
        if final_text:
            self.context.add_assistant_text(final_text)
            self.conversation_log.append({"type": "assistant", "content": final_text})

        # Autosave session
        self._autosave()

    def _execute_plan(self, steps: list) -> str:
        """Execute a planner result with persistent tasks and shared memory."""
        if not steps:
            return "Empty plan"

        tmp_dir = self._session_dir() / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        results = {}  # output_file → execution metadata
        failed_outputs = set()  # output files from failed steps
        total = len(steps)
        completed = 0
        final_result = ""

        # Validate the complete plan before creating persistent tasks. A malformed
        # later step must not leave an apparently runnable partial plan behind.
        normalized_steps = []
        seen_outputs = set()
        for i, original in enumerate(steps):
            step = dict(original)
            desc = step.get("description") or f"Step {i + 1}"
            if bool(step.get("agent")) == bool(step.get("tool")):
                return f"Error: plan step {i + 1} must provide exactly one agent or tool"
            output_file = step.get("output") or f"step-{i + 1:02d}.txt"
            if output_file in seen_outputs:
                return f"Error: duplicate plan output: {output_file}"
            unknown_uses = [name for name in step.get("uses", []) if name not in seen_outputs]
            if unknown_uses:
                return (
                    f"Error: plan step {i + 1} references unknown or future outputs: "
                    + ", ".join(unknown_uses)
                )
            out_path = (tmp_dir / output_file).resolve()
            try:
                out_path.relative_to(tmp_dir.resolve())
            except ValueError:
                return f"Error: plan output escapes session directory: {output_file}"
            step["description"] = desc
            step["output"] = output_file
            step["_out_path"] = out_path
            normalized_steps.append(step)
            seen_outputs.add(output_file)

        # Materialize the validated plan before execution so it survives an
        # interruption and can be inspected or resumed by another session.
        plan_tasks = []
        output_to_task = {}
        for i, step in enumerate(normalized_steps):
            output_file = step["output"]
            dependencies = [output_to_task[name] for name in step.get("uses", [])
                            if name in output_to_task]
            task = self.task_store.create(
                title=step["description"],
                status="pending",
                dependencies=dependencies,
                metadata={
                    "plan_step": i + 1,
                    "agent": step.get("agent"),
                    "tool": step.get("tool"),
                    "output": output_file,
                    "uses": step.get("uses", []),
                },
            )
            plan_tasks.append(task)
            output_to_task[output_file] = task["id"]

        for i, step in enumerate(normalized_steps):
            desc = step["description"]
            agent_name = step.get("agent")
            tool_name = step.get("tool")
            args = step.get("args", {})
            output_file = step["output"]
            out_path = step["_out_path"]
            uses = step.get("uses", [])
            task_id = plan_tasks[i]["id"]

            # Skip if any dependency failed
            missing_deps = [u for u in uses if u in failed_outputs]
            if missing_deps:
                print(f"\n  {C.warning}[{i + 1}/{total}]{C.reset} {desc}")
                print(f"  {C.tool_error}Skipped — depends on failed: {', '.join(missing_deps)}{C.reset}")
                failed_outputs.add(output_file)
                results[output_file] = {
                    "size": 0, "ok": False, "reason": "skipped", "task_id": task_id,
                }
                self.task_store.update(
                    task_id, status="skipped",
                    error=f"depends on failed outputs: {', '.join(missing_deps)}",
                )
                continue

            print(f"\n  {C.info}[{i + 1}/{total}]{C.reset} {desc}")
            self.task_store.update(task_id, status="in_progress")

            # Pass references, not full previous outputs. The next agent can use
            # read/memory only when it actually needs the detail.
            if uses and (agent_name or tool_name):
                context_parts = []
                for use_file in uses:
                    info = results.get(use_file)
                    if info:
                        context_parts.append(
                            f"- {use_file}: artifact={info.get('artifact')}, "
                            f"memory_id={info.get('memory_id')}, task_id={info.get('task_id')}"
                        )
                if context_parts:
                    extra_context = (
                        "Previous step references. Use read or memory(get) only for details:\n"
                        + "\n".join(context_parts)
                    )
                    if agent_name:
                        current_args = args.copy()
                        for key in ("task", "question", "target", "path", "symbol"):
                            if key in current_args:
                                current_args[key] = current_args[key] + f"\n\nContext from previous steps:\n{extra_context}"
                                break
                        else:
                            current_args["context"] = extra_context
                        args = current_args

            # Execute
            is_error = False
            try:
                if agent_name:
                    result = self.registry.execute("agent", {
                        "name": agent_name,
                        "args": args,
                    }, self.tool_ctx)
                elif tool_name:
                    result = self.registry.execute(tool_name, args, self.tool_ctx)
                else:
                    result = f"Step has no agent or tool: {desc}"
                    is_error = True
            except KeyboardInterrupt:
                result = "Cancelled by user"
                print(f"  {C.warning}Plan interrupted at step {i + 1}{C.reset}")
                failed_outputs.add(output_file)
                self.task_store.update(task_id, status="failed", error=result)
                break
            except Exception as e:
                result = f"Error: {e}"
                is_error = True

            if (
                result.startswith("Error:")
                or result.startswith("Agent error:")
                or "<tool_call>" in result.lower()
                or "<function=" in result.lower()
            ):
                is_error = True

            # The complete result is an artifact; shared memory receives only a
            # concise preview and a pointer to that artifact.
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(result, encoding="utf-8")
            preview = " ".join(result.split())[:500] or "(empty result)"
            memory_record = self.memory_store.put(
                kind="summary",
                summary=f"{desc}: {preview}",
                scope=output_file,
                evidence={
                    "task_id": task_id,
                    "artifact": str(out_path),
                    "agent": agent_name,
                    "tool": tool_name,
                },
                tags=["plan-step", "failed" if is_error else "completed"],
                status="needs_review" if is_error else "proposed",
                author=agent_name or tool_name or "plan",
            )
            results[output_file] = {
                "size": len(result),
                "ok": not is_error,
                "task_id": task_id,
                "artifact": str(out_path),
                "memory_id": memory_record["id"],
            }
            if is_error:
                failed_outputs.add(output_file)
                self.task_store.update(
                    task_id, status="failed", artifact=str(out_path),
                    memory_id=memory_record["id"], error=result[:1000],
                )
                print(f"  {C.tool_error}Failed: {result[:100]}{C.reset}")
            else:
                self.task_store.update(
                    task_id, status="done", artifact=str(out_path),
                    memory_id=memory_record["id"], error="",
                )
                print(f"  {C.muted}→ {output_file} ({len(result)} chars, {memory_record['id']}){C.reset}")
                completed += 1
                final_result = result

        # Summary
        skipped = sum(1 for r in results.values() if isinstance(r, dict) and r.get("reason") == "skipped")
        failed = sum(
            1 for r in results.values()
            if isinstance(r, dict) and not r.get("ok") and r.get("reason") != "skipped"
        )
        state = "complete" if completed == total else "incomplete"
        parts = [f"Plan {state}: {completed} done, {failed} failed, {skipped} skipped (of {total})."]
        parts.append(f"Temp files: {tmp_dir}")
        for fname, info in results.items():
            if isinstance(info, dict):
                status = "OK" if info["ok"] else ("skipped" if info.get("reason") == "skipped" else "FAILED")
                parts.append(
                    f"  {fname}: {info['size']} chars [{status}], "
                    f"task=#{info.get('task_id')}, memory={info.get('memory_id', '-')}"
                )
        if state == "complete" and final_result:
            parts.append("\nFinal step result:\n" + final_result[:20000])
        elif state != "complete":
            parts.append("Do not claim full completion; report failed and skipped steps explicitly.")
        return "\n".join(parts)

    def _autosave(self):
        """Save context to current session directory."""
        try:
            self.context.save(self._session_dir() / "context.json")
        except Exception:
            pass

    def _check_context_budget(self) -> bool:
        """Check if context exceeds 75% of limit. If so, offer to compact.
        Returns True if user chose to compact/clear (caller should re-prompt)."""
        tokens = self.context.estimate_tokens()
        threshold = int(self.context.context_limit * 0.75)
        if tokens < threshold or not self.context.messages:
            return False

        pct = int(tokens / self.context.context_limit * 100)
        print(f"\n  {C.warning}{t('context_warning', pct=pct, tokens=tokens, limit=self.context.context_limit)}{C.reset}")
        try:
            self._notify_attention()
            answer = input(f"  {t('compact_prompt')}: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False

        if answer in ("y", "yes", "д", "да", "1", "/compact"):
            # Run compact
            self._handle_command("/compact")
            return True
        if answer in ("c", "clear", "о", "очистить", "2", "/clear"):
            self._handle_command("/clear")
            return True
        # User declined — proceed with the request
        return False

    # --- Main entry point ---

    def run(self):
        """Main REPL loop."""
        import signal
        signal.signal(signal.SIGTERM, lambda *_: self._cleanup() or sys.exit(0))

        self._print_banner()

        # Check connection (non-fatal)
        ok, info = self.llm.check_connection()
        if ok:
            self._update_model_display(info)
            # Apply detected context limit if no explicit override
            detected = getattr(self.llm, "detected_context_limit", None)
            cfg = self._models.get(self.active_model, {})
            if detected and not cfg.get("context_limit"):
                self.context.context_limit = detected
            ctx_info = f", ctx:{self.context.context_limit // 1000}K"
            print(f"\n{C.agent_prefix}{t('emelya')}:{C.reset} {t('ready_model', model=self._model_display)}{C.muted}{ctx_info}{C.reset}")
        else:
            pass  # model unavailable — silently proceed, /model will show status

        print(f"\n{C.agent_prefix}{t('emelya')}:{C.reset} {t('greeting')}")

        self.log.session_start(
            self.config.base_url, self.config.model,
            str(self.config.work_dir), self.session_name,
        )
        self.hook_runner.run("SessionStart", {
            "session": self.session_name,
            "work_dir": str(self.config.work_dir),
        })

        # Resume session if --resume was passed
        if self.config.resume_session:
            self._cmd_resume(self.config.resume_session)

        # Interactive line editor with per-session history
        self.input_history = InputHistory(str(self._session_dir() / "history"))
        extra_cmds = list(self._custom_commands.keys())
        editor = LineEditor(
            work_dir=self.config.work_dir,
            extra_commands=extra_cmds,
            agents_dirs=self.agents_dirs,
            skills_dirs=self.skills_dirs,
        )

        while True:
            try:
                print()  # blank line before prompt
                user_input = editor.read(prompt=f"{C.user_prompt}{t('you')}:{C.reset} ", history=self.input_history)
            except (EOFError, KeyboardInterrupt):
                print(f"\n\n{t('bye')}")
                break

            if user_input is None:  # Ctrl+C / Escape
                continue
            user_input = user_input.strip()
            if not user_input:
                continue

            # Explicit shell escape — user-authorized, outside model context.
            # Inherited stdio lets full-screen programs (mc, vim, htop) own TTY.
            if user_input.startswith("!|"):
                command = user_input[2:].lstrip()
                if not command:
                    self._run_shell_escape("")
                    continue
                model_text = self._last_assistant_text()
                if not model_text:
                    print("  No previous model response to pipe.")
                    continue
                self._run_shell_escape(command, stdin_text=model_text)
                continue
            if user_input.startswith("!"):
                self._run_shell_escape(user_input[1:].lstrip())
                continue

            # Handle /commands — anything starting with / is a command, never sent to model
            if user_input.strip().lower() in ("exit", "quit", "выход", "пока", "учше", "йгше",
                                              "/exit", "/quit"):
                user_input = "/exit"

            if user_input.startswith("/"):
                if user_input == "/exit":
                    print(f"\n{t('bye')}")
                    break
                if not self._handle_command(user_input):
                    cmd = user_input.split()[0]
                    print(f"  {t('unknown_cmd')}: {cmd}")
                    custom = (" " + " ".join(self._custom_commands)) if self._custom_commands else ""
                    print(f"  Available: /help /clear /compact /undo /drop /rename /resume /branch /export /model /skill /agent /tools /mcp /stats /context /history /commands /exit{custom}")
                continue

            # Check context budget before sending to model
            if self._check_context_budget():
                continue  # user chose to compact or clear, re-prompt

            # Agent loop
            try:
                self._agent_loop(user_input)
            except KeyboardInterrupt:
                print(f"\n  {t('interrupted')}")

        self._cleanup()

    def _cleanup(self):
        """Clean shutdown — stop MCP, close logs."""
        self._shutdown_mcp()
        self.hook_runner.run("SessionEnd", {"session": self.session_name})
        self.log.session_end()
        self.log.close()
        self.usage_tracker.close()
