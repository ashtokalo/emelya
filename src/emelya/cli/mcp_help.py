"""emelya mcp help — show MCP CLI usage."""


def run():
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
