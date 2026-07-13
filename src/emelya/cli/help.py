"""emelya help — show usage."""

from emelya import __version__


def run():
    print(f"Emelya — CLI AI Agent for local LLMs (v{__version__})")
    print()
    print("Usage: emelya [options] [subcommand]")
    print()
    print("Options:")
    print("  --host HOST                  Server host (default: localhost)")
    print("  --port PORT                  Server port (default: 8080)")
    print("  --model MODEL                Model name on server (default: default)")
    print("  --use NAME                   Use a named model from .emelya/models.json")
    print("  --temperature FLOAT          Sampling temperature (default: 0.7)")
    print("  --max-tokens INT             Max tokens per response (default: 8192)")
    print("  --context-limit INT          Context window limit (default: 8000)")
    print("  --work-dir DIR               Working directory (default: .)")
    print("  --max-iterations INT         Max tool call iterations (default: 10)")
    print("  --resume SESSION             Resume a previous session by name")
    print("  --no-stream                  Disable streaming output")
    print("  --confirm {all,write,none}   Bash confirmation mode (default: all)")
    print()
    print("Subcommands:")
    print("  help                         Show this help")
    print("  mcp                          Manage MCP servers (use 'emelya mcp help')")
    print()
    print("Without a subcommand, starts the interactive REPL.")
    print("Inside the REPL, type /help for interactive command reference.")
