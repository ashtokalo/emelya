"""emelya mcp add — add an MCP server."""

import json
import sys
from pathlib import Path


def run(work_dir: Path, add_args: list):
    from emelya.mcp_manager import _parse_mcp_add_args, _make_mcp_client
    from emelya.mcp import MCPError, compact_tool_description, load_mcp_config, save_cache

    if not add_args:
        print("Usage:")
        print("  emelya mcp add <name> -- <command> [args...]   (stdio)")
        print("  emelya mcp add <name> <url> [KEY=VALUE ...]    (HTTP)")
        print("Examples:")
        print("  emelya mcp add playwright -- npx -y @playwright/mcp@latest")
        print("  emelya mcp add example https://example.com/mcp X-Auth-Token=secret")
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
