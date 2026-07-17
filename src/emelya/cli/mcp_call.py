"""emelya mcp call — call an MCP tool directly."""

import sys
from pathlib import Path


def run(work_dir: Path, call_args: list):
    from emelya.mcp import load_mcp_config, MCPError
    from emelya.mcp_manager import _make_mcp_client, parse_mcp_call_args, format_mcp_result

    if len(call_args) < 2:
        print("Usage: emelya mcp call <server> <tool> [args]")
        print("Examples:")
        print("  emelya mcp call example list_orders")
        print('  emelya mcp call example get_order \'{"id": 123}\'')
        print("  emelya mcp call example sql_query select version()")
        sys.exit(1)

    srv_name = call_args[0]
    tool_name = call_args[1]
    args_str = " ".join(call_args[2:])

    global_dir = Path.home() / ".emelya"
    local_dir = work_dir / ".emelya"
    merged_config = {}
    for data_dir in (global_dir, local_dir):
        for name, cfg in load_mcp_config(data_dir / "mcp.json").items():
            merged_config[name] = cfg

    if srv_name not in merged_config:
        print(f"Error: server '{srv_name}' not found in config", file=sys.stderr)
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
