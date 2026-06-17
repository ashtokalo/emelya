"""MCP management mixin — server lifecycle, lazy loading, commands."""

import json

from i18n import t
from theme import C
from mcp import MCPClient, MCPError, compact_tool_description, load_mcp_config, load_cache, save_cache


class MCPMixin:
    """MCP server management methods for REPL.

    Expects self to have: config, registry, _mcp_clients, _mcp_config,
    _mcp_startup_info, _update_dynamic_descriptions.
    """

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

    # --- /mcp commands ---

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
        config_path.write_text(
            json.dumps({"servers": config}, indent=2, ensure_ascii=False),
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
            config_path.write_text(
                json.dumps({"servers": config}, indent=2, ensure_ascii=False),
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
