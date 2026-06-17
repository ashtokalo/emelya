"""REPL — main interactive loop with agent cycle."""

import json
import platform
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from config import Config
from i18n import t
from theme import C
from context import Context
from input_history import InputHistory
from line_editor import LineEditor, strip_markers
from llm import LLMClient, LLMError
from log import SessionLog, UsageTracker, collect_stats
from mcp import MCPClient, MCPError, compact_tool_description, load_mcp_config, load_cache, save_cache
from sandbox import Sandbox
from tools import ToolContext, ToolRegistry
from tools.skill import scan_skills
from tools.agent import scan_agents


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


class REPL:
    """Interactive REPL with tool-calling agent loop."""

    def __init__(self, config: Config):
        self.config = config
        self.context = Context(context_limit=config.context_limit)
        self.sandbox = Sandbox(config.work_dir)
        self.registry = ToolRegistry()

        src_dir = Path(__file__).parent
        self.app_dir = src_dir.parent  # emelya/ root
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

    # --- Model management ---

    def _load_models(self) -> dict:
        """Load models and routing from .emelya/models.json."""
        models = {
            "local": {
                "base_url": self.config.base_url,
                "model": self.config.model,
            }
        }
        self._routing = {}  # operation → [model_name, ...]
        if self._models_path.exists():
            try:
                data = json.loads(self._models_path.read_text(encoding="utf-8"))
                models.update(data.get("models", {}))
                self._routing = data.get("routing", {})
            except (json.JSONDecodeError, OSError):
                pass
        return models

    def _save_models(self):
        """Save models config (excluding 'local' which comes from CLI)."""
        to_save = {k: v for k, v in self._models.items() if k != "local"}
        data = {"models": to_save}
        if self._routing:
            data["routing"] = self._routing
        self._models_path.parent.mkdir(parents=True, exist_ok=True)
        self._models_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _get_client(self, model_name: str) -> LLMClient:
        """Get or create LLM client for a model by name. Cached."""
        if not hasattr(self, "_client_cache"):
            self._client_cache = {}
        if model_name in self._client_cache:
            return self._client_cache[model_name]
        cfg = self._models.get(model_name)
        if not cfg:
            return None
        client = LLMClient(
            base_url=cfg.get("base_url", self.config.base_url),
            model=cfg.get("model", "default"),
            api_key=cfg.get("api_key"),
        )
        self._client_cache[model_name] = client
        return client

    def _call_with_routing(self, operation: str, messages, tools, **kwargs):
        """Call LLM with failover chain based on routing config.
        Falls back to self.llm if no routing for operation."""
        chain = self._routing.get(operation, self._routing.get("default"))
        if not chain:
            # No routing — use active model directly
            if self.config.stream:
                return self.llm.stream_chat(messages, tools, **kwargs)
            return self.llm.chat(messages, tools, **kwargs)

        last_err = None
        for model_name in chain:
            client = self._get_client(model_name)
            if not client:
                continue
            try:
                # Quick health check (1s timeout)
                ok, _ = client.check_connection()
                if not ok:
                    continue
                # Use streaming for default operation
                if operation == "default" and self.config.stream:
                    resp = client.stream_chat(messages, tools, **kwargs)
                else:
                    resp = client.chat(messages, tools, **kwargs)
                # Update display name from response
                mid = resp.get("model_id")
                if mid:
                    display = mid
                    for suffix in (".gguf", ".bin"):
                        if display.endswith(suffix):
                            display = display[:-len(suffix)]
                    self._model_display = display
                return resp
            except Exception as e:
                last_err = e
                continue

        raise LLMError(f"All models in routing chain '{operation}' failed: {last_err}")

    def _resolve_client_for_operation(self, operation: str):
        """Resolve LLM client for a given operation using routing chain.
        Returns (client, model_name) or (self.llm, self.active_model)."""
        if self._manual_model or not self._routing:
            return self.llm, self.active_model
        chain = self._routing.get(operation, self._routing.get("default"))
        if not chain:
            return self.llm, self.active_model
        for model_name in chain:
            client = self._get_client(model_name)
            if not client:
                continue
            try:
                ok, _ = client.check_connection()
                if ok:
                    return client, model_name
            except Exception:
                continue
        return self.llm, self.active_model

    def _create_llm_client(self) -> LLMClient:
        """Create LLM client for the active model."""
        cfg = self._models.get(self.active_model)
        if not cfg:
            cfg = self._models.get("local", {})
            self.active_model = "local"
        # Apply context_limit: explicit > known provider > CLI default
        model_ctx_limit = cfg.get("context_limit")
        if not model_ctx_limit:
            base_url = cfg.get("base_url", "")
            for domain, limit in self._KNOWN_LIMITS.items():
                if domain in base_url:
                    model_ctx_limit = limit
                    break
        self.context.context_limit = model_ctx_limit or self.config.context_limit
        self._use_compact_tools = self.context.context_limit < 32000
        # Apply max_tokens: per-model > CLI default
        self.config.max_tokens = cfg.get("max_tokens") or self.config.max_tokens
        return LLMClient(
            base_url=cfg.get("base_url", self.config.base_url),
            model=cfg.get("model", "default"),
            api_key=cfg.get("api_key"),
        )

    def _switch_model(self, name: str):
        """Switch to a specific model. Disables routing (manual override)."""
        if name == "auto":
            # Re-enable routing
            self._manual_model = False
            print(f"  {C.info}Routing enabled{C.reset}")
            return
        if name not in self._models:
            print(f"  {t('unknown_model')}: {name}")
            self._list_models()
            return
        prev = self.active_model
        self.active_model = name
        self._model_display = name
        self._manual_model = True  # disable routing
        self.llm = self._create_llm_client()
        self.tool_ctx.llm_client = self.llm
        self.log.event(f"Model switched: {prev} -> {name} ({self.llm.base_url} / {self.llm.model})")
        ok, info = self.llm.check_connection()
        if ok:
            self._update_model_display(info)
            # Apply detected context limit if no explicit override
            detected = getattr(self.llm, "detected_context_limit", None)
            cfg = self._models.get(name, {})
            if detected and not cfg.get("context_limit"):
                self.context.context_limit = detected
            print(f"  {t('switched_to')}: {self._model_display}, ctx:{self.context.context_limit // 1000}K")
        else:
            print(f"  {t('switched_to')}: {name} ({t('warning')}: {info})")

    def _update_model_display(self, info: str = ""):
        """Extract short model name from connection info or API response."""
        # From check_connection: "models: Qwen3.5-4B-Q5_K_M.gguf"
        if info.startswith("models: "):
            name = info[8:].split(",")[0].strip()
            # Strip common suffixes for cleaner display
            for suffix in (".gguf", ".bin"):
                if name.endswith(suffix):
                    name = name[:-len(suffix)]
            self._model_display = name

    def _list_models(self):
        for name, cfg in self._models.items():
            active = " *" if name == self.active_model and self._manual_model else ""
            url = cfg.get("base_url", "?")
            model = cfg.get("model", "?")
            has_key = " [key]" if cfg.get("api_key") else ""
            ctx = cfg.get("context_limit")
            ctx_str = f" ctx:{ctx // 1000}K" if ctx else ""
            print(f"  {name}: {url} / {model}{has_key}{ctx_str}{active}")
        if self._routing:
            mode = "manual" if self._manual_model else "auto"
            print(f"  {C.muted}Routing: {mode} (/model auto to enable, /model <name> to override){C.reset}")
            if not self._manual_model:
                for op, chain in self._routing.items():
                    print(f"    {op}: {' → '.join(chain)}")

    def _init_mcp(self):
        """Register MCP servers from cache. No connections at startup.

        Servers connect lazily when load_tools("mcp:name") is called.
        """
        app_data_dir = self.config.work_dir / ".emelya"
        config_path = app_data_dir / "mcp.json"
        cache_dir = app_data_dir / "mcp_cache"

        self._mcp_config = load_mcp_config(config_path)
        if not self._mcp_config:
            return

        for name, cfg in self._mcp_config.items():
            cached = load_cache(cache_dir, name)
            if cached:
                # Register from cache with a lazy executor
                self._register_mcp_lazy(name, cached)
                self._mcp_startup_info.append((name, len(cached), True))
            else:
                try:
                    self._mcp_connect(name, cfg)
                    n = len(self._mcp_clients[name].tools)
                    self._mcp_startup_info.append((name, n, False))
                except MCPError as e:
                    self._mcp_startup_info.append((name, 0, False, str(e)))

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
                            self._mcp_connect(srv_name, cfg, verbose=True)
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

    def _mcp_connect(self, name: str, cfg: dict, verbose: bool = False):
        """Connect to an MCP server and register its tools."""
        command = cfg.get("command", "")
        if not command:
            return
        status_cb = (lambda msg: print(f"  MCP {name}: {msg}", flush=True)) if verbose else None
        client = MCPClient(
            name=name, command=command,
            args=cfg.get("args", []),
            env=cfg.get("env"),
            work_dir=str(self.config.work_dir),
        )
        client.start(timeout=30, on_status=status_cb)
        self._mcp_clients[name] = client

        # Cache tools
        cache_dir = self.config.work_dir / ".emelya" / "mcp_cache"
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

    def _confirm(self, description: str, detail: str = "") -> bool:
        """Ask user for confirmation. Respects confirm_mode.
        Note: tool call details are already printed by _print_tool_call,
        so this only shows the prompt."""
        if self.config.confirm_mode == "no_confirm":
            return True
        try:
            answer = input(f"  {C.warning}{t('allow')}:{C.reset} ").strip().lower()
            return answer in ("y", "yes", "д", "да")
        except (EOFError, KeyboardInterrupt):
            return False

    def _print_tool_call(self, tc: dict):
        """Display tool call to user."""
        name = tc["name"]
        args = tc["args"]
        if name == "bash":
            desc = args.get("description", "")
            cmd = args.get("command", "")
            print(f"\n  {C.tool_call}bash:{C.reset} {desc}")
            print(f"  {C.muted}$ {cmd}{C.reset}")
        elif name == "read":
            print(f"\n  {C.tool_call}read:{C.reset} {args.get('path', '')}")
        elif name == "write":
            print(f"\n  {C.tool_call}write:{C.reset} {args.get('path', '')}")
        elif name == "edit":
            print(f"\n  {C.tool_call}edit:{C.reset} {args.get('path', '')}")
        elif name == "glob":
            print(f"\n  {C.tool_call}glob:{C.reset} {args.get('pattern', '')}")
        elif name == "grep":
            pattern = args.get("pattern", "")
            include = args.get("include", "")
            extra = f" ({include})" if include else ""
            print(f"\n  {C.tool_call}grep:{C.reset} {pattern}{extra}")
        elif name == "skill":
            print(f"\n  {C.tool_call}skill:{C.reset} {args.get('name', '')}")
        elif name == "agent":
            print(f"\n  {C.tool_call}agent:{C.reset} {args.get('name', '')}")
        else:
            print(f"\n  {C.tool_call}{name}:{C.reset} {json.dumps(args, ensure_ascii=False)}")

    def _print_tool_result(self, name: str, result: str):
        """Display tool result (abbreviated). Limits both lines and chars."""
        is_error = result.startswith("Error:")
        color = C.tool_error if is_error else C.tool_result
        max_chars = 500
        max_lines = 10
        lines = result.split("\n")
        truncated = False

        # Check total length — for long single-line results (JSON from MCP)
        if len(result) > max_chars and len(lines) <= 2:
            preview = result[:max_chars]
            print(f"  {color}{preview}{C.reset}")
            print(f"  {C.muted}... ({len(result)} chars total){C.reset}")
            return

        if len(lines) > max_lines:
            show = lines[:max_lines]
            truncated = True
        else:
            show = lines
        for line in show:
            print(f"  {color}{line}{C.reset}")
        if truncated:
            print(f"  {C.muted}... ({len(lines)} lines total){C.reset}")

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
            # Strip .gguf/.bin for display
            display = mid
            for suffix in (".gguf", ".bin"):
                if display.endswith(suffix):
                    display = display[:-len(suffix)]
            self._model_display = display

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

    def _agent_loop(self, user_input: str):
        """Run the agent loop with work context isolation.

        Tool calls and results live in a temporary work context.
        Only user input and final text response go into the main context.
        """
        self.context.add_user(user_input)

        # No auto-trim — user controls context size via /compact and /clear

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
            self.context.add_assistant_text(response.get("content") or "")
            return

        # Has tool calls — switch to work context
        work_messages = list(self.context.get_messages())
        work_messages.append(response["raw"])
        final_text = response.get("content") or ""
        consecutive_errors = 0
        _seen_calls = {}  # (name, args_key) -> count — duplicate detection

        for iteration in range(self.config.max_iterations):
            # Execute tool calls
            for tc in tool_calls:
                self._print_tool_call(tc)

                # Duplicate tool call detection
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
                    consecutive_errors += 1
                    continue

                import time as _time
                t0 = _time.monotonic()
                try:
                    result = self.registry.execute(tc["name"], tc["args"], self.tool_ctx)
                except KeyboardInterrupt:
                    result = "Cancelled by user"
                elapsed = int((_time.monotonic() - t0) * 1000)

                self.log.tool_exec(tc["name"], tc["args"], result, elapsed)

                # Intercept plan_mode exit — ask user approval
                if tc["name"] == "plan_mode" and tc["args"].get("action") == "exit":
                    try:
                        answer = input(f"\n  {C.warning}{t('allow')}{C.reset} ").strip().lower()
                        if answer not in ("y", "yes", "д", "да"):
                            from tools.plan_mode import reset as plan_reset
                            plan_reset()
                            result = "Plan rejected by user. Ask what to change."
                    except (EOFError, KeyboardInterrupt):
                        from tools.plan_mode import reset as plan_reset
                        plan_reset()
                        result = "Plan rejected."

                # Intercept planner result — execute plan
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
                work_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

                if result.startswith("Error:"):
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

    # Known input context limits for auto-detection
    _KNOWN_LIMITS = {
        "api.deepseek.com": 128000,  # V3: 64K input, V4: 128K+ (conservative)
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
            print(f"  {t('unknown_model')}: {name}")
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
            from tools.skill import scan_skills
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
            from tools.agent import scan_agents
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
            print(f"  {t('unknown_subcmd')}: /mcp {subcmd}")
            print("  Usage: /mcp, /mcp add, /mcp remove <name>, /mcp refresh [name]")

    def _mcp_status(self):
        config_path = self.config.work_dir / ".emelya" / "mcp.json"
        mcp_config = getattr(self, "_mcp_config", {})

        if not mcp_config and not self._mcp_clients:
            print(f"  {t('no_mcp')}")
            print(f"  {t('config')}: {config_path}")
            print(f"  {t('add_hint')}: /mcp add <name> <command> [args...]")
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

        print(f"  {t('config')}: {config_path}")

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
        app_data_dir = self.config.work_dir / ".emelya"
        config_path = app_data_dir / "mcp.json"
        config = load_mcp_config(config_path)
        entry = {"command": command, "args": args}
        if env:
            entry["env"] = env
        config[name] = entry
        app_data_dir.mkdir(parents=True, exist_ok=True)
        import json as _json
        config_path.write_text(
            _json.dumps({"servers": config}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Connect with progress
        cache_dir = app_data_dir / "mcp_cache"
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
        config_path = self.config.work_dir / ".emelya" / "mcp.json"
        config = load_mcp_config(config_path)
        if name in config:
            del config[name]
            import json as _json
            config_path.write_text(
                _json.dumps({"servers": config}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        # Remove cache
        cache_file = self.config.work_dir / ".emelya" / "mcp_cache" / f"{name}.json"
        if cache_file.exists():
            cache_file.unlink()

        print(f"  Removed: {name}")

    def _mcp_restart(self, name: str = ""):
        """Refresh MCP server(s): reconnect and update cache."""
        config = load_mcp_config(self.config.work_dir / ".emelya" / "mcp.json")
        names = [name] if name else list(config.keys())
        if not names:
            print(f"  {t('no_mcp')}")
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
                self._mcp_connect(n, cfg, verbose=True)
                self._update_dynamic_descriptions()
                n_tools = len(self._mcp_clients[n].tools)
                print(f"  {n}: refreshed ({n_tools} tools, cache updated)")
            except MCPError as e:
                print(f"  {n}: failed — {e}")

        self._update_dynamic_descriptions()

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

    def run(self):
        """Main REPL loop."""
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
                    print("  Available: /help /clear /compact /undo /drop /save /load /new /model /skill /agent /tools /mcp /stats /context /history /exit")
                continue

            # Agent loop
            try:
                self._agent_loop(user_input)
            except KeyboardInterrupt:
                print(f"\n  {t('interrupted')}")

        self._shutdown_mcp()
        self.log.session_end()
        self.log.close()
        self.usage_tracker.close()
