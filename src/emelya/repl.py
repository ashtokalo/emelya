"""REPL — main interactive loop with agent cycle."""

import json
import platform
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
from emelya.sandbox import Sandbox
from emelya.tools import ToolContext, ToolRegistry
from emelya.tools.skill import scan_skills
from emelya.tools.agent import scan_agents

from emelya.models import ModelMixin
from emelya.mcp_manager import MCPMixin
from emelya.commands import CommandsMixin


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
        self.sandbox = Sandbox(config.work_dir)
        self.registry = ToolRegistry()

        src_dir = Path(__file__).parent          # src/emelya/
        self.app_dir = src_dir.parent.parent    # project root
        self.skills_dir = self.app_dir / "skills"
        self.agents_dir = self.app_dir / "agents"
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

        # Session management
        self.session_name = datetime.now().strftime("tmp%Y%m%d%H%M%S")
        self.log = self._create_log()
        self.usage_tracker = UsageTracker(config.work_dir / ".emelya" / "usage.jsonl")

        # Tool context shared across calls
        self.tool_ctx = ToolContext(
            work_dir=config.work_dir,
            sandbox=self.sandbox,
            confirm=self._confirm,
            skills_dir=self.skills_dir,
            agents_dir=self.agents_dir,
            llm_client=self.llm,
            config=config,
            session_log=self.log,
            usage_tracker=self.usage_tracker,
            get_routed_client=self._resolve_client_for_operation,
        )

        # Load tools
        self.registry.load_builtin(src_dir)
        self.tool_ctx.tool_registry = self.registry

        # Load MCP servers (register as extension categories)
        self._mcp_clients = {}
        self._mcp_startup_info = []  # collected during init, printed after banner
        self._init_mcp()

        # Update skill/agent tool descriptions with available items
        self._update_dynamic_descriptions()

        # Build system prompt
        self._init_system_prompt()

    def _session_dir(self, name: str = None) -> Path:
        return self.sessions_dir / (name or self.session_name)

    def _create_log(self) -> SessionLog:
        log_path = self._session_dir() / "log.txt"
        return SessionLog(log_path)

    # --- Setup ---

    def _update_dynamic_descriptions(self):
        skills = scan_skills(self.skills_dir)
        if skills:
            skills_list = ", ".join(f"{k} ({v})" for k, v in skills.items())
            self.registry.update_description(
                "skill",
                f"Load a skill with specialized instructions. Available: {skills_list}",
            )

        agents = scan_agents(self.agents_dir)
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

        self.context.set_system(prompt)

    # --- Agent loop ---

    def _confirm(self, description: str, detail: str = "") -> bool:
        """Ask user for confirmation. Respects confirm_mode."""
        if self.config.confirm_mode == "no_confirm":
            return True
        try:
            answer = input(f"  {C.warning}{t('allow')}:{C.reset} ").strip().lower()
            return answer in ("y", "yes", "д", "да")
        except (EOFError, KeyboardInterrupt):
            return False

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
        elif name == "write":
            self._print_write_preview(self._short_path(args.get("path", "")), args.get("content", ""))
        elif name == "edit":
            edit_args = dict(args)
            edit_args["path"] = self._short_path(edit_args.get("path", ""))
            self._print_edit_diff(edit_args)
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

    def _print_write_preview(self, path: str, content: str):
        """Show write preview with first lines of content."""
        lines = content.splitlines() if content else []
        n_lines = len(lines)
        print(f"\n  {C.tool_call}● {t('tool_write', path=path)}{C.reset}")
        print(f"  ⎿  {t('tool_n_lines', n=n_lines)}")
        max_preview = 10
        for i, line in enumerate(lines[:max_preview]):
            print(f"  {C.diff_add}  {i + 1:4}  {line}{C.reset}")
        if n_lines > max_preview:
            print(f"  {C.muted}  {t('tool_more_lines', n=n_lines - max_preview)}{C.reset}")

    def _print_edit_diff(self, args: dict):
        """Show a compact diff for edit tool calls with line numbers."""
        import difflib
        path = args.get("path", "")
        old = args.get("old_string", "")
        new = args.get("new_string", "")
        old_lines = old.splitlines()
        new_lines = new.splitlines()

        # Count added/removed
        n_added = sum(1 for l in difflib.unified_diff(old_lines, new_lines, n=0, lineterm="")
                      if l.startswith("+") and not l.startswith("+++"))
        n_removed = sum(1 for l in difflib.unified_diff(old_lines, new_lines, n=0, lineterm="")
                        if l.startswith("-") and not l.startswith("---"))
        parts = []
        if n_added:
            parts.append(t("tool_added", n=n_added))
        if n_removed:
            parts.append(t("tool_removed", n=n_removed))
        if not n_added and not n_removed:
            parts.append(t("tool_changed", n=len(old_lines)))
        summary = ", ".join(parts)

        print(f"\n  {C.tool_call}● {t('tool_edit', path=path)}{C.reset}")
        print(f"  ⎿  {summary}")

        # Find the starting line number in the actual file
        start_line = 1
        try:
            resolved = self.sandbox.validate(path)
            if resolved.exists():
                file_text = resolved.read_text(encoding="utf-8", errors="replace")
                idx = file_text.find(old)
                if idx >= 0:
                    start_line = file_text[:idx].count("\n") + 1
        except Exception:
            pass

        # Build diff with line numbers
        diff = list(difflib.unified_diff(old_lines, new_lines, n=2, lineterm=""))
        if not diff:
            return

        max_diff_lines = 20
        shown = 0
        old_num = start_line
        new_num = start_line
        for line in diff:
            if line.startswith("---") or line.startswith("+++"):
                continue
            if line.startswith("@@"):
                continue
            if shown >= max_diff_lines:
                remaining = len([l for l in diff if l and l[0] in "+-  " and not l.startswith(("---", "+++"))]) - shown
                if remaining > 0:
                    print(f"  {C.muted}  {t('tool_more_lines', n=remaining)}{C.reset}")
                break
            if line.startswith("-"):
                print(f"  {C.diff_del}  {old_num:4} - {line[1:]}{C.reset}")
                old_num += 1
            elif line.startswith("+"):
                print(f"  {C.diff_add}  {new_num:4} + {line[1:]}{C.reset}")
                new_num += 1
            else:
                print(f"  {C.diff_ctx}  {old_num:4}   {line[1:]}{C.reset}")
                old_num += 1
                new_num += 1
            shown += 1

    def _print_tool_result(self, name: str, result: str):
        """Display tool result (abbreviated). Limits lines, chars per line, and total chars."""
        # Suppress redundant success messages for edit/write — diff/preview already shown
        if not result.startswith("Error:") and not result.startswith("Cancel"):
            if name == "edit" and result.startswith("Replaced "):
                return
            if name == "write" and result.startswith("Written "):
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

            def _on_content(text):
                nonlocal prefix_printed
                if not prefix_printed:
                    spinner.stop()
                    elapsed = time.monotonic() - t0
                    prefix = _make_prefix(elapsed)
                    print(prefix, end="", flush=True)
                    prefix_printed = True
                print(text, end="", flush=True)

            try:
                response = self.llm.stream_chat(
                    messages=messages, tools=tools,
                    on_content=_on_content, **call_kwargs,
                )
            finally:
                spinner.stop()
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
                print(f"{prefix}{response['content']}")
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
                print(f"{prefix}{response['content']}")

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
        self.context.add_user(user_input)
        self.conversation_log.append({"type": "user", "content": user_input})

        # First call — from main context (update usage for accurate token tracking)
        tools = self.registry.get_tools(compact=self._use_compact_tools)
        try:
            response = self._llm_call(self.context.get_messages(), tools)
            self.context.update_usage(response.get("usage"))
        except (LLMError, KeyboardInterrupt) as e:
            # No response received — remove orphaned user message
            self.context.pop_last_user()
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
            return

        # Has tool calls — switch to work context
        work_messages = list(self.context.get_messages())
        work_messages.append(response["raw"])
        final_text = response.get("content") or ""
        consecutive_errors = 0
        _seen_calls = {}  # (name, args_key) -> count — duplicate detection

        _PARALLEL_SAFE = {"read", "grep", "glob", "list_dir", "man", "web_search"}

        for iteration in range(self.config.max_iterations):
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

            # Break if too many consecutive errors — force answer
            if consecutive_errors >= 3:
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
        if not final_text:
            print(f"\n  ({t('max_iter')})")
            work_messages.append({
                "role": "user",
                "content": (
                    "STOP. Maximum tool call iterations reached. "
                    "You MUST provide your final answer RIGHT NOW based on everything you have seen. "
                    "Do NOT say you need more information."
                ),
            })
            try:
                response = self.llm.chat(
                    messages=work_messages, tools=None,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                )
                if response.get("content"):
                    final_text = response["content"]
                    tokens = self.context.estimate_tokens()
                    print(f"\n{C.agent_prefix}{t('emelya')}{C.reset} ({C.muted}{self._model_display}, {tokens} tok{C.reset}): {final_text}")
            except Exception as e:
                print(f"\nError: {e}")

        # Only the final text goes into main context
        if final_text:
            self.context.add_assistant_text(final_text)
            self.conversation_log.append({"type": "assistant", "content": final_text})

        # Autosave session
        self._autosave()

    def _execute_plan(self, steps: list) -> str:
        """Execute a plan created by planner agent. Returns summary."""
        if not steps:
            return "Empty plan"

        tmp_dir = self._session_dir() / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        results = {}  # output_file → {"size": N, "ok": bool}
        failed_outputs = set()  # output files from failed steps
        total = len(steps)
        completed = 0

        for i, step in enumerate(steps):
            desc = step.get("description", f"Step {i + 1}")
            agent_name = step.get("agent")
            tool_name = step.get("tool")
            args = step.get("args", {})
            output_file = step.get("output")
            uses = step.get("uses", [])

            # Skip if any dependency failed
            missing_deps = [u for u in uses if u in failed_outputs]
            if missing_deps:
                print(f"\n  {C.warning}[{i + 1}/{total}]{C.reset} {desc}")
                print(f"  {C.tool_error}Skipped — depends on failed: {', '.join(missing_deps)}{C.reset}")
                if output_file:
                    failed_outputs.add(output_file)
                    results[output_file] = {"size": 0, "ok": False, "reason": "skipped"}
                continue

            print(f"\n  {C.info}[{i + 1}/{total}]{C.reset} {desc}")

            # Inject content from previous step outputs
            if uses and (agent_name or tool_name):
                context_parts = []
                for use_file in uses:
                    use_path = tmp_dir / use_file
                    if use_path.exists():
                        content = use_path.read_text(encoding="utf-8")
                        context_parts.append(f"--- {use_file} ---\n{content}")
                if context_parts:
                    extra_context = "\n\n".join(context_parts)
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
                if output_file:
                    failed_outputs.add(output_file)
                break
            except Exception as e:
                result = f"Error: {e}"
                is_error = True

            if result.startswith("Error:") or result.startswith("Agent error:"):
                is_error = True

            # Save to tmp file
            if output_file:
                out_path = tmp_dir / output_file
                out_path.write_text(result, encoding="utf-8")
                results[output_file] = {"size": len(result), "ok": not is_error}
                if is_error:
                    failed_outputs.add(output_file)
                    print(f"  {C.tool_error}Failed: {result[:100]}{C.reset}")
                else:
                    print(f"  {C.muted}→ {output_file} ({len(result)} chars){C.reset}")
                    completed += 1
            else:
                completed += 1

        # Summary
        failed = len(failed_outputs)
        skipped = sum(1 for r in results.values() if isinstance(r, dict) and r.get("reason") == "skipped")
        parts = [f"Plan: {completed} done, {failed} failed, {skipped} skipped (of {total})."]
        parts.append(f"Temp files: {tmp_dir}")
        for fname, info in results.items():
            if isinstance(info, dict):
                status = "OK" if info["ok"] else ("skipped" if info.get("reason") == "skipped" else "FAILED")
                parts.append(f"  {fname}: {info['size']} chars [{status}]")
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
            # Translate known error messages
            if info == "Server unavailable":
                info = t("server_unavailable")
            elif "Authentication failed" in info:
                info = t("auth_failed")
            print(f"\n{C.agent_prefix}{t('emelya')}:{C.reset} {C.warning}{info}{C.reset}. {t('use_model_switch')}")

        # Show MCP status collected during init
        if self._mcp_startup_info:
            for entry in self._mcp_startup_info:
                name, n_tools = entry[0], entry[1]
                is_cached = entry[2] if len(entry) > 2 else False
                err = entry[3] if len(entry) > 3 else None
                if err:
                    print(f"\n{C.agent_prefix}{t('emelya')}:{C.reset} MCP {C.warning}{name}{C.reset} — {err}")
                elif is_cached:
                    print(f"\n{C.agent_prefix}{t('emelya')}:{C.reset} MCP {C.muted}{name}{C.reset} — {n_tools} tools ({t('cached')})")
                else:
                    print(f"\n{C.agent_prefix}{t('emelya')}:{C.reset} MCP {C.success}{name}{C.reset} — {n_tools} tools")

        print(f"\n{C.agent_prefix}{t('emelya')}:{C.reset} {t('greeting')}")

        self.log.session_start(
            self.config.base_url, self.config.model,
            str(self.config.work_dir), self.session_name,
        )

        # Resume session if --resume was passed
        if self.config.resume_session:
            self._cmd_resume(self.config.resume_session)

        # Interactive line editor with per-session history
        self.input_history = InputHistory(str(self._session_dir() / "history"))
        editor = LineEditor(work_dir=self.config.work_dir)

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

            # Handle /commands — anything starting with / is a command, never sent to model
            if user_input.startswith("/"):
                if user_input in ("/exit", "/quit"):
                    print(f"\n{t('bye')}")
                    break
                if not self._handle_command(user_input):
                    cmd = user_input.split()[0]
                    print(f"  {t('unknown_cmd')}: {cmd}")
                    print("  Available: /help /clear /compact /undo /drop /rename /resume /branch /export /model /skill /agent /tools /mcp /stats /context /history /exit")
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
        self.log.session_end()
        self.log.close()
        self.usage_tracker.close()
