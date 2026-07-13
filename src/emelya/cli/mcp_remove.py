"""emelya mcp remove — remove an MCP server."""

import json
import sys
from pathlib import Path


def run(work_dir: Path, name: str):
    from emelya.mcp import load_mcp_config

    if not name:
        print("Usage: emelya mcp remove <name>")
        sys.exit(1)

    local_dir = work_dir / ".emelya"
    config_path = local_dir / "mcp.json"
    config = load_mcp_config(config_path)

    if name not in config:
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
