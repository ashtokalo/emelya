"""REPL — main interactive loop with agent cycle."""

import json
import platform
import sys
from datetime import datetime
from pathlib import Path

from config import Config
from context import Context
from input_history import InputHistory
from line_editor import LineEditor, strip_markers
from llm import LLMClient, LLMError
from log import SessionLog, collect_stats
from mcp import MCPClient, MCPError, compact_tool_description, load_mcp_config, load_cache, save_cache
from sandbox import Sandbox
from tools import ToolContext, ToolRegistry
from tools.skill import scan_skills
from tools.agent import scan_agents


class REPL:
    """Interactive REPL with tool-calling agent loop."""

    def __init__(self, config: Config):
        self.config = config
        self.context = Context(context_limit=config.context_limit)
        self.sandbox = Sandbox(config.work_dir)
        self.registry = ToolRegistry()

        src_dir = Path(__file__).parent
        self.app_dir = src_dir.parent  # aigent/ root
        self.skills_dir = self.app_dir / "skills"
        self.agents_dir = self.app_dir / "agents"
        self.sessions_dir = config.work_dir / ".aigent" / "sessions"

        # Models management
        self._models_path = config.work_dir / ".aigent" / "models.json"
        self._models = self._load_models()
        self.active_model = config.model_name or "local"
        self.llm = self._create_llm_client()

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

    # --- Model management ---

    def _load_models(self) -> dict:
        """Load models from .aigent/models.json. Always includes 'local' from CLI args."""
        models = {
            "local": {
                "base_url": self.config.base_url,
                "model": self.config.model,
            }
        }
        if self._models_path.exists():
            try:
                data = json.loads(self._models_path.read_text(encoding="utf-8"))
                models.update(data.get("models", {}))
            except (json.JSONDecodeError, OSError):
                pass
        return models

    def _save_models(self):
        """Save models config (excluding 'local' which comes from CLI)."""
        to_save = {k: v for k, v in self._models.items() if k != "local"}
        self._models_path.parent.mkdir(parents=True, exist_ok=True)
        self._models_path.write_text(
            json.dumps({"models": to_save}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _create_llm_client(self) -> LLMClient:
        """Create LLM client for the active model."""
        cfg = self._models.get(self.active_model)
        if not cfg:
            cfg = self._models.get("local", {})
            self.active_model = "local"
        # Apply model-specific context_limit if set
        model_ctx_limit = cfg.get("context_limit")
        if model_ctx_limit:
            self.context.context_limit = model_ctx_limit
        else:
            self.context.context_limit = self.config.context_limit
        return LLMClient(
            base_url=cfg.get("base_url", self.config.base_url),
            model=cfg.get("model", "default"),
            api_key=cfg.get("api_key"),
        )

    def _switch_model(self, name: str):
        """Switch to a different model."""
        if name not in self._models:
            print(f"  Unknown model: {name}")
            self._list_models()
            return
        prev = self.active_model
        self.active_model = name
        self.llm = self._create_llm_client()
        self.tool_ctx.llm_client = self.llm
        self.log.event(f"Model switched: {prev} -> {name} ({self.llm.base_url} / {self.llm.model})")
        ok, info = self.llm.check_connection()
        if ok:
            print(f"  Switched to: {name} ({info})")
        else:
            print(f"  Switched to: {name} (warning: {info})")

    def _list_models(self):
        for name, cfg in self._models.items():
            active = " *" if name == self.active_model else ""
            url = cfg.get("base_url", "?")
            model = cfg.get("model", "?")
            has_key = " [key]" if cfg.get("api_key") else ""
            ctx = cfg.get("context_limit")
            ctx_str = f" ctx:{ctx // 1000}K" if ctx else ""
            print(f"  {name}: {url} / {model}{has_key}{ctx_str}{active}")

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

        # Append project config if exists (aigent.md)
        if self.config.config_file.exists():
            try:
                custom = self.config.config_file.read_text(encoding="utf-8").strip()
                if custom:
                    prompt += f"\n\nProject instructions ({self.config.config_file.name}):\n{custom}"
            except Exception:
                pass

        # Append local config if exists (.aigent/config.md — personal, not in repo)
        local_config = self.config.work_dir / ".aigent" / "config.md"
        if local_config.exists():
            try:
                local = local_config.read_text(encoding="utf-8").strip()
                if local:
                    prompt += f"\n\nLocal instructions ({local_config.name}):\n{local}"
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
        url = f"{self.llm.base_url}/v1/chat/completions"
        self.log.request(url, messages, tools)

        tokens = self.context.estimate_tokens()
        prefix = f"\nAigent({self.active_model}, {tokens} tok): "

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

        self.log.response(
            response.get("content"), response.get("tool_calls"),
            usage=response.get("usage"), model=self.llm.model,
        )
        return response

    def _agent_loop(self, user_input: str):
        """Run the agent loop with work context isolation.

        Tool calls and results live in a temporary work context.
        Only user input and final text response go into the main context.
        """
        self.context.add_user(user_input)

        # No auto-trim — user controls context size via /compact and /clear

        # First call — from main context (update usage for accurate token tracking)
        tools = self.registry.get_tools()
        try:
            response = self._llm_call(self.context.get_messages(), tools)
            self.context.update_usage(response.get("usage"))
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
        consecutive_errors = 0

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

                if result.startswith("Error:"):
                    consecutive_errors += 1
                else:
                    consecutive_errors = 0

            # Break if too many consecutive errors (model is stuck)
            if consecutive_errors >= 3:
                work_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_calls[-1]["id"] if tool_calls else "",
                    "content": (
                        "STOP: 3 consecutive errors. Your arguments are being truncated. "
                        "Change approach: use smaller edits, split work into multiple steps, "
                        "or write a helper script via bash."
                    ),
                })
                consecutive_errors = 0


            # Progressive nudges — inserted BEFORE the LLM call, AFTER all tool results
            remaining = self.config.max_iterations - iteration - 1
            if remaining == 3:
                work_messages.append({
                    "role": "user",
                    "content": "IMPORTANT: Only 3 steps left. Start composing your answer.",
                })
            elif remaining == 1:
                work_messages.append({
                    "role": "user",
                    "content": "LAST STEP. Give your final answer NOW. Do not call any more tools.",
                })

            # Next LLM call in work context (refresh tools — load_tools may have added new ones)
            tools = self.registry.get_tools()
            try:
                response = self._llm_call(work_messages, tools)
            except LLMError as e:
                print(f"\nError: {e}")
                self.log.event(f"Error: {e}")
                final_text = final_text or f"(Error during processing: {e})"
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
            # Max iterations — force final response without tools
            print("\n  (max iterations, forcing answer...)")
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
                    print(f"\nAigent({self.active_model}, {tokens} tok): {final_text}")
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
            print(f"  Unknown model or command: {subcmd}")
            print("  Usage: /model, /model <name>, /model add <name> <url> <model> [api_key], /model remove <name>")

    # Known input context limits for auto-detection
    _KNOWN_LIMITS = {
        "api.deepseek.com": 64000,   # V3: 64K input (1M total with cache)
        "api.openai.com": 128000,    # GPT-4o: 128K
        "openrouter.ai": 128000,     # varies by model
    }

    def _model_add(self, arg: str):
        if not arg:
            print("  Usage: /model add <name> <base_url> <model> [api_key] [--context-limit N]")
            print("  Providers (base_url):")
            print("    Local llama.cpp:  http://localhost:8080")
            print("    OpenAI:           https://api.openai.com")
            print("    DeepSeek:         https://api.deepseek.com")
            print("    OpenRouter:       https://openrouter.ai/api")
            print("  Examples:")
            print("    /model add gpt4 https://api.openai.com gpt-4o sk-...")
            print("    /model add deepseek https://api.deepseek.com deepseek-chat sk-...")
            print("    /model add local8k http://localhost:8080 default --context-limit 8000")
            return

        parts = arg.split()
        # Extract --context-limit
        ctx_limit = None
        filtered = []
        i = 0
        while i < len(parts):
            if parts[i] == "--context-limit" and i + 1 < len(parts):
                try:
                    ctx_limit = int(parts[i + 1])
                except ValueError:
                    pass
                i += 2
            else:
                filtered.append(parts[i])
                i += 1

        if len(filtered) < 3:
            print("  Error: need at least name, base_url, and model")
            return

        name = filtered[0]
        base_url = filtered[1]
        model = filtered[2]
        api_key = filtered[3] if len(filtered) > 3 else None

        # Auto-detect context limit from known providers
        if ctx_limit is None:
            for domain, limit in self._KNOWN_LIMITS.items():
                if domain in base_url:
                    ctx_limit = limit
                    break

        entry = {"base_url": base_url, "model": model}
        if api_key:
            entry["api_key"] = api_key
        if ctx_limit:
            entry["context_limit"] = ctx_limit

        self._models[name] = entry
        self._save_models()

        # Test connection
        client = LLMClient(base_url=base_url, model=model, api_key=api_key)
        ok, msg = client.check_connection()
        status = "connected" if ok else f"warning: {msg}"
        print(f"  Added: {name} ({base_url} / {model}) — {status}")

    def _model_remove(self, name: str):
        if not name:
            print("  Usage: /model remove <name>")
            return
        if name == "local":
            print("  Cannot remove 'local' model")
            return
        if name not in self._models:
            print(f"  Unknown model: {name}")
            return
        if name == self.active_model:
            self._switch_model("local")
        del self._models[name]
        self._save_models()
        print(f"  Removed: {name}")

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
        self.input_history = InputHistory(str(self._session_dir() / "history"))

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
        self.input_history = InputHistory(str(self._session_dir() / "history"))

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
        mcp_config = getattr(self, "_mcp_config", {})

        if not mcp_config and not self._mcp_clients:
            print("  No MCP servers configured")
            print(f"  Config: {config_path}")
            print("  Add: /mcp add <name> <command> [args...]")
            return

        # Show all configured servers (connected or cached)
        for name in sorted(set(list(mcp_config.keys()) + list(self._mcp_clients.keys()))):
            category = f"mcp:{name}"
            n_ext = len(self.registry._extensions.get(category, {}))

            if name in self._mcp_clients:
                client = self._mcp_clients[name]
                running = client._process and client._process.poll() is None
                status = "connected" if running else "stopped"
                info = client.server_info or {}
                server_name = info.get("name", "")
                extra = f" ({server_name})" if server_name else ""
            else:
                status = "cached" if n_ext > 0 else "not cached"
                extra = ""

            loaded = " [loaded]" if category in self.registry._loaded else ""
            print(f"  {name}: {status}, {n_ext} tools{extra}{loaded}")

        print(f"  Config: {config_path}")

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

    def _print_stats(self):
        """Show token usage stats across all sessions."""
        stats = collect_stats(self.sessions_dir)
        totals = stats["totals"]

        if totals["requests"] == 0:
            print("  No usage data yet")
            return

        def fmt(n):
            if n >= 1_000_000:
                return f"{n / 1_000_000:.1f}M"
            if n >= 1_000:
                return f"{n / 1_000:.1f}K"
            return str(n)

        print(f"\n  === Token Usage Stats ===\n")

        # By model
        print("  By model:")
        for model, m in sorted(stats["by_model"].items()):
            print(f"    {model}: {fmt(m['total'])} tokens"
                  f" ({fmt(m['prompt'])} prompt + {fmt(m['completion'])} completion)"
                  f" / {m['requests']} requests")

        # By date
        print(f"\n  By date:")
        for date, d in stats["by_date"].items():
            print(f"    {date}: {fmt(d['total'])} tokens / {d['requests']} requests")

        # Totals
        print(f"\n  Total: {fmt(totals['total'])} tokens"
              f" ({fmt(totals['prompt'])} prompt + {fmt(totals['completion'])} completion)"
              f" / {totals['requests']} requests")

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
        print(f"  Model:   {self.active_model} ({self.llm.base_url} / {self.llm.model})")
        print(f"  WorkDir: {self.config.work_dir}")
        print(f"  Session: {self.session_name}")
        print(f"  Stream:  {'on' if self.config.stream else 'off'}")
        print("=" * 50)

        # Check connection (non-fatal — user can switch model later)
        print(f"\nChecking {self.active_model}...")
        ok, msg = self.llm.check_connection()
        if ok:
            print(f"Connected ({msg})")
        else:
            print(f"Warning: {msg}")
            print(f"Use /model to switch to another model")
        print("\nCommands: /clear /compact /undo /drop /save /load /new /model /tools /mcp /stats /context /history /exit")
        print("-" * 50)

        self.log.session_start(
            self.config.base_url, self.config.model,
            str(self.config.work_dir), self.session_name,
        )

        # Interactive line editor with per-session history
        self.input_history = InputHistory(str(self._session_dir() / "history"))
        editor = LineEditor(work_dir=self.config.work_dir)

        while True:
            try:
                print()  # blank line before prompt
                user_input = editor.read(prompt="You: ", history=self.input_history)
            except (EOFError, KeyboardInterrupt):
                print("\n\nBye!")
                break

            if user_input is None:  # Ctrl+C / Escape
                continue
            user_input = user_input.strip()
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
                    print("  Available: /clear /compact /undo /drop /save /load /new /model /tools /mcp /stats /context /history /exit")
                continue

            # Agent loop
            try:
                self._agent_loop(user_input)
            except KeyboardInterrupt:
                print("\n  (interrupted)")

        self._shutdown_mcp()
        self.log.session_end()
        self.log.close()
