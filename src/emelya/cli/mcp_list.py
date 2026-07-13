"""emelya mcp list — list configured MCP servers."""

from pathlib import Path


def run(work_dir: Path):
    from emelya.mcp import load_mcp_config, load_cache

    global_dir = Path.home() / ".emelya"
    local_dir = work_dir / ".emelya"

    merged = {}
    for data_dir, label in ((global_dir, "global"), (local_dir, "local")):
        for name, server_cfg in load_mcp_config(data_dir / "mcp.json").items():
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
