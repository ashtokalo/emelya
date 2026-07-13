"""Commands mixin — /command handling, session management, UI display."""

import json
from datetime import datetime
from pathlib import Path

from emelya import __version__
from emelya.i18n import t
from emelya.theme import C
from emelya.log import SessionLog, UsageTracker, collect_stats
from emelya.input_history import InputHistory


def _extract_cmd_desc(content: str) -> str:
    """Extract description from YAML frontmatter or first heading."""
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                if line.startswith("description:"):
                    return line.split(":", 1)[1].strip()
            content = parts[2]
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


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
            self._cmd_clear()
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

        if command == "/commands":
            self._cmd_list_commands()
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

        if cmd == "/model":
            self._cmd_model(arg)
            return True

        if cmd == "/skill":
            self._cmd_skill(arg)
            return True

        if cmd == "/agent":
            self._cmd_agent(arg)
            return True

        # Custom commands from .emelya/commands/
        if cmd in self._custom_commands:
            self._run_custom_command(cmd[1:], arg)
            return True

        return False

    def _scan_custom_commands(self) -> dict:
        """Scan .emelya/commands/ for user-defined .md commands.

        Global commands from ~/.emelya/commands/ are loaded first.
        Local commands from {work_dir}/.emelya/commands/ override them.
        """
        result = {}
        dirs = [
            Path.home() / ".emelya" / "commands",
            self.config.work_dir / ".emelya" / "commands",
        ]
        for commands_dir in dirs:
            if not commands_dir.is_dir():
                continue
            for f in sorted(commands_dir.glob("*.md")):
                try:
                    content = f.read_text(encoding="utf-8").strip()
                    desc = _extract_cmd_desc(content) or f.stem
                    result[f"/{f.stem}"] = {"path": f, "desc": desc}
                except Exception:
                    result[f"/{f.stem}"] = {"path": f, "desc": f.stem}
        return result

    def _run_custom_command(self, name: str, args: str = ""):
        """Execute a custom command from .emelya/commands/<name>.md."""
        info = self._custom_commands.get(f"/{name}")
        if not info:
            return
        try:
            content = info["path"].read_text(encoding="utf-8").strip()
            # Strip frontmatter
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    content = parts[2].strip()
            # Substitute {args} placeholder with any trailing argument
            if args:
                content = content.replace("{args}", args)
            self._agent_loop(content)
        except Exception as e:
            print(f"  Error running /{name}: {e}")

    def _cmd_list_commands(self):
        """List custom commands from .emelya/commands/."""
        if not self._custom_commands:
            print("  No custom commands. Create .md files in .emelya/commands/ to add /commands.")
            return
        print(f"  {t('available')}:")
        for cmd, info in self._custom_commands.items():
            print(f"    {cmd} — {info['desc']}")

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

    def _cmd_rename(self, name: str = ""):
        """Rename current session."""
        if not name:
            print(f"  Usage: /rename <new_name>")
            print(f"  Current: {self.session_name}")
            return

        old_name = self.session_name
        self.session_name = name
        session_dir = self._session_dir()

        # Save context to new location
        self.context.save(session_dir / "context.json")

        # Move log to new session dir
        self.log.event(f"Session renamed: {old_name} -> {name}")
        old_log_path = self.log.path
        self.log.close()
        import shutil
        new_log_path = session_dir / "log.txt"
        if old_log_path != new_log_path:
            shutil.copy2(old_log_path, new_log_path)
        self.log = SessionLog(new_log_path)

        print(f"  {t('session_saved')}: {self.session_name}")

    def _cmd_resume(self, name: str = ""):
        """Resume a previous session."""
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

        # Autosave current before switching
        self._autosave()
        self.log.event(f"Switching to session: {name}")
        self.log.close()

        self.session_name = name
        self.context.load(ctx_path)
        self.log = self._create_log()
        self.log.event(f"Session resumed: {name}")
        self.tool_ctx.files_read.clear()
        self.tool_ctx.loaded_skills.clear()
        self.input_history = InputHistory(str(self._session_dir() / "history"))

        tokens = self.context.estimate_tokens()
        n_msgs = len(self.context.messages)
        print(f"  {t('session_loaded')}: {name} ({n_msgs} messages, {tokens} tokens)")

    def _cmd_clear(self):
        """Clear conversation history — save current session, start fresh."""
        # Autosave current if it has content
        if self.context.messages:
            self._autosave()

        self.log.close()

        self.session_name = datetime.now().strftime("tmp%Y%m%d%H%M%S")
        self.context.clear()
        self.conversation_log.clear()
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
        print(f"  {t('context_cleared')} [{tokens} tokens]")

    def _cmd_branch(self, name: str = ""):
        """Create a branch — copy current conversation to a new session and switch to it."""
        branch_name = name or (self.session_name + "-branch-" + datetime.now().strftime("%H%M%S"))

        # Autosave current session first
        self._autosave()
        old_name = self.session_name

        # Switch to new session name
        self.session_name = branch_name
        session_dir = self._session_dir()

        # Save current context (full copy) to the new session
        self.context.save(session_dir / "context.json")

        # Start new log for the branch
        self.log.event(f"Branched to: {branch_name}")
        self.log.close()
        self.log = self._create_log()
        self.log.session_start(
            self.config.base_url, self.config.model,
            str(self.config.work_dir), self.session_name,
        )
        self.log.event(f"Branched from: {old_name}")
        self.input_history = InputHistory(str(session_dir / "history"))

        tokens = self.context.estimate_tokens()
        n_msgs = len(self.context.messages)
        print(f"  Branched: {old_name} → {branch_name} ({n_msgs} messages, {tokens} tokens)")

    def _cmd_export(self, path: str = ""):
        """Export full conversation to a readable text file, including all tool calls."""
        if not path:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = f"{self.session_name}-{ts}.md"

        export_path = self.config.work_dir / path

        lines = [f"# Conversation: {self.session_name}\n"]

        for entry in self.conversation_log:
            etype = entry["type"]
            if etype == "user":
                lines.append(f"## User\n\n{entry['content']}\n")
            elif etype == "assistant":
                lines.append(f"## Assistant\n\n{entry['content']}\n")
            elif etype == "tool_call":
                name = entry["name"]
                args = entry.get("args", {})
                if name == "edit":
                    old = args.get("old_string", "")
                    new = args.get("new_string", "")
                    lines.append(f"**● {t('tool_edit', path=args.get('path', ''))}**\n")
                    if old or new:
                        lines.append("```diff")
                        for l in old.splitlines():
                            lines.append(f"- {l}")
                        for l in new.splitlines():
                            lines.append(f"+ {l}")
                        lines.append("```\n")
                elif name == "write":
                    content = args.get("content", "")
                    n = content.count("\n") + 1 if content else 0
                    lines.append(f"**● {t('tool_write', path=args.get('path', ''))}** ({n} lines)\n")
                    preview = "\n".join(content.splitlines()[:20])
                    if content.count("\n") > 20:
                        preview += f"\n... ({content.count(chr(10)) + 1} lines total)"
                    lines.append(f"```\n{preview}\n```\n")
                elif name == "bash":
                    cmd = args.get("command", "")
                    desc = args.get("description", cmd)
                    lines.append(f"**● {t('tool_bash', desc=desc)}**\n```bash\n{cmd}\n```\n")
                elif name == "read":
                    lines.append(f"**● {t('tool_read', path=args.get('path', ''))}**\n")
                elif name == "grep":
                    lines.append(f"**● {t('tool_grep', pattern=args.get('pattern', ''))}**\n")
                elif name == "glob":
                    lines.append(f"**● {t('tool_glob', pattern=args.get('pattern', ''))}**\n")
                elif name == "agent":
                    lines.append(f"**● {t('tool_agent', name=args.get('name', ''))}** {json.dumps(args.get('args', {}), ensure_ascii=False)}\n")
                else:
                    lines.append(f"**● {t('tool_other', name=name)}** `{json.dumps(args, ensure_ascii=False)}`\n")
            elif etype == "tool_result":
                content = entry.get("content", "")
                if content.startswith("Error:"):
                    lines.append(f"❌ {content}\n")
                else:
                    # Abbreviate long results
                    preview = content[:1000]
                    if len(content) > 1000:
                        preview += f"\n... ({len(content)} chars total)"
                    lines.append(f"<details><summary>Result ({len(content)} chars)</summary>\n\n```\n{preview}\n```\n</details>\n")

        n_entries = len(self.conversation_log)
        text = "\n".join(lines)
        export_path.write_text(text, encoding="utf-8")
        print(f"  Exported: {export_path} ({n_entries} entries, {len(text)} chars)")

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
  {C.muted}Emelya — CLI AI Agent v{__version__}, Fairy-tale dimwit{C.reset}"""
        print(logo)
        print()
        print(f"  {C.info}{t('workdir')}{C.reset} {self.config.work_dir}")
        print(f"  {C.info}{t('session')}{C.reset} {self.session_name}")
        print()
