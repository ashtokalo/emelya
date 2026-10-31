"""Tool registry — loads, manages, and executes tools."""

import importlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from emelya.sandbox import Sandbox


def is_binary(path, check_bytes=8192):
    """Check if file is binary by looking for null bytes."""
    try:
        with open(path, "rb") as f:
            return b"\x00" in f.read(check_bytes)
    except OSError:
        return False


@dataclass
class FileBackup:
    """Snapshot of one path before a file-changing operation."""
    path: str
    content: Optional[bytes]
    mode: Optional[int] = None


@dataclass
class ChangeSet:
    """One user-visible operation, possibly spanning several files."""
    description: str
    files: List[FileBackup]


def _restore_backup(backup: FileBackup):
    """Restore one snapshot using an adjacent temporary file."""
    path = Path(backup.path)
    if backup.content is None:
        if path.exists() or path.is_symlink():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".emelya-undo-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(backup.content)
            stream.flush()
            os.fsync(stream.fileno())
        if backup.mode is not None:
            os.chmod(temp_name, backup.mode)
        os.replace(temp_name, str(path))
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


@dataclass
class ToolContext:
    """Shared context passed to every tool execution."""
    work_dir: Path
    sandbox: Sandbox
    files_read: set = field(default_factory=set)
    confirm: Callable = None  # confirm(description, detail) -> bool
    get_cancellation_result: Callable = None  # () -> model-facing error text
    loaded_skills: set = field(default_factory=set)
    undo_stack: list = field(default_factory=list)  # list[ChangeSet]
    agent_depth: int = 0       # current nesting level (0 = main agent)
    max_agent_depth: int = 3   # max nesting allowed
    # These are set by the REPL for skill/agent tools
    skills_dirs: list = None   # ordered: builtin → global → local
    agents_dirs: list = None   # ordered: builtin → global → local
    llm_client: object = None
    session_log: object = None  # SessionLog for usage tracking
    usage_tracker: object = None  # UsageTracker for project-wide stats
    tool_registry: object = None
    get_routed_client: Callable = None  # (operation) -> LLMClient or None
    config: object = None
    hook_runner: object = None  # HookRunner instance, set by REPL
    memory_store: object = None  # FileMemoryStore shared by main/sub-agents
    task_store: object = None  # FileTaskStore shared by main/sub-agents
    user_language: str = ""  # inherited by sub-agents; guessed only at the root

    def cancellation_result(self) -> str:
        """Return an explicit model-facing result for a denied confirmation."""
        if self.get_cancellation_result:
            return self.get_cancellation_result()
        return (
            "Error: operation cancelled by user. "
            "The command was NOT executed and no changes were made."
        )

    def snapshot_file(self, path: Path) -> FileBackup:
        """Return a snapshot without changing undo history."""
        if path.exists():
            return FileBackup(str(path), path.read_bytes(), path.stat().st_mode)
        return FileBackup(str(path), None)

    def push_changeset(self, description: str, backups: list):
        """Add one operation to undo history."""
        self.undo_stack.append(ChangeSet(description, list(backups)))

    def backup_file(self, path: Path, description: str = None):
        """Save one file as a single-file operation."""
        self.push_changeset(description or f"Change {path.name}", [self.snapshot_file(path)])

    def undo(self) -> str:
        """Undo last file modification. Returns status message."""
        if not self.undo_stack:
            return "Nothing to undo"
        entry = self.undo_stack[-1]
        # Accept legacy tuple entries created by older callers/sessions.
        if isinstance(entry, tuple):
            entry = ChangeSet("Legacy change", [FileBackup(entry[0], entry[1])])

        current = [self.snapshot_file(Path(backup.path)) for backup in entry.files]
        try:
            for backup in reversed(entry.files):
                _restore_backup(backup)
        except Exception as exc:
            # Best effort: put the post-change state back and retain the undo
            # entry so the user can retry.
            for backup in reversed(current):
                try:
                    _restore_backup(backup)
                except OSError:
                    pass
            return f"Error: undo failed: {exc}"

        self.undo_stack.pop()
        restored = sum(1 for backup in entry.files if backup.content is not None)
        deleted = sum(1 for backup in entry.files if backup.content is None)

        total = restored + deleted
        if total == 1:
            if deleted:
                return "Undo: deleted newly created file"
            return f"Undo: restored {Path(entry.files[0].path).name}"
        return f"Undo: restored {restored}, deleted {deleted} ({total} files)"


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
        """Load all built-in tool modules from src/emelya/tools/."""
        import sys
        tools_dir = src_dir / "tools"
        # Ensure parent of emelya package is on path
        pkg_parent = str(src_dir.parent)
        if pkg_parent not in sys.path:
            sys.path.insert(0, pkg_parent)

        for py_file in sorted(tools_dir.glob("*.py")):
            if py_file.name == "__init__.py":
                continue
            module_name = f"emelya.tools.{py_file.stem}"
            try:
                module = importlib.import_module(module_name)
                if hasattr(module, "DEFINITION") and hasattr(module, "execute"):
                    name = module.DEFINITION["function"]["name"]
                    category = getattr(module, "CATEGORY", None)
                    self.register(name, module.DEFINITION, module.execute, category)
            except Exception as e:
                print(f"Warning: failed to load tool {py_file.name}: {e}")

    def get_tools(self, names: list = None, compact: bool = False) -> list:
        """Return tool definitions for LLM API.
        names=None → core + loaded extensions.
        names=list → only those specific tools (for sub-agents).
        compact=True → strip param descriptions to save tokens."""
        if names is not None:
            all_tools = self._all_tools()
            defs = [all_tools[n]["definition"] for n in names if n in all_tools]
        else:
            defs = [t["definition"] for t in self._core.values()]
            for cat in self._loaded:
                if cat in self._extensions:
                    for t in self._extensions[cat].values():
                        defs.append(t["definition"])

        if compact:
            return [self._compact_def(d) for d in defs]
        return defs

    @staticmethod
    def _compact_def(definition: dict) -> dict:
        """Strip descriptions from parameters, shorten tool description."""
        import copy
        d = copy.deepcopy(definition)
        fn = d.get("function", {})
        # Shorten description to first sentence
        desc = fn.get("description", "")
        for i, c in enumerate(desc):
            if c in ".!?" and i > 5:
                fn["description"] = desc[:i + 1]
                break
        # Remove parameter descriptions
        params = fn.get("parameters", {})
        for prop in params.get("properties", {}).values():
            prop.pop("description", None)
        return d

    def _all_tools(self) -> dict:
        """All tools (core + all extensions) by name."""
        merged = dict(self._core)
        for cat_tools in self._extensions.values():
            merged.update(cat_tools)
        return merged

    def execute(self, name: str, args: dict, ctx: ToolContext) -> str:
        """Execute a tool call, return result string."""
        # Block write tools in plan mode
        try:
            from emelya.tools.plan_mode import is_active, BLOCKED_TOOLS
            if is_active() and name in BLOCKED_TOOLS:
                return f"Error: '{name}' is blocked in plan mode. Use plan_mode(action='exit', plan='...') first."
        except ImportError:
            pass

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
            return (
                f"Error: invalid JSON in arguments (truncated or broken escaping). "
                "Split large content into smaller pieces or avoid special characters in strings."
            )

        # Validate required parameters from tool definition
        tool_def = all_tools[name]["definition"]
        required = tool_def.get("function", {}).get("parameters", {}).get("required", [])
        missing = [r for r in required if r not in args]
        if missing:
            got = list(args.keys())
            return (
                f"Error: missing required parameters: {', '.join(missing)} "
                f"(got: {', '.join(got) if got else 'none'}). "
                "Arguments were likely truncated. Use smaller content."
            )

        try:
            ctx.sandbox.begin_access_scope()
            return all_tools[name]["execute"](args, ctx)
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"
        finally:
            ctx.sandbox.end_access_scope()

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
