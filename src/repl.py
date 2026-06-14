"""REPL — main interactive loop with agent cycle."""

import json
import platform
import sys
from datetime import datetime
from pathlib import Path

from config import Config
from context import Context
from llm import LLMClient, LLMError
from log import SessionLog
from mcp import MCPClient, MCPError, compact_tool_description, load_mcp_config, load_cache, save_cache
from sandbox import Sandbox
from tools import ToolContext, ToolRegistry
from tools.skill import scan_skills
from tools.agent import scan_agents


class REPL:
    """Interactive REPL with tool-calling agent loop."""

    def __init__(self, config: Config):
        self.config = config
        self.llm = LLMClient(
            base_url=config.base_url,
            model=config.model,
        )
        self.context = Context(context_limit=config.context_limit)
        self.sandbox = Sandbox(config.work_dir)
        self.registry = ToolRegistry()

        src_dir = Path(__file__).parent
        self.app_dir = src_dir.parent  # aigent/ root
        self.skills_dir = self.app_dir / "skills"
        self.agents_dir = self.app_dir / "agents"
        self.sessions_dir = config.work_dir / ".aigent" / "sessions"

        # Session management
        self.session_name = datetime.now().strftime("tmp%Y%m%d%H%M%S")
        self.log = self._create_log()

        # Tool context shared across calls
        self.tool_ctx = ToolContext(
            work_dir=config.work_dir,
            sandbox=self.sandbox,
            confirm=self._confirm,
            skills_dir=self.skills_dir,
            agents_dir=self.agents_dir,
            llm_client=self.llm,
            config=config,
        )

        # Load tools
        self.registry.load_builtin(src_dir)
        self.tool_ctx.tool_registry = self.registry

        # Load MCP servers (register as extension categories)
        self._mcp_clients = {}
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

    def _init_mcp(self):
        """Register MCP servers from cache. No connections at startup.

        Servers connect lazily when load_tools("mcp:name") is called.
        """
        aigent_dir = self.config.work_dir / ".aigent"
        config_path = aigent_dir / "mcp.json"
        cache_dir = aigent_dir / "mcp_cache"

        self._mcp_config = load_mcp_config(config_path)
        if not self._mcp_config:
            return

        for name, cfg in self._mcp_config.items():
            cached = load_cache(cache_dir, name)
            if cached:
                # Register from cache with a lazy executor
                self._register_mcp_lazy(name, cached)
                print(f"  MCP: {name} ({len(cached)} tools, cached)")
            else:
                # No cache — need to connect now to discover tools
                self._mcp_connect(name, cfg)

    def _register_mcp_lazy(self, server_name: str, cached_tools: list):
        """Register MCP tools with lazy connection — connects on first call."""
        category = f"mcp:{server_name}"
        if category not in self.registry._extensions:
            self.registry._extensions[category] = {}

        for tool in cached_tools:
            tool_name = tool.get("name", "")
            if not tool_name:
                continue
            definition = {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": tool.get("description", tool_name),
                    "parameters": tool.get("inputSchema", {
                        "type": "object", "properties": {},
                    }),
                },
            }

            def _make_lazy_executor(srv_name, t_name):
                def executor(args, ctx):
                    # Connect on first call if not connected
                    if srv_name not in self._mcp_clients:
                        cfg = self._mcp_config.get(srv_name)
                        if not cfg:
                            return f"Error: MCP server '{srv_name}' not configured"
                        print(f"\n  Connecting to MCP {srv_name}...", flush=True)
                        try:
                            self._mcp_connect(srv_name, cfg)
                        except MCPError as e:
                            return f"Error connecting to {srv_name}: {e}"
                    client = self._mcp_clients.get(srv_name)
                    if not client:
                        return f"Error: MCP server '{srv_name}' not connected"
                    try:
                        return client.call_tool(t_name, args)
                    except Exception as e:
                        return f"Error: MCP {srv_name}/{t_name}: {e}"
                return executor

            self.registry._extensions[category][tool_name] = {
                "definition": definition,
                "execute": _make_lazy_executor(server_name, tool_name),
                "description": tool.get("description", tool_name),
            }

    def _mcp_connect(self, name: str, cfg: dict):
        """Connect to an MCP server and register its tools."""
        command = cfg.get("command", "")
        if not command:
            return
        status_cb = lambda msg: print(f"  MCP {name}: {msg}", flush=True)
        client = MCPClient(
            name=name, command=command,
            args=cfg.get("args", []),
            env=cfg.get("env"),
            work_dir=str(self.config.work_dir),
        )
        client.start(timeout=30, on_status=status_cb)
        self._mcp_clients[name] = client

        # Cache tools
        cache_dir = self.config.work_dir / ".aigent" / "mcp_cache"
        cached = [compact_tool_description(t) for t in client.tools]
        save_cache(cache_dir, name, cached)

        # Re-register with live executor
        self.registry.register_mcp_server(name, cached, client)

    def _shutdown_mcp(self):
        """Stop all MCP server processes."""
        for name, client in self._mcp_clients.items():
            try:
                client.stop()
            except Exception:
                pass
        self._mcp_clients.clear()

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

        # Append project config if exists
        if self.config.config_file.exists():
            try:
                custom = self.config.config_file.read_text(encoding="utf-8").strip()
                if custom:
                    prompt += f"\n\nProject instructions ({self.config.config_file.name}):\n{custom}"
            except Exception:
                pass

        self.context.set_system(prompt)

    def _confirm(self, description: str, detail: str = "") -> bool:
        """Ask user for confirmation. Respects confirm_mode.
        Note: tool call details are already printed by _print_tool_call,
        so this only shows the prompt."""
        if self.config.confirm_mode == "no_confirm":
            return True
        try:
            answer = input("  Allow? (y/n): ").strip().lower()
            return answer in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    def _print_tool_call(self, tc: dict):
        """Display tool call to user."""
        name = tc["name"]
        args = tc["args"]
        if name == "bash":
            desc = args.get("description", "")
            cmd = args.get("command", "")
            print(f"\n  bash: {desc}")
            print(f"  $ {cmd}")
        elif name == "read":
            path = args.get("path", "")
            print(f"\n  read: {path}")
        elif name == "write":
            path = args.get("path", "")
            print(f"\n  write: {path}")
        elif name == "edit":
            path = args.get("path", "")
            print(f"\n  edit: {path}")
        elif name == "glob":
            pattern = args.get("pattern", "")
            print(f"\n  glob: {pattern}")
        elif name == "grep":
            pattern = args.get("pattern", "")
            include = args.get("include", "")
            extra = f" ({include})" if include else ""
            print(f"\n  grep: {pattern}{extra}")
        elif name == "skill":
            print(f"\n  skill: {args.get('name', '')}")
        elif name == "agent":
            print(f"\n  agent: {args.get('name', '')}")
        else:
            print(f"\n  {name}: {json.dumps(args, ensure_ascii=False)}")

    def _print_tool_result(self, name: str, result: str):
        """Display tool result (abbreviated)."""
        lines = result.split("\n")
        if len(lines) > 15:
            preview = "\n".join(lines[:10])
            print(f"  {preview}")
            print(f"  ... ({len(lines)} lines total)")
        else:
            for line in lines:
                print(f"  {line}")

    def _llm_call(self, messages, tools):
        """Single LLM call with streaming support. Returns response dict."""
        url = f"{self.config.base_url}/v1/chat/completions"
        self.log.request(url, messages, tools)

        tokens = self.context.estimate_tokens()
        prefix = f"\nAigent ({tokens} tokens): "

        if self.config.stream:
            prefix_printed = False

            def _on_content(text):
                nonlocal prefix_printed
                if not prefix_printed:
                    print(prefix, end="", flush=True)
                    prefix_printed = True
                print(text, end="", flush=True)

            response = self.llm.stream_chat(
                messages=messages,
                tools=tools,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                on_content=_on_content,
            )
            if prefix_printed:
                print()  # newline after streamed content
        else:
            response = self.llm.chat(
                messages=messages,
                tools=tools,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            if response.get("content"):
                print(f"{prefix}{response['content']}")

        self.log.response(response.get("content"), response.get("tool_calls"))
        # Update real token counts if available
        self.context.update_usage(response.get("usage"))
        return response

    def _agent_loop(self, user_input: str):
        """Run the agent loop with work context isolation.

        Tool calls and results live in a temporary work context.
        Only user input and final text response go into the main context.
        """
        self.context.add_user(user_input)

        # Trim context if needed
        self.context.trim()

        # First call — from main context
        tools = self.registry.get_tools()
        try:
            response = self._llm_call(self.context.get_messages(), tools)
        except (LLMError, KeyboardInterrupt) as e:
            # No response received — remove orphaned user message
            self.context.pop_last_user()
            if isinstance(e, KeyboardInterrupt):
                print("\n  (interrupted)")
                self.log.event("Interrupted by user")
            else:
                print(f"\nError: {e}")
                self.log.event(f"Error: {e}")
            return

        tool_calls = response.get("tool_calls")
        if not tool_calls:
            # Simple text response — save directly to main context
            self.context.add_assistant_text(response.get("content") or "")
            return

        # Has tool calls — switch to work context
        work_messages = list(self.context.get_messages())
        work_messages.append(response["raw"])
        final_text = response.get("content") or ""

        for iteration in range(self.config.max_iterations):
            # Execute tool calls
            for tc in tool_calls:
                self._print_tool_call(tc)

                import time as _time
                t0 = _time.monotonic()
                try:
                    result = self.registry.execute(tc["name"], tc["args"], self.tool_ctx)
                except KeyboardInterrupt:
                    result = "Cancelled by user"
                elapsed = int((_time.monotonic() - t0) * 1000)

                self.log.tool_exec(tc["name"], tc["args"], result, elapsed)
                self._print_tool_result(tc["name"], result)
                work_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

            # Next LLM call in work context (refresh tools — load_tools may have added new ones)
            tools = self.registry.get_tools()
            try:
                response = self._llm_call(work_messages, tools)
            except LLMError as e:
                print(f"\nError: {e}")
                self.log.event(f"Error: {e}")
                break
            except KeyboardInterrupt:
                print("\n  (interrupted)")
                self.log.event("Interrupted by user")
                break

            work_messages.append(response["raw"])
            if response.get("content"):
                final_text = response["content"]

            tool_calls = response.get("tool_calls")
            if not tool_calls:
                break  # Final text response
        else:
            # Max iterations — force final response
            print("\n  (max iterations reached, finishing...)")
            work_messages.append({
                "role": "user",
                "content": "Please provide your final answer now.",
            })
            try:
                response = self.llm.chat(
                    messages=work_messages, tools=None,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                )
                if response.get("content"):
                    final_text = response["content"]
                    print(f"\nAigent: {final_text}")
            except Exception as e:
                print(f"\nError: {e}")

        # Only the final text goes into main context
        if final_text:
            self.context.add_assistant_text(final_text)

        # Autosave session
        self._autosave()

    def _autosave(self):
        """Save context to current session directory."""
        try:
            self.context.save(self._session_dir() / "context.json")
        except Exception:
            pass

    def _handle_command(self, command: str) -> bool:
        """Handle /commands. Returns True if handled."""
        if command == "/clear":
            self.context.clear()
            self.tool_ctx.files_read.clear()
            self.tool_ctx.loaded_skills.clear()
            tokens = self.context.estimate_tokens()
            print(f"  Context cleared [{tokens} tokens]")
            return True

        if command == "/undo":
            result = self.tool_ctx.undo()
            print(f"  {result}")
            return True

        if command == "/compact":
            tokens_before = self.context.estimate_tokens()
            print("  Compacting context...")
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
            print(f"  Loaded tools: {', '.join(self.registry.names)}")
            extensions = self.registry.get_extension_summary()
            if extensions:
                print(f"  Extensions (via load_tools):")
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

        # Commands with arguments
        parts = command.split(None, 1)
        cmd = parts[0]
        arg = parts[1].strip() if len(parts) > 1 else ""

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

        return False

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

        print(f"  Session saved: {self.session_name}")
        print(f"  {session_dir}")

    def _cmd_load(self, name: str = ""):
        """Load a saved session."""
        if not name:
            # List available sessions
            if not self.sessions_dir.is_dir():
                print("  No saved sessions")
                return
            sessions = sorted(d.name for d in self.sessions_dir.iterdir() if d.is_dir())
            if not sessions:
                print("  No saved sessions")
                return
            print("  Available sessions:")
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
            print(f"  Session not found: {name}")
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

        tokens = self.context.estimate_tokens()
        n_msgs = len(self.context.messages)
        print(f"  Session loaded: {name} ({n_msgs} messages, {tokens} tokens)")

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

        tokens = self.context.estimate_tokens()
        print(f"  New session: {self.session_name} [{tokens} tokens]")

    def _cmd_mcp(self, arg: str = ""):
        """Handle /mcp subcommands: (status), add, remove, restart."""
        parts = arg.split(None, 1) if arg else []
        subcmd = parts[0] if parts else ""
        subarg = parts[1].strip() if len(parts) > 1 else ""

        if subcmd == "add":
            self._mcp_add(subarg)
        elif subcmd == "remove":
            self._mcp_remove(subarg)
        elif subcmd in ("restart", "refresh"):
            self._mcp_restart(subarg)
        elif subcmd == "":
            self._mcp_status()
        else:
            print(f"  Unknown: /mcp {subcmd}")
            print("  Usage: /mcp, /mcp add, /mcp remove <name>, /mcp refresh [name]")

    def _mcp_status(self):
        config_path = self.config.work_dir / ".aigent" / "mcp.json"
        if not self._mcp_clients:
            print("  No MCP servers connected")
            print(f"  Config: {config_path}")
            print("  Add: /mcp add <name> <command> [args...]")
            return
        for name, client in self._mcp_clients.items():
            info = client.server_info or {}
            n_tools = len(client.tools)
            running = client._process and client._process.poll() is None
            status = "running" if running else "stopped"
            loaded = f" [loaded]" if f"mcp:{name}" in self.registry._loaded else ""
            print(f"  {name}: {status}, {n_tools} tools ({info.get('name', '?')}){loaded}")

    def _mcp_add(self, arg: str):
        if not arg:
            print("  Usage: /mcp add <name> <command> [args...]")
            print("  Options: --env KEY=VALUE (can repeat)")
            print("  Examples:")
            print("    /mcp add playwright npx -y @playwright/mcp@latest")
            print("    /mcp add myserver python3 /path/to/server.py")
            print("    /mcp add remote npx mcp-remote https://example.com/mcp --header Auth: token")
            print("    /mcp add gemini node server.mjs --env GEMINI_API_KEY=xxx")
            return

        # Parse with shell quoting support
        import shlex
        try:
            raw_parts = shlex.split(arg)
        except ValueError:
            raw_parts = arg.split()
        env = {}
        filtered = []
        i = 0
        while i < len(raw_parts):
            if raw_parts[i] == "--env" and i + 1 < len(raw_parts):
                kv = raw_parts[i + 1]
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    env[k] = v
                i += 2
            else:
                filtered.append(raw_parts[i])
                i += 1

        if len(filtered) < 2:
            print("  Error: need at least a name and command")
            return
        name = filtered[0]
        command = filtered[1]
        args = filtered[2:] if len(filtered) > 2 else []

        # Save to config
        aigent_dir = self.config.work_dir / ".aigent"
        config_path = aigent_dir / "mcp.json"
        config = load_mcp_config(config_path)
        entry = {"command": command, "args": args}
        if env:
            entry["env"] = env
        config[name] = entry
        aigent_dir.mkdir(parents=True, exist_ok=True)
        import json as _json
        config_path.write_text(
            _json.dumps({"servers": config}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Connect with progress
        cache_dir = aigent_dir / "mcp_cache"
        status_cb = lambda msg: print(f"  [{name}] {msg}", flush=True)
        try:
            client = MCPClient(name=name, command=command, args=args,
                               env=env or None,
                               work_dir=str(self.config.work_dir))
            client.start(timeout=60, on_status=status_cb)
            self._mcp_clients[name] = client

            cached = [compact_tool_description(t) for t in client.tools]
            save_cache(cache_dir, name, cached)
            self.registry.register_mcp_server(name, cached, client)
            self._update_dynamic_descriptions()

            tool_names = [t["name"] for t in cached]
            print(f"  Added: {name} ({len(cached)} tools: {', '.join(tool_names)})")
        except MCPError as e:
            print(f"  Error: {e}")
            print("  Saved to config. Fix the issue and try /mcp restart")

    def _mcp_remove(self, name: str):
        if not name:
            print("  Usage: /mcp remove <name>")
            return

        # Stop client
        if name in self._mcp_clients:
            self._mcp_clients[name].stop()
            del self._mcp_clients[name]

        # Remove from config
        config_path = self.config.work_dir / ".aigent" / "mcp.json"
        config = load_mcp_config(config_path)
        if name in config:
            del config[name]
            import json as _json
            config_path.write_text(
                _json.dumps({"servers": config}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        # Remove cache
        cache_file = self.config.work_dir / ".aigent" / "mcp_cache" / f"{name}.json"
        if cache_file.exists():
            cache_file.unlink()

        print(f"  Removed: {name}")

    def _mcp_restart(self, name: str = ""):
        """Refresh MCP server(s): reconnect and update cache."""
        config = load_mcp_config(self.config.work_dir / ".aigent" / "mcp.json")
        names = [name] if name else list(config.keys())
        if not names:
            print("  No MCP servers configured")
            return

        for n in names:
            # Stop if running
            if n in self._mcp_clients:
                self._mcp_clients[n].stop()
                del self._mcp_clients[n]

            cfg = config.get(n)
            if not cfg:
                print(f"  {n}: not in config")
                continue

            try:
                self._mcp_connect(n, cfg)
                self._update_dynamic_descriptions()
                n_tools = len(self._mcp_clients[n].tools)
                print(f"  {n}: refreshed ({n_tools} tools, cache updated)")
            except MCPError as e:
                print(f"  {n}: failed — {e}")

        self._update_dynamic_descriptions()

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

    def run(self):
        """Main REPL loop."""
        print("=" * 50)
        print("Aigent — CLI AI Agent")
        print(f"  Server:  {self.config.base_url}")
        print(f"  Model:   {self.config.model}")
        print(f"  WorkDir: {self.config.work_dir}")
        print(f"  Session: {self.session_name}")
        print(f"  Stream:  {'on' if self.config.stream else 'off'}")
        print("=" * 50)

        # Check connection
        print("\nChecking server connection...")
        ok, msg = self.llm.check_connection()
        if not ok:
            print(f"Error: {msg}")
            print(f"Make sure llama-server is running on {self.config.base_url}")
            sys.exit(1)
        print(f"Connected ({msg})")
        print("\nCommands: /clear /compact /undo /save /load /new /tools /mcp /context /history /exit")
        print("-" * 50)

        self.log.session_start(
            self.config.base_url, self.config.model,
            str(self.config.work_dir), self.session_name,
        )

        while True:
            try:
                user_input = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n\nBye!")
                break

            if not user_input:
                continue

            # Handle /commands — anything starting with / is a command, never sent to model
            if user_input.startswith("/"):
                if user_input in ("/exit", "/quit"):
                    print("\nBye!")
                    break
                if not self._handle_command(user_input):
                    cmd = user_input.split()[0]
                    print(f"  Unknown command: {cmd}")
                    print("  Available: /clear /compact /undo /save /load /new /tools /mcp /context /history /exit")
                continue

            # Agent loop
            try:
                self._agent_loop(user_input)
            except KeyboardInterrupt:
                print("\n  (interrupted)")

        self._shutdown_mcp()
        self.log.session_end()
        self.log.close()
