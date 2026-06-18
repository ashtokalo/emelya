"""MCP client — Model Context Protocol over stdio transport."""

import json
import os
import subprocess
import threading
from pathlib import Path


class MCPError(Exception):
    pass


class MCPClient:
    """JSON-RPC 2.0 client over stdio for MCP servers."""

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, name: str, command: str, args: list = None,
                 env: dict = None, work_dir: str = None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env
        self.work_dir = work_dir
        self._process = None
        self._id = 0
        self._lock = threading.Lock()
        self.server_info = None
        self.tools = []  # raw tool definitions from server

    def start(self, timeout: int = 30, on_status=None):
        """Start the MCP server process.

        timeout: seconds to wait for each step
        on_status: callback(message) for progress updates
        """
        def status(msg):
            if on_status:
                on_status(msg)

        cmd = [self.command] + self.args
        env = dict(os.environ)
        if self.env:
            env.update(self.env)

        status(f"Starting: {' '.join(cmd[:3])}...")
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=self.work_dir,
            )
        except FileNotFoundError:
            raise MCPError(f"Command not found: {self.command}")
        except Exception as e:
            raise MCPError(f"Failed to start: {e}")

        self._timeout = timeout

        # Initialize
        status("Connecting...")
        result = self._request("initialize", {
            "protocolVersion": self.PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "emelya", "version": "1.0"},
        })
        self.server_info = result.get("serverInfo", {})
        server_name = self.server_info.get("name", "?")
        status(f"Connected to {server_name}")

        # Send initialized notification
        self._notify("notifications/initialized")

        # List tools
        status("Loading tools...")
        result = self._request("tools/list", {})
        self.tools = result.get("tools", [])
        status(f"Ready ({len(self.tools)} tools)")

    def stop(self):
        """Stop the MCP server process."""
        if self._process:
            try:
                self._process.stdin.close()
                self._process.wait(timeout=5)
            except Exception:
                self._process.kill()
            self._process = None

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _ensure_running(self):
        """Restart server if it died."""
        if not self.is_alive:
            self.stop()  # cleanup
            self.start(timeout=getattr(self, "_timeout", 30))

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call an MCP tool, return result as string. Auto-reconnects if server died."""
        self._ensure_running()
        result = self._request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        # MCP returns content as array of {type, text} objects
        content = result.get("content", [])
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "image":
                    parts.append(f"[image: {item.get('mimeType', 'unknown')}]")
                else:
                    parts.append(str(item))
            else:
                parts.append(str(item))
        return "\n".join(parts) if parts else "(empty result)"

    def _next_id(self) -> int:
        with self._lock:
            self._id += 1
            return self._id

    def _request(self, method: str, params: dict) -> dict:
        """Send JSON-RPC request, return result."""
        if not self._process or self._process.poll() is not None:
            raise MCPError(f"MCP server '{self.name}' is not running")

        msg = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params,
        }
        self._send(msg)
        response = self._recv()

        if "error" in response:
            err = response["error"]
            code = err.get("code", 0)
            message = err.get("message", "Unknown error")
            raise MCPError(f"MCP error ({code}): {message}")

        return response.get("result", {})

    def _notify(self, method: str, params: dict = None):
        """Send JSON-RPC notification (no response expected)."""
        msg = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params:
            msg["params"] = params
        self._send(msg)

    def _send(self, msg: dict):
        """Write JSON-RPC message to stdin."""
        data = json.dumps(msg) + "\n"
        try:
            self._process.stdin.write(data.encode("utf-8"))
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise MCPError(f"MCP server '{self.name}' pipe broken: {e}")

    def _recv(self) -> dict:
        """Read JSON-RPC response from stdout with timeout."""
        timeout = getattr(self, "_timeout", 30)
        result = [None]
        error = [None]

        def _read():
            try:
                while True:
                    line = self._process.stdout.readline()
                    if not line:
                        stderr_data = ""
                        try:
                            stderr_data = self._process.stderr.read().decode(errors="replace")[:300]
                        except Exception:
                            pass
                        error[0] = MCPError(
                            f"MCP server '{self.name}' closed unexpectedly"
                            + (f": {stderr_data}" if stderr_data else "")
                        )
                        return
                    line = line.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # Skip notifications (no "id" field)
                    if "id" in msg:
                        result[0] = msg
                        return
            except Exception as e:
                error[0] = MCPError(f"Read error: {e}")

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        reader.join(timeout=timeout)

        if reader.is_alive():
            # Timeout — try to get stderr for diagnostics
            stderr_peek = ""
            try:
                import select
                if select.select([self._process.stderr], [], [], 0)[0]:
                    stderr_peek = self._process.stderr.read(500).decode(errors="replace")
            except Exception:
                pass
            hint = f": {stderr_peek}" if stderr_peek else " (server not responding)"
            raise MCPError(f"Timeout ({timeout}s) waiting for '{self.name}'{hint}")

        if error[0]:
            raise error[0]
        if result[0] is None:
            raise MCPError(f"No response from '{self.name}'")
        return result[0]

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


def compact_tool_description(tool: dict) -> dict:
    """Create a compact version of an MCP tool definition for small models.

    - Truncate description to first sentence
    - Keep only required parameters
    - Shorten parameter descriptions
    """
    fn = tool.copy()
    # Shorten description
    desc = fn.get("description", "")
    if desc:
        # First sentence (up to first period, question mark, or 100 chars)
        for i, c in enumerate(desc):
            if c in ".!?" and i > 10:
                desc = desc[:i + 1]
                break
        if len(desc) > 120:
            desc = desc[:117] + "..."
        fn["description"] = desc

    # Simplify parameters
    schema = fn.get("inputSchema", {})
    if schema:
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        compact_props = {}
        for name, prop in props.items():
            compact_prop = {"type": prop.get("type", "string")}
            prop_desc = prop.get("description", "")
            if prop_desc:
                # Truncate param description to 60 chars
                if len(prop_desc) > 60:
                    prop_desc = prop_desc[:57] + "..."
                compact_prop["description"] = prop_desc
            if "enum" in prop:
                compact_prop["enum"] = prop["enum"]
            compact_props[name] = compact_prop
        schema = {
            "type": "object",
            "properties": compact_props,
        }
        if required:
            schema["required"] = list(required)
        fn["inputSchema"] = schema

    return fn


def load_mcp_config(config_path: Path) -> dict:
    """Load MCP server configuration from .emelya/mcp.json.

    Format:
    {
      "servers": {
        "name": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-xxx"],
          "env": {"KEY": "value"},
          "description": "What this server does"
        }
      }
    }
    """
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return data.get("servers", {})
    except (json.JSONDecodeError, OSError):
        return {}


def load_cache(cache_dir: Path, server_name: str) -> list:
    """Load cached compact tool definitions."""
    path = cache_dir / f"{server_name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_cache(cache_dir: Path, server_name: str, tools: list):
    """Save compact tool definitions to cache."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{server_name}.json"
    path.write_text(json.dumps(tools, indent=2, ensure_ascii=False), encoding="utf-8")
