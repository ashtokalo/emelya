"""Commands mixin — /command handling, session management, UI display."""

import json
from datetime import datetime

from emelya.i18n import t
from emelya.theme import C
from emelya.log import SessionLog, UsageTracker, collect_stats
from emelya.input_history import InputHistory


class CommandsMixin:
    """Command handling and UI methods for REPL.

    Expects self to have: config, context, registry, llm, log,
    tool_ctx, session_name, sessions_dir, app_dir, _model_display,
    _mcp_startup_info, input_history, usage_tracker,
    and methods from ModelMixin and MCPMixin.
    """

    def _handle_command(self, command: str) -> bool:
        """Handle /commands. Returns True if handled."""
        if command == "/help":
            self._print_help()
            return True

        if command == "/clear":
            self.context.clear()
            self.tool_ctx.files_read.clear()
            self.tool_ctx.loaded_skills.clear()
            tokens = self.context.estimate_tokens()
            print(f"  {t('context_cleared')} [{tokens} tokens]")
            return True

        if command == "/undo":
            result = self.tool_ctx.undo()
            print(f"  {result}")
            return True

        if command == "/compact":
            tokens_before = self.context.estimate_tokens()
            print(f"  {t('compacting')}")
            try:
                msgs = self.context.compact_prompt()
                response = self.llm.chat(
                    messages=msgs,
                    tools=None,
                    temperature=0.3,
                    max_tokens=300,
                )
                summary = response.get("content", "")
                if summary:
                    self.context.apply_compact(summary)
                    tokens_after = self.context.estimate_tokens()
                    print(f"  Compacted: {tokens_before} -> {tokens_after} tokens")
                else:
                    print("  Compaction failed: empty response")
            except Exception as e:
                print(f"  Compaction failed: {e}")
            return True

        if command == "/tools":
            print(f"  {t('loaded_tools')}: {', '.join(self.registry.names)}")
            extensions = self.registry.get_extension_summary()
            if extensions:
                print(f"  {t('extensions')} (via load_tools):")
                for cat, tools in extensions.items():
                    loaded = " [loaded]" if cat in self.registry._loaded else ""
                    print(f"    {cat}: {', '.join(tools.keys())}{loaded}")
            return True

        if command == "/context":
            tokens = self.context.estimate_tokens()
            n_msgs = len(self.context.messages)
            print(f"  Context: {tokens} tokens, {n_msgs} messages (limit: {self.config.context_limit})")
            return True

        if command == "/history":
            self._print_full_context()
            return True

        if command == "/stats":
            self._print_stats()
            return True

        # Commands with arguments
        parts = command.split(None, 1)
        cmd = parts[0]
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/drop":
            if not arg or not arg.isdigit():
                print("  Usage: /drop <index> (use /history to see indexes)")
            else:
                result = self.context.drop(int(arg))
                print(f"  {result}")
            return True

        if cmd == "/save":
            self._cmd_save(arg)
            return True

        if cmd == "/load":
            self._cmd_load(arg)
            return True

        if cmd == "/new":
            self._cmd_new(arg)
            return True

        if cmd == "/mcp":
            self._cmd_mcp(arg)
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

        return False

    def _cmd_model(self, arg: str = ""):
        """Handle /model subcommands."""
        parts = arg.split(None, 1) if arg else []
        subcmd = parts[0] if parts else ""
        subarg = parts[1].strip() if len(parts) > 1 else ""

        if subcmd == "add":
            self._model_add(subarg)
        elif subcmd == "remove":
            self._model_remove(subarg)
        elif subcmd == "":
            self._list_models()
        elif subcmd in self._models:
            # Direct switch: /model gpt4
            self._switch_model(subcmd)
        else:
            print(f"  {t('unknown_model')}: {subcmd}")
            print("  Usage: /model, /model <name>, /model add <name> <url> <model> [api_key], /model remove <name>")

    def _cmd_save(self, name: str = ""):
        """Save current session."""
        old_name = self.session_name
        if name:
            self.session_name = name
        session_dir = self._session_dir()

        # Save context
        self.context.save(session_dir / "context.json")

        # If session name changed, move log to new location
        if name and name != old_name:
            self.log.event(f"Session saved as: {self.session_name}")
            old_log_path = self.log.path
            self.log.close()
            # Copy log to new session dir
            import shutil
            new_log_path = session_dir / "log.txt"
            if old_log_path != new_log_path:
                shutil.copy2(old_log_path, new_log_path)
            # Continue logging in new location
            self.log = SessionLog(new_log_path)
        else:
            self.log.event(f"Session saved: {self.session_name}")

        print(f"  {t('session_saved')}: {self.session_name}")
        print(f"  {session_dir}")

    def _cmd_load(self, name: str = ""):
        """Load a saved session."""
        if not name:
            # List available sessions
            if not self.sessions_dir.is_dir():
                print(f"  {t('no_sessions')}")
                return
            sessions = sorted(d.name for d in self.sessions_dir.iterdir() if d.is_dir())
            if not sessions:
                print(f"  {t('no_sessions')}")
                return
            print(f"  {t('available_sessions')}:")
            for s in sessions:
                ctx_file = self.sessions_dir / s / "context.json"
                marker = " *" if s == self.session_name else ""
                size = ""
                if ctx_file.exists():
                    import os
                    size = f" ({os.path.getsize(ctx_file)} bytes)"
                print(f"    {s}{size}{marker}")
            return

        ctx_path = self.sessions_dir / name / "context.json"
        if not ctx_path.exists():
            print(f"  {t('session_not_found')}: {name}")
            return

        # Close current log, switch session
        self.log.event(f"Switching to session: {name}")
        self.log.close()

        self.session_name = name
        self.context.load(ctx_path)
        self.log = self._create_log()
        self.log.event(f"Session loaded: {name}")
        self.tool_ctx.files_read.clear()
        self.tool_ctx.loaded_skills.clear()
        self.input_history = InputHistory(str(self._session_dir() / "history"))

        tokens = self.context.estimate_tokens()
        n_msgs = len(self.context.messages)
        print(f"  {t('session_loaded')}: {name} ({n_msgs} messages, {tokens} tokens)")

    def _cmd_new(self, name: str = ""):
        """Start a new session."""
        # Save current if it has content
        if self.context.messages:
            self._cmd_save()

        self.log.close()

        self.session_name = name or datetime.now().strftime("tmp%Y%m%d%H%M%S")
        self.context.clear()
        self.tool_ctx.files_read.clear()
        self.tool_ctx.loaded_skills.clear()
        self._init_system_prompt()

        self.log = self._create_log()
        self.log.session_start(
            self.config.base_url, self.config.model,
            str(self.config.work_dir), self.session_name,
        )
        self.input_history = InputHistory(str(self._session_dir() / "history"))

        tokens = self.context.estimate_tokens()
        print(f"  {t('new_session')}: {self.session_name} [{tokens} tokens]")

    def _cmd_skill(self, arg: str = ""):
        """Load a skill and inject into current context as user instruction."""
        if not arg:
            from emelya.tools.skill import scan_skills
            skills = scan_skills(self.skills_dir)
            if skills:
                print(f"  {t('available')}:")
                for name, desc in skills.items():
                    print(f"    {name} — {desc}")
            else:
                print("  No skills available")
            return

        # Load skill content
        result = self.registry.execute("skill", {"name": arg}, self.tool_ctx)
        if result.startswith("Error:") or result.startswith("Unknown"):
            print(f"  {result}")
            return

        # Inject into context as system-like instruction and run agent loop
        self.context.add_user(f"/skill {arg}")
        self.context.add_assistant_text(f"Skill '{arg}' loaded.")
        # Add skill content to work context for next LLM call
        self._agent_loop(f"I loaded skill '{arg}'. Follow its instructions. Here it is:\n{result}")

    def _cmd_agent(self, arg: str = ""):
        """Run an agent directly from command line."""
        if not arg:
            from emelya.tools.agent import scan_agents
            agents = scan_agents(self.agents_dir)
            if agents:
                print(f"  {t('available')}:")
                for name, desc in agents.items():
                    print(f"    {name} — {desc}")
            else:
                print("  No agents available")
            return

        parts = arg.split(None, 1)
        name = parts[0]
        task = parts[1] if len(parts) > 1 else ""

        if not task:
            print(f"  Usage: /agent {name} <task description>")
            return

        # Run through agent loop — model will see agent result
        self._agent_loop(f"Use agent '{name}' for this task: {task}")

    # --- Display / UI ---

    def _print_help(self):
        """Print help from prompts/help.md."""
        help_path = self.app_dir / "prompts" / t("help_file")
        try:
            text = help_path.read_text(encoding="utf-8").strip()
            print(f"\n{text}")
        except FileNotFoundError:
            print("  Help file not found: prompts/help.md")

    def _print_stats(self):
        """Show token usage stats: current session + project total."""
        stats = collect_stats(self.sessions_dir)

        def fmt(n):
            if n >= 1_000_000:
                return f"{n / 1_000_000:.1f}M"
            if n >= 1_000:
                return f"{n / 1_000:.1f}K"
            return str(n)

        def print_section(title, by_model, by_date, totals):
            if totals["requests"] == 0:
                print(f"\n  {title}: no data")
                return
            print(f"\n  === {title} ===\n")
            if by_model:
                print(f"  {t('by_model')}:")
                for model, m in sorted(by_model.items()):
                    print(f"    {model}: {fmt(m['total'])} tokens"
                          f" ({fmt(m['prompt'])} in + {fmt(m['completion'])} out)"
                          f" / {m['requests']} req")
            if by_date:
                print(f"\n  {t('by_date')}:")
                for date, d in sorted(by_date.items()):
                    print(f"    {date}: {fmt(d['total'])} tokens / {d['requests']} req")
            print(f"\n  {t('total')}: {fmt(totals['total'])} tokens / {totals['requests']} req")

        # Current session (from session logs)
        cur = stats.get("by_session", {}).get(self.session_name)
        if cur:
            print_section(
                f"{t('session')}: {self.session_name}",
                cur.get("by_model", {}),
                cur.get("by_date", {}),
                cur,
            )

        # Project total (from persistent usage.jsonl — survives session deletion)
        usage_path = self.config.work_dir / ".emelya" / "usage.jsonl"
        project_stats = UsageTracker.load_stats(usage_path)
        print_section(
            t("project_total"),
            project_stats.get("by_model", {}),
            project_stats.get("by_date", {}),
            project_stats.get("totals", {"requests": 0}),
        )

    def _print_full_context(self):
        """Print the full conversation context."""
        msgs = self.context.get_messages()
        print(f"\n  === Context: {len(msgs)} messages, ~{self.context.estimate_tokens()} tokens ===\n")
        for i, msg in enumerate(msgs):
            role = msg["role"].upper()
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id")

            if role == "TOOL":
                # Abbreviate long tool results
                preview = content[:200] + "..." if len(content) > 200 else content
                print(f"  [{i}] TOOL (id={tool_call_id}):\n  {preview}\n")
            elif tool_calls:
                tc_summary = ", ".join(
                    f"{tc['function']['name']}({tc['function'].get('arguments', '')[:80]})"
                    for tc in tool_calls
                )
                if content:
                    print(f"  [{i}] {role}: {content[:200]}")
                print(f"  [{i}] {role} tool_calls: {tc_summary}\n")
            else:
                preview = content[:500] + "..." if len(content) > 500 else content
                print(f"  [{i}] {role}: {preview}\n")

    def _print_banner(self):
        logo = f"""{C.agent_prefix}\
  ██████╗ ██║  ██║ ██████╗   ██████╗  ╔██████╗
  ██╔═══╝ ███╗███║ ██╔═══╝  ██║  ██║  ██╔══██║
  ████╗   ██║█║██║ ████╗    ██║  ██║  ╚██████║
  ██╔═╝   ██║ ╚██║ ██╔═╝    ██║  ██║  ██╔══██║
  ██████╗ ██║  ██║ ██████╗ ██╔╝  ██║ ██╔╝  ██║
  ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚═╝   ╚═╝ ╚═╝   ╚═╝{C.reset}
  {C.muted}Emelya — CLI AI Agent v0.1, Fairy-tale dimwit{C.reset}"""
        print(logo)
        print()
        print(f"  {C.info}{t('workdir')}{C.reset} {self.config.work_dir}")
        print(f"  {C.info}{t('session')}{C.reset} {self.session_name}")
        print()
