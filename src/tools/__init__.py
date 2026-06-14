"""Tool registry — loads, manages, and executes tools."""

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from sandbox import Sandbox


@dataclass
class ToolContext:
    """Shared context passed to every tool execution."""
    work_dir: Path
    sandbox: Sandbox
    files_read: set = field(default_factory=set)
    confirm: Callable = None  # confirm(description, detail) -> bool
    loaded_skills: set = field(default_factory=set)
    undo_stack: list = field(default_factory=list)  # [(path, old_content | None)]
    agent_depth: int = 0       # current nesting level (0 = main agent)
    max_agent_depth: int = 3   # max nesting allowed
    # These are set by the REPL for skill/agent tools
    skills_dir: Path = None
    agents_dir: Path = None
    llm_client: object = None
    tool_registry: object = None
    config: object = None

    def backup_file(self, path: Path):
        """Save file content to undo stack before modification."""
        if path.exists():
            try:
                content = path.read_bytes()
                self.undo_stack.append((str(path), content))
            except OSError:
                pass
        else:
            # File doesn't exist yet — undo means delete
            self.undo_stack.append((str(path), None))

    def undo(self) -> str:
        """Undo last file modification. Returns status message."""
        if not self.undo_stack:
            return "Nothing to undo"
        path_str, old_content = self.undo_stack.pop()
        path = Path(path_str)
        if old_content is None:
            if path.exists():
                path.unlink()
                return f"Undo: deleted {path.name} (was newly created)"
            return "Undo: file already gone"
        else:
            path.write_bytes(old_content)
            return f"Undo: restored {path.name}"


class ToolRegistry:
    """Registry of available tools with core/extension separation."""

    def __init__(self):
        self._core = {}        # name -> {definition, execute}
        self._extensions = {}  # category -> {name -> {definition, execute, description}}
        self._loaded = set()   # loaded extension categories

    def register(self, name: str, definition: dict, execute: Callable,
                 category: str = None):
        entry = {"definition": definition, "execute": execute}
        if category is None:
            self._core[name] = entry
        else:
            if category not in self._extensions:
                self._extensions[category] = {}
            desc = definition["function"].get("description", "")
            entry["description"] = desc
            self._extensions[category][name] = entry

    def load_builtin(self, src_dir: Path):
        """Load all built-in tool modules from src/tools/."""
        import sys
        tools_dir = src_dir / "tools"
        src_str = str(src_dir)
        if src_str not in sys.path:
            sys.path.insert(0, src_str)

        for py_file in sorted(tools_dir.glob("*.py")):
            if py_file.name == "__init__.py":
                continue
            module_name = f"tools.{py_file.stem}"
            try:
                module = importlib.import_module(module_name)
                if hasattr(module, "DEFINITION") and hasattr(module, "execute"):
                    name = module.DEFINITION["function"]["name"]
                    category = getattr(module, "CATEGORY", None)
                    self.register(name, module.DEFINITION, module.execute, category)
            except Exception as e:
                print(f"Warning: failed to load tool {py_file.name}: {e}")

    def get_tools(self, names: list = None) -> list:
        """Return tool definitions for LLM API.
        names=None → core + loaded extensions.
        names=list → only those specific tools (for sub-agents)."""
        if names is not None:
            all_tools = self._all_tools()
            return [all_tools[n]["definition"] for n in names if n in all_tools]

        result = [t["definition"] for t in self._core.values()]
        for cat in self._loaded:
            if cat in self._extensions:
                for t in self._extensions[cat].values():
                    result.append(t["definition"])
        return result

    def _all_tools(self) -> dict:
        """All tools (core + all extensions) by name."""
        merged = dict(self._core)
        for cat_tools in self._extensions.values():
            merged.update(cat_tools)
        return merged

    def execute(self, name: str, args: dict, ctx: ToolContext) -> str:
        """Execute a tool call, return result string."""
        all_tools = self._all_tools()
        if name not in all_tools:
            # Check if it's in an unloaded extension
            for cat, tools in self._extensions.items():
                if name in tools and cat not in self._loaded:
                    return (
                        f"Error: tool '{name}' is available but not loaded. "
                        f"Use load_tools(category=\"{cat}\") first."
                    )
            return f"Error: unknown tool '{name}'"

        if "_raw_error" in args:
            return f"Error: invalid JSON in arguments: {args['_raw_error']}"

        try:
            return all_tools[name]["execute"](args, ctx)
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"

    def load_category(self, category: str) -> str:
        """Load an extension category. Returns status message."""
        if category in self._loaded:
            tools = list(self._extensions.get(category, {}).keys())
            return f"Category '{category}' already loaded: {', '.join(tools)}"
        if category not in self._extensions:
            available = ", ".join(self._extensions.keys())
            return f"Unknown category '{category}'. Available: {available}"
        self._loaded.add(category)
        tools = list(self._extensions[category].keys())
        return f"Loaded: {', '.join(tools)}"

    # --- MCP integration ---

    def register_mcp_server(self, server_name: str, tools: list, client):
        """Register MCP server tools as an extension category.

        tools: list of compact tool definitions from MCP server
        client: MCPClient instance for executing calls
        """
        category = f"mcp:{server_name}"
        if category not in self._extensions:
            self._extensions[category] = {}

        for tool in tools:
            name = tool.get("name", "")
            if not name:
                continue
            # Convert MCP tool format to our tool format
            definition = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description", name),
                    "parameters": tool.get("inputSchema", {
                        "type": "object", "properties": {},
                    }),
                },
            }

            def _make_executor(srv_name, tool_name, mcp_client):
                def executor(args, ctx):
                    try:
                        return mcp_client.call_tool(tool_name, args)
                    except Exception as e:
                        return f"Error: MCP {srv_name}/{tool_name}: {e}"
                return executor

            self._extensions[category][name] = {
                "definition": definition,
                "execute": _make_executor(server_name, name, client),
                "description": tool.get("description", name),
            }

    def get_extension_summary(self) -> dict:
        """Return {category: {tool_name: description}} for all extensions."""
        summary = {}
        for cat, tools in self._extensions.items():
            summary[cat] = {
                name: t["description"] for name, t in tools.items()
            }
        return summary

    def update_description(self, tool_name: str, description: str):
        """Update a tool's description (used for dynamic skill/agent lists)."""
        if tool_name in self._core:
            self._core[tool_name]["definition"]["function"]["description"] = description

    @property
    def names(self) -> list:
        """Names of currently active tools (core + loaded extensions)."""
        result = list(self._core.keys())
        for cat in self._loaded:
            if cat in self._extensions:
                result.extend(self._extensions[cat].keys())
        return result
