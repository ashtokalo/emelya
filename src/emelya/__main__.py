#!/usr/bin/env python3
"""Emelya — CLI AI Agent for local LLMs."""

import json
import sys
from pathlib import Path

# Ensure src/ is in Python path (for both direct run and package install)
src_dir = Path(__file__).parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from emelya.config import parse_args
from emelya.repl import REPL


def _scaffold_emelya(work_dir: Path):
    """Create .emelya directory with default resources on first run."""
    emelya_dir = work_dir / ".emelya"
    if emelya_dir.exists():
        return

    emelya_dir.mkdir(parents=True, exist_ok=True)

    # commands/ — user-defined /command shortcuts
    (emelya_dir / "commands").mkdir()

    # memory/ — project knowledge files (indexed automatically by first heading)
    (emelya_dir / "memory").mkdir()

    # mcp.json — MCP server configuration
    mcp_data = {"servers": {}}
    (emelya_dir / "mcp.json").write_text(
        json.dumps(mcp_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # models.json — named models and routing
    # 'local' is always the model from CLI args (--url / --port / --model).
    # Add named models here and adjust routing to use them per operation.
    models_data = {
        "models": {},
        "routing": {
            "default":      ["local"],
            "planner":      ["local"],
            "agent":        ["local"],
            "agent_light":  ["local"],
        },
    }
    models_path = emelya_dir / "models.json"
    models_path.write_text(
        json.dumps(models_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        models_path.chmod(0o600)
    except OSError:
        pass


def _extract_work_dir(argv: list) -> Path:
    """Extract --work-dir value from argv list, defaulting to '.'."""
    for i, a in enumerate(argv):
        if a == "--work-dir" and i + 1 < len(argv):
            return Path(argv[i + 1]).resolve()
        if a.startswith("--work-dir="):
            return Path(a.split("=", 1)[1]).resolve()
    return Path(".").resolve()


def _cli_mcp_add(work_dir: Path, add_args: list):
    """Handle 'emelya mcp add ...' subcommand."""
    from emelya.mcp_manager import _parse_mcp_add_args, _make_mcp_client
    from emelya.mcp import MCPError, compact_tool_description, load_mcp_config, save_cache

    if not add_args:
        print("Usage:")
        print("  emelya mcp add <name> -- <command> [args...]   (stdio)")
        print("  emelya mcp add <name> <url> [KEY=VALUE ...]    (HTTP)")
        print("Examples:")
        print("  emelya mcp add playwright -- npx -y @playwright/mcp@latest")
        print("  emelya mcp add shopfans https://example.com/mcp X-Auth-Token=secret")
        sys.exit(1)

    try:
        name, entry = _parse_mcp_add_args(add_args)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    app_data_dir = work_dir / ".emelya"
    config_path = app_data_dir / "mcp.json"
    existing = load_mcp_config(config_path)
    existing[name] = entry
    app_data_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"servers": existing}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    cache_dir = app_data_dir / "mcp_cache"
    status_cb = lambda msg: print(f"  [{name}] {msg}", flush=True)
    try:
        client = _make_mcp_client(name, entry, work_dir=str(work_dir))
        client.start(timeout=60, on_status=status_cb)
        tool_names = [t["name"] for t in client.tools]
        cached = [compact_tool_description(t) for t in client.tools]
        save_cache(cache_dir, name, cached)
        client.stop()
        print(f"Added: {name} ({len(cached)} tools: {', '.join(tool_names)})")
    except MCPError as e:
        print(f"Error connecting: {e}")
        print("Saved to config. Fix the issue and try /mcp refresh inside emelya.")
        sys.exit(1)


def _cli_mcp_list(work_dir: Path):
    """Handle 'emelya mcp list' subcommand."""
    from emelya.mcp import load_mcp_config, load_cache

    global_dir = Path.home() / ".emelya"
    local_dir = work_dir / ".emelya"

    merged: dict[str, tuple[dict, str]] = {}  # name → (cfg, source)
    for data_dir, label in ((global_dir, "global"), (local_dir, "local")):
        cfg = load_mcp_config(data_dir / "mcp.json")
        for name, server_cfg in cfg.items():
            merged[name] = (server_cfg, label)

    if not merged:
        print("No MCP servers configured.")
        print(f"  global: {global_dir / 'mcp.json'}")
        print(f"  local:  {local_dir / 'mcp.json'}")
        return

    for name, (cfg, source) in sorted(merged.items()):
        transport = "http" if cfg.get("url") else "stdio"
        endpoint = cfg.get("url") or cfg.get("command", "?")

        cache_dir = (global_dir if source == "global" else local_dir) / "mcp_cache"
        cached = load_cache(cache_dir, name)
        n_tools = len(cached) if cached is not None else None
        tools_str = f"{n_tools} tools" if n_tools is not None else "not cached"

        print(f"  {name}  [{transport}, {source}]  {tools_str}")
        print(f"    {endpoint}")


def _cli_mcp_remove(work_dir: Path, name: str):
    """Handle 'emelya mcp remove NAME' subcommand."""
    from emelya.mcp import load_mcp_config

    if not name:
        print("Usage: emelya mcp remove <name>")
        sys.exit(1)

    local_dir = work_dir / ".emelya"
    config_path = local_dir / "mcp.json"
    config = load_mcp_config(config_path)

    if name not in config:
        # Check if it's in global config
        global_config = load_mcp_config(Path.home() / ".emelya" / "mcp.json")
        if name in global_config:
            print(f"Error: '{name}' is in the global config (~/.emelya/mcp.json).")
            print("Edit it manually to remove.")
        else:
            print(f"Error: '{name}' not found in local config.")
        sys.exit(1)

    del config[name]
    config_path.write_text(
        json.dumps({"servers": config}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    cache_file = local_dir / "mcp_cache" / f"{name}.json"
    if cache_file.exists():
        cache_file.unlink()

    print(f"Removed: {name}")


def _cli_mcp_call(work_dir: Path, call_args: list):
    """Handle 'emelya mcp call SERVER TOOL [ARGS...]' subcommand."""
    from emelya.mcp import load_mcp_config, MCPError
    from emelya.mcp_manager import _make_mcp_client, parse_mcp_call_args, format_mcp_result

    if len(call_args) < 2:
        print("Usage: emelya mcp call <server> <tool> [args]")
        print("Examples:")
        print("  emelya mcp call shopfans list_orders")
        print('  emelya mcp call shopfans get_order \'{"id": 123}\'')
        print("  emelya mcp call shopfans sql_query select version()")
        sys.exit(1)

    srv_name = call_args[0]
    tool_name = call_args[1]
    args_str = " ".join(call_args[2:])

    # Load merged config (global then local)
    global_dir = Path.home() / ".emelya"
    local_dir = work_dir / ".emelya"
    merged_config = {}
    for data_dir in (global_dir, local_dir):
        for name, cfg in load_mcp_config(data_dir / "mcp.json").items():
            merged_config[name] = cfg

    if srv_name not in merged_config:
        print(f"Error: server '{srv_name}' not found in config")
        sys.exit(1)

    server_cfg = merged_config[srv_name]
    def err(msg):
        print(msg, file=sys.stderr, flush=True)

    status_cb = lambda msg: err(f"  [{srv_name}] {msg}")
    try:
        client = _make_mcp_client(srv_name, server_cfg, work_dir=str(work_dir))
        client.start(timeout=60, on_status=status_cb)
    except MCPError as e:
        err(f"Error: {e}")
        sys.exit(1)

    tool_def = next((t for t in client.tools if t.get("name") == tool_name), None)
    if tool_def is None:
        available = ", ".join(t["name"] for t in client.tools)
        err(f"Error: tool '{tool_name}' not found in '{srv_name}'")
        err(f"Available: {available}")
        client.stop()
        sys.exit(1)

    try:
        call_kwargs, hint = parse_mcp_call_args(args_str, tool_def)
    except ValueError as e:
        err(f"Error: {e}")
        client.stop()
        sys.exit(1)

    if hint:
        err(f"  {hint}")

    try:
        result = client.call_tool(tool_name, call_kwargs)
        client.stop()
        print(format_mcp_result(result))
    except MCPError as e:
        err(f"Error: {e}")
        client.stop()
        sys.exit(1)


def main():
    argv = sys.argv[1:]

    # Handle 'mcp' subcommand: emelya [--work-dir DIR] mcp add|list ...
    non_flag_args = [a for a in argv if not a.startswith("-")]
    if "mcp" in non_flag_args:
        mcp_idx = argv.index("mcp")
        post_mcp = argv[mcp_idx + 1:]
        work_dir = _extract_work_dir(argv[:mcp_idx])
        subcmd = post_mcp[0] if post_mcp else ""
        if subcmd == "add":
            _scaffold_emelya(work_dir)
            _cli_mcp_add(work_dir, post_mcp[1:])
            return
        if subcmd == "list":
            _cli_mcp_list(work_dir)
            return
        if subcmd == "remove":
            _cli_mcp_remove(work_dir, post_mcp[1] if len(post_mcp) > 1 else "")
            return
        if subcmd == "call":
            _cli_mcp_call(work_dir, post_mcp[1:])
            return
        if subcmd in ("help", "--help", ""):
            print("Usage: emelya mcp <command> [args]")
            print()
            print("Commands:")
            print("  list                              List configured MCP servers")
            print("  add <name> -- <cmd> [args]        Add a stdio server")
            print("  add <name> <url> [KEY=VALUE ...]  Add an HTTP server")
            print("  remove <name>                     Remove a server")
            print("  call <name> <tool> [args]         Call a tool directly")
            print()
            print("Examples:")
            print("  emelya mcp list")
            print("  emelya mcp add playwright -- npx -y @playwright/mcp@latest")
            print("  emelya mcp add shopfans https://example.com/mcp X-Auth-Token=secret")
            print("  emelya mcp remove playwright")
            print("  emelya mcp call shopfans sql_query select version()")
            print('  emelya mcp call shopfans get_order \'{"id": 123}\'')
            print()
            print("Options:")
            print("  --work-dir DIR   Project directory (default: current dir)")
            return

    config = parse_args()
    config.work_dir.mkdir(parents=True, exist_ok=True)
    _scaffold_emelya(config.work_dir)

    repl = REPL(config)
    repl.run()


if __name__ == "__main__":
    main()
