"""MCP management mixin — server lifecycle, lazy loading, commands."""

import json
from pathlib import Path

from emelya.i18n import t
from emelya.theme import C
from emelya.mcp import MCPClient, HTTPMCPClient, MCPError, compact_tool_description, load_mcp_config, load_cache, save_cache


def _parse_mcp_add_args(parts: list) -> tuple:
    """Parse 'mcp add' arguments. Returns (name, config_entry). Raises ValueError."""
    if not parts:
        raise ValueError("need at least a name")

    name = parts[0]
    rest = parts[1:]

    if not rest:
        raise ValueError("need a command (use -- before it) or an HTTP/HTTPS URL")

    if rest[0].startswith(("http://", "https://")):
        # HTTP transport: NAME URL [KEY=VALUE ...]
        url = rest[0]
        headers = {}
        for kv in rest[1:]:
            if "=" not in kv:
                raise ValueError(f"expected KEY=VALUE for header, got: {kv!r}")
            k, v = kv.split("=", 1)
            headers[k] = v
        entry = {"url": url}
        if headers:
            entry["headers"] = headers
    elif "--" in rest:
        # stdio transport: NAME -- CMD [ARGS]
        sep = rest.index("--")
        cmd_parts = rest[sep + 1:]
        if not cmd_parts:
            raise ValueError("no command after --")
        command = cmd_parts[0]
        args = cmd_parts[1:]
        entry = {"command": command}
        if args:
            entry["args"] = args
    else:
        raise ValueError(
            "use -- before stdio command (e.g. 'name -- npx server') "
            "or provide an HTTP/HTTPS URL"
        )

    return name, entry


def _make_mcp_client(name: str, entry: dict, work_dir: str = None):
    """Create MCPClient or HTTPMCPClient based on entry config."""
    if entry.get("url"):
        return HTTPMCPClient(
            name=name,
            url=entry["url"],
            headers=entry.get("headers", {}),
            timeout=entry.get("timeout", 30),
        )
    elif entry.get("command"):
        return MCPClient(
            name=name,
            command=entry["command"],
            args=entry.get("args", []),
            env=entry.get("env") or None,
            work_dir=work_dir,
        )
    else:
        raise MCPError(f"Server '{name}' has neither 'url' nor 'command'")


def format_mcp_result(result: str) -> str:
    """Format MCP result for display. Pretty-print JSON if compact enough."""
    try:
        data = json.loads(result)
    except (json.JSONDecodeError, ValueError):
        return result
    pretty = json.dumps(data, indent=2, ensure_ascii=False)
    if pretty.count("\n") <= 100:
        return pretty
    return json.dumps(data, ensure_ascii=False)


def parse_mcp_call_args(args_str: str, tool_def: dict) -> dict:
    """Parse call args string using tool schema.

    - JSON object string → parsed as-is
    - Plain text → assigned to first required (or first) parameter
    - Empty → {}
    Returns (call_kwargs, hint) where hint describes auto-mapping (may be empty).
    """
    if not args_str:
        return {}, ""

    if args_str.startswith("{"):
        try:
            return json.loads(args_str), ""
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid JSON: {e}") from e

    # Plain text → first param from schema
    schema = tool_def.get("inputSchema", {})
    props = schema.get("properties", {})
    required = schema.get("required", [])
    first_param = required[0] if required else (list(props.keys())[0] if props else None)
    if not first_param:
        raise ValueError(f"tool has no parameters, but args provided: {args_str!r}")

    param_type = props.get(first_param, {}).get("type", "string")
    if param_type == "integer" and args_str.isdigit():
        value = int(args_str)
    elif param_type == "number":
        try:
            value = float(args_str)
        except ValueError:
            value = args_str
    else:
        value = args_str

    return {first_param: value}, f"→ {first_param}={args_str!r}"


class MCPMixin:
    """MCP server management methods for REPL.

    Expects self to have: config, registry, _mcp_clients, _mcp_config,
    _mcp_startup_info, _update_dynamic_descriptions.
    """

    def _init_mcp(self):
        """Register MCP servers from cache. No connections at startup.

        Loads global ~/.emelya/mcp.json first, then local .emelya/mcp.json.
        Local servers override global servers with the same name.
        Each server's cache lives next to its config file.
        Servers connect lazily when load_tools("mcp:name") is called.
        """
        global_data_dir = Path.home() / ".emelya"
        local_data_dir = self.config.work_dir / ".emelya"

        # Merge: global first, local overrides
        merged_config = {}
        cache_dirs: dict[str, Path] = {}
        for data_dir in (global_data_dir, local_data_dir):
            cfg = load_mcp_config(data_dir / "mcp.json")
            for name, server_cfg in cfg.items():
                merged_config[name] = server_cfg
                cache_dirs[name] = data_dir / "mcp_cache"

        self._mcp_config = merged_config
        self._mcp_cache_dirs = cache_dirs

        if not self._mcp_config:
            return

        for name, cfg in self._mcp_config.items():
            cache_dir = cache_dirs[name]
            cached = load_cache(cache_dir, name)
            if cached:
                # Register from cache with a lazy executor
                self._register_mcp_lazy(name, cached)
                self._mcp_startup_info.append((name, len(cached), True))
            else:
                try:
                    self._mcp_connect(name, cfg, cache_dir=cache_dir)
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
                        cache_dir = self._mcp_cache_dirs.get(srv_name)
                        print(f"\n  Connecting to MCP {srv_name}...", flush=True)
                        try:
                            self._mcp_connect(srv_name, cfg, verbose=True, cache_dir=cache_dir)
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

    def _mcp_connect(self, name: str, cfg: dict, verbose: bool = False, cache_dir: Path = None):
        """Connect to an MCP server and register its tools.

        Chooses transport automatically:
          - cfg has 'url'     → HTTPMCPClient (MCP Streamable HTTP)
          - cfg has 'command' → MCPClient (stdio)
        """
        if cache_dir is None:
            cache_dir = self._mcp_cache_dirs.get(name,
                self.config.work_dir / ".emelya" / "mcp_cache")
        status_cb = (lambda msg: print(f"  MCP {name}: {msg}", flush=True)) if verbose else None

        if cfg.get("url"):
            # HTTP transport — no subprocess
            client = HTTPMCPClient(
                name=name,
                url=cfg["url"],
                headers=cfg.get("headers", {}),
                timeout=cfg.get("timeout", 30),
            )
        elif cfg.get("command"):
            client = MCPClient(
                name=name,
                command=cfg["command"],
                args=cfg.get("args", []),
                env=cfg.get("env"),
                work_dir=str(self.config.work_dir),
            )
        else:
            raise MCPError(f"Server '{name}' has neither 'url' nor 'command'")

        client.start(timeout=cfg.get("timeout", 30), on_status=status_cb)
        self._mcp_clients[name] = client

        # Cache tools next to the server's config file
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
        elif subcmd == "tools":
            self._mcp_tools(subarg)
        elif subcmd == "call":
            self._mcp_call(subarg)
        elif subcmd == "":
            self._mcp_status()
        else:
            print(f"  {t('unknown_subcmd')}: /mcp {subcmd}")
            print("  Usage: /mcp, /mcp add, /mcp remove <name>, /mcp refresh [name], /mcp tools <name>, /mcp call <name> <tool> [json]")

    def _mcp_status(self):
        config_path = self.config.work_dir / ".emelya" / "mcp.json"
        mcp_config = getattr(self, "_mcp_config", {})

        if not mcp_config and not self._mcp_clients:
            print(f"  {t('no_mcp')}")
            print(f"  {t('config')}: {config_path}")
            print(f"  {t('add_hint')}: /mcp add <name> -- <command>")
            return

        # Show all configured servers (connected or cached)
        for name in sorted(set(list(mcp_config.keys()) + list(self._mcp_clients.keys()))):
            category = f"mcp:{name}"
            n_ext = len(self.registry._extensions.get(category, {}))

            if name in self._mcp_clients:
                client = self._mcp_clients[name]
                status = "connected" if client.is_alive else "stopped"
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
            print("  Usage:")
            print("    /mcp add <name> -- <command> [args...]   (stdio)")
            print("    /mcp add <name> <url> [KEY=VALUE ...]    (HTTP)")
            print("  Examples:")
            print("    /mcp add playwright -- npx -y @playwright/mcp@latest")
            print("    /mcp add example https://example.com/mcp X-Auth-Token=secret")
            return

        import shlex
        try:
            parts = shlex.split(arg)
        except ValueError:
            parts = arg.split()

        try:
            name, entry = _parse_mcp_add_args(parts)
        except ValueError as e:
            print(f"  Error: {e}")
            return

        # Save to config
        app_data_dir = self.config.work_dir / ".emelya"
        config_path = app_data_dir / "mcp.json"
        existing = load_mcp_config(config_path)
        existing[name] = entry
        app_data_dir.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps({"servers": existing}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Connect with progress
        cache_dir = app_data_dir / "mcp_cache"
        status_cb = lambda msg: print(f"  [{name}] {msg}", flush=True)
        try:
            client = _make_mcp_client(name, entry, work_dir=str(self.config.work_dir))
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
            print("  Saved to config. Fix the issue and try /mcp refresh")

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
        config = self._mcp_config  # already merged global + local
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

    def _mcp_tools(self, name: str = ""):
        """List tools for an MCP server with descriptions and parameters."""
        if not name:
            print("  Usage: /mcp tools <server_name>")
            # List available servers
            servers = list(getattr(self, "_mcp_config", {}).keys())
            if servers:
                print(f"  Servers: {', '.join(servers)}")
            return

        # Try cached tools first, then live
        category = f"mcp:{name}"
        ext_tools = self.registry._extensions.get(category, {})
        if not ext_tools:
            print(f"  No tools found for '{name}'. Is the server configured?")
            return

        print(f"\n  === MCP {name}: {len(ext_tools)} tools ===\n")
        for tool_name, tool_info in sorted(ext_tools.items()):
            defn = tool_info.get("definition", {}).get("function", {})
            desc = defn.get("description", "")
            params = defn.get("parameters", {}).get("properties", {})
            required = set(defn.get("parameters", {}).get("required", []))

            # Tool name and description
            print(f"  {C.tool_call}{tool_name}{C.reset}")
            if desc:
                if len(desc) > 120:
                    desc = desc[:120] + "…"
                print(f"    {C.muted}{desc}{C.reset}")

            # Parameters
            if params:
                for pname, pinfo in params.items():
                    ptype = pinfo.get("type", "")
                    pdesc = pinfo.get("description", "")
                    req = " *" if pname in required else ""
                    if pdesc and len(pdesc) > 80:
                        pdesc = pdesc[:80] + "…"
                    print(f"    {pname}{req} ({ptype}){f' — {pdesc}' if pdesc else ''}")
            print()

    def _mcp_call(self, arg: str = ""):
        """Call an MCP tool directly without the model.

        Args can be:
          - JSON object: /mcp call srv tool {"key": "value"}
          - Plain text:  /mcp call srv tool some text here
            → uses the text as value of the first parameter from the tool schema
          - Nothing:     /mcp call srv tool
            → calls with empty args
        """
        if not arg:
            print("  Usage: /mcp call <server_name> <tool_name> [args]")
            print("  Examples:")
            print("    /mcp call example list_orders")
            print('    /mcp call example get_order {"id": 123}')
            print("    /mcp call example search some query text")
            return

        parts = arg.split(None, 2)
        if len(parts) < 2:
            print("  Usage: /mcp call <server_name> <tool_name> [args]")
            return

        srv_name = parts[0]
        tool_name = parts[1]
        args_str = parts[2].strip() if len(parts) > 2 else ""

        # Find the tool (needed for schema lookup)
        category = f"mcp:{srv_name}"
        ext_tools = self.registry._extensions.get(category, {})
        if not ext_tools:
            print(f"  Error: server '{srv_name}' not found or has no tools")
            return
        if tool_name not in ext_tools:
            available = ", ".join(sorted(ext_tools.keys()))
            print(f"  Error: tool '{tool_name}' not found in {srv_name}")
            print(f"  Available: {available}")
            return

        # Parse args using schema from registry definition
        defn = ext_tools[tool_name].get("definition", {}).get("function", {})
        tool_def = {"inputSchema": defn.get("parameters", {})}
        try:
            call_args, hint = parse_mcp_call_args(args_str, tool_def)
        except ValueError as e:
            print(f"  Error: {e}")
            return
        if hint:
            print(f"  {C.muted}{hint}{C.reset}")

        print(f"  Calling {srv_name}/{tool_name}...")
        try:
            result = ext_tools[tool_name]["execute"](call_args, self.tool_ctx)
            print()
            print(format_mcp_result(result))
        except Exception as e:
            print(f"  Error: {e}")

    @staticmethod
    def _format_mcp_result(result: str) -> str:
        return format_mcp_result(result)
