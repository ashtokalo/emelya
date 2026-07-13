"""Interactive line editor with cursor, history, paste regions, and @-completion.

Paste regions: multi-line paste is wrapped in \\uE000...\\uE001 markers.
Display collapses them to [N lines, M bytes]. Cursor skips over them.
Sending to model: markers are stripped, content passes through.
"""

import os
import re
import select
import shutil
import sys
import termios
import tty

# --- Key constants ---

KEY_ENTER = "enter"
KEY_ESCAPE = "escape"
KEY_BACKSPACE = "backspace"
KEY_DELETE = "delete"
KEY_UP = "up"
KEY_DOWN = "down"
KEY_LEFT = "left"
KEY_RIGHT = "right"
KEY_HOME = "home"
KEY_END = "end"
KEY_CTRL_LEFT = "ctrl_left"
KEY_CTRL_RIGHT = "ctrl_right"
KEY_TAB = "tab"
KEY_SHIFT_TAB = "shift_tab"
KEY_CTRL_A = "\x01"
KEY_CTRL_C = "\x03"
KEY_CTRL_E = "\x05"
KEY_CTRL_K = "\x0b"
KEY_CTRL_U = "\x15"
KEY_CTRL_W = "\x17"

_PASTE_START = "paste_start"
_PASTE_END = "paste_end"

# Paste region markers (Unicode Private Use Area)
MARKER_START = "\uE000"
MARKER_END = "\uE001"

try:
    _fd = sys.stdin.fileno()
except Exception:
    _fd = 0  # fallback for non-TTY environments (testing, piped input)


# --- Low-level terminal I/O ---

def _read_byte():
    return os.read(_fd, 1)[0]


def _has_byte(timeout=0):
    r, _, _ = select.select([_fd], [], [], timeout)
    return bool(r)


def _read_utf8_char(first_byte):
    if first_byte < 0x80:
        return chr(first_byte)
    if first_byte & 0xE0 == 0xC0:
        return bytes([first_byte, _read_byte()]).decode("utf-8", errors="replace")
    if first_byte & 0xF0 == 0xE0:
        return bytes([first_byte, _read_byte(), _read_byte()]).decode("utf-8", errors="replace")
    if first_byte & 0xF8 == 0xF0:
        return bytes([first_byte, _read_byte(), _read_byte(), _read_byte()]).decode("utf-8", errors="replace")
    return chr(first_byte)


def read_key():
    """Read a single keypress in raw mode."""
    b = _read_byte()

    if b == 0x0D or b == 0x0A:
        return KEY_ENTER
    if b == 0x7F or b == 0x08:
        return KEY_BACKSPACE
    if b == 0x09:
        return KEY_TAB
    if b == 0x03:
        return KEY_CTRL_C
    if b in (0x01, 0x05, 0x0B, 0x15, 0x17):
        return chr(b)

    if b == 0x1B:
        if not _has_byte(0.05):
            return KEY_ESCAPE
        b2 = _read_byte()
        if b2 == 0x5B:
            b3 = _read_byte()
            if b3 == 0x41: return KEY_UP
            if b3 == 0x42: return KEY_DOWN
            if b3 == 0x43: return KEY_RIGHT
            if b3 == 0x44: return KEY_LEFT
            if b3 == 0x48: return KEY_HOME
            if b3 == 0x46: return KEY_END
            if b3 == 0x5A: return KEY_SHIFT_TAB
            if b3 == 0x33:
                _read_byte()
                return KEY_DELETE
            if b3 == 0x32:
                buf = [b3]
                while _has_byte(0.01):
                    nb = _read_byte()
                    buf.append(nb)
                    if nb == 0x7E:
                        break
                seq = bytes(buf).decode("ascii", errors="replace")
                if seq == "200~":
                    return _PASTE_START
                if seq == "201~":
                    return _PASTE_END
                return KEY_ESCAPE
            if b3 == 0x31:
                b4 = _read_byte()
                if b4 == 0x3B:
                    b5 = _read_byte()
                    b6 = _read_byte()
                    if b5 == 0x35:
                        if b6 == 0x43: return KEY_CTRL_RIGHT
                        if b6 == 0x44: return KEY_CTRL_LEFT
                        if b6 == 0x41: return KEY_UP
                        if b6 == 0x42: return KEY_DOWN
                while _has_byte(0.01):
                    _read_byte()
                return KEY_ESCAPE
            while _has_byte(0.01):
                _read_byte()
            return KEY_ESCAPE
        if b2 == 0x4F:
            b3 = _read_byte()
            if b3 == 0x48: return KEY_HOME
            if b3 == 0x46: return KEY_END
            return KEY_ESCAPE
        return KEY_ESCAPE

    if b == 0x20:
        return " "

    return _read_utf8_char(b)


# --- Paste region helpers ---

def _make_placeholder(text):
    """Create display placeholder for paste region content."""
    lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    size = len(text.encode("utf-8"))
    return f"[{lines} lines, {size} bytes]"


def _find_region(buf, pos):
    """If pos is inside a paste region (between MARKER_START and MARKER_END),
    return (start_idx, end_idx+1). Otherwise None."""
    # Find MARKER_START at or before pos
    start = None
    for i in range(pos, -1, -1):
        if i < len(buf) and buf[i] == MARKER_START:
            start = i
            break
        if i < len(buf) and buf[i] == MARKER_END:
            return None  # we hit an end marker first — not inside
    if start is None:
        return None
    # Find matching MARKER_END after start
    for i in range(start + 1, len(buf)):
        if buf[i] == MARKER_END:
            return (start, i + 1)
    return None


def _next_region_end(buf, pos):
    """If buf[pos] == MARKER_START, return index after MARKER_END. Else pos+1."""
    if pos < len(buf) and buf[pos] == MARKER_START:
        for i in range(pos + 1, len(buf)):
            if buf[i] == MARKER_END:
                return i + 1
        return len(buf)
    return pos + 1


def _prev_region_start(buf, pos):
    """If buf[pos-1] == MARKER_END, return index of MARKER_START. Else pos-1."""
    if pos > 0 and buf[pos - 1] == MARKER_END:
        for i in range(pos - 2, -1, -1):
            if buf[i] == MARKER_START:
                return i
        return 0
    return pos - 1


def strip_markers(text):
    """Remove paste markers from text (for sending to model)."""
    return text.replace(MARKER_START, "").replace(MARKER_END, "")


# --- Path completer ---

def path_completer(work_dir, query):
    """Complete file paths relative to work_dir."""
    base = os.path.join(str(work_dir), query) if query else str(work_dir)

    if os.path.isdir(base) and (not query or query.endswith("/")):
        parent, prefix = base, ""
    else:
        parent = os.path.dirname(base) or str(work_dir)
        prefix = os.path.basename(base)

    if not os.path.isdir(parent):
        return []
    try:
        entries = os.listdir(parent)
    except (PermissionError, OSError):
        return []

    if not prefix.startswith("."):
        entries = [e for e in entries if not e.startswith(".")]
    if prefix:
        lp = prefix.lower()
        entries = [e for e in entries if e.lower().startswith(lp)]
    if not entries:
        return []

    entries.sort(key=lambda e: (not os.path.isdir(os.path.join(parent, e)), e.lower()))

    results = []
    for entry in entries:
        full = os.path.join(parent, entry)
        try:
            rel = os.path.relpath(full, str(work_dir))
        except ValueError:
            rel = full
        if os.path.isdir(full):
            rel = rel.rstrip("/") + "/"
        results.append(rel)
    return results


# --- Line Editor ---

class LineEditor:
    """Interactive line editor. Buffer is list[str] (single chars + markers)."""

    def __init__(self, work_dir=None, extra_commands=None,
                 agents_dirs=None, skills_dirs=None):
        self.work_dir = work_dir or os.getcwd()
        self._prev_wrap_lines = 0
        self._extra_commands = list(extra_commands) if extra_commands else []
        self._agents_dirs = list(agents_dirs) if agents_dirs else []
        self._skills_dirs = list(skills_dirs) if skills_dirs else []

    def read(self, prompt="You: ", history=None):
        """Read a line. Returns str on Enter, None on Ctrl+C/Escape."""
        old_settings = termios.tcgetattr(_fd)
        tty.setraw(_fd)
        sys.stdout.write("\033[?2004h")  # enable bracketed paste
        sys.stdout.flush()

        try:
            return self._read_loop(prompt, history)
        finally:
            sys.stdout.write("\033[?2004l")
            sys.stdout.flush()
            termios.tcsetattr(_fd, termios.TCSADRAIN, old_settings)

    def _read_loop(self, prompt, history):
        buf = []
        cursor = 0
        hist_idx = len(history.items) if history else 0
        saved_buf = []
        comp_items = []
        comp_idx = -1
        comp_at_pos = -1

        self._draw(prompt, buf, cursor)

        while True:
            key = read_key()

            if key != KEY_TAB and key != KEY_SHIFT_TAB:
                comp_items = []
                comp_idx = -1
                comp_at_pos = -1

            # --- Enter ---
            if key == KEY_ENTER:
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                result = "".join(buf)
                if history and result.strip():
                    history.add(result)
                return strip_markers(result)

            # --- Cancel ---
            if key == KEY_ESCAPE or key == KEY_CTRL_C:
                self._erase(prompt, buf)
                return None

            # --- Bracketed paste ---
            if key == _PASTE_START:
                paste = self._read_paste()
                if paste:
                    if "\n" not in paste:
                        # Single line — insert as chars
                        for ch in paste:
                            buf.insert(cursor, ch)
                            cursor += 1
                    else:
                        # Multi-line — wrap in markers
                        for ch in MARKER_START + paste + MARKER_END:
                            buf.insert(cursor, ch)
                            cursor += 1
                    self._draw(prompt, buf, cursor)
                continue

            # --- Cursor movement (skip over paste regions) ---
            if key == KEY_LEFT:
                if cursor > 0:
                    cursor = _prev_region_start(buf, cursor)
                    self._draw(prompt, buf, cursor)
                continue

            if key == KEY_RIGHT:
                if cursor < len(buf):
                    cursor = _next_region_end(buf, cursor)
                    self._draw(prompt, buf, cursor)
                continue

            if key == KEY_HOME or key == KEY_CTRL_A:
                cursor = 0
                self._draw(prompt, buf, cursor)
                continue

            if key == KEY_END or key == KEY_CTRL_E:
                cursor = len(buf)
                self._draw(prompt, buf, cursor)
                continue

            if key == KEY_CTRL_LEFT:
                cursor = self._word_left(buf, cursor)
                self._draw(prompt, buf, cursor)
                continue

            if key == KEY_CTRL_RIGHT:
                cursor = self._word_right(buf, cursor)
                self._draw(prompt, buf, cursor)
                continue

            # --- Deletion (delete entire paste region if cursor is on marker) ---
            if key == KEY_BACKSPACE:
                if cursor > 0:
                    region = _find_region(buf, cursor - 1)
                    if region:
                        del buf[region[0]:region[1]]
                        cursor = region[0]
                    else:
                        buf.pop(cursor - 1)
                        cursor -= 1
                    self._draw(prompt, buf, cursor)
                continue

            if key == KEY_DELETE:
                if cursor < len(buf):
                    region = _find_region(buf, cursor)
                    if region:
                        del buf[region[0]:region[1]]
                    else:
                        buf.pop(cursor)
                    self._draw(prompt, buf, cursor)
                continue

            if key == KEY_CTRL_U:
                buf.clear()
                cursor = 0
                self._draw(prompt, buf, cursor)
                continue

            if key == KEY_CTRL_K:
                del buf[cursor:]
                self._draw(prompt, buf, cursor)
                continue

            if key == KEY_CTRL_W:
                new_cursor = self._word_left(buf, cursor)
                del buf[new_cursor:cursor]
                cursor = new_cursor
                self._draw(prompt, buf, cursor)
                continue

            # --- History ---
            if key == KEY_UP and history:
                if hist_idx > 0:
                    if hist_idx == len(history.items):
                        saved_buf = list(buf)
                    hist_idx -= 1
                    buf = list(history.items[hist_idx])
                    cursor = len(buf)
                    self._draw(prompt, buf, cursor)
                continue

            if key == KEY_DOWN and history:
                if hist_idx < len(history.items):
                    hist_idx += 1
                    if hist_idx == len(history.items):
                        buf = list(saved_buf) if saved_buf else []
                    else:
                        buf = list(history.items[hist_idx])
                    cursor = len(buf)
                    self._draw(prompt, buf, cursor)
                continue

            # --- Tab: completion (/command name, /command args, @path) ---
            if key == KEY_TAB or key == KEY_SHIFT_TAB:
                text = "".join(c for c in buf if isinstance(c, str))
                completed = False

                # Command name completion: /re<TAB> → /rename, /resume
                if text.startswith("/") and " " not in text:
                    prefix = text[1:]  # strip /
                    if comp_idx >= 0 and comp_items and comp_at_pos == 0:
                        if key == KEY_SHIFT_TAB:
                            comp_idx = (comp_idx - 1) % len(comp_items)
                        else:
                            comp_idx = (comp_idx + 1) % len(comp_items)
                    else:
                        comp_items = self._complete_command_name(prefix)
                        comp_at_pos = 0
                        comp_idx = 0 if comp_items else -1
                    if comp_idx >= 0 and comp_items:
                        buf.clear()
                        new_text = comp_items[comp_idx]
                        for ch in new_text:
                            buf.append(ch)
                        cursor = len(buf)
                        self._draw(prompt, buf, cursor)
                    completed = True

                # Command argument completion: /agent, /skill, /model, /resume, /mcp
                if not completed:
                    cmd_match = self._find_cmd_query(text, cursor, buf)
                    if cmd_match:
                        cmd, start_pos, query = cmd_match
                        if comp_idx >= 0 and comp_items and comp_at_pos == start_pos:
                            if key == KEY_SHIFT_TAB:
                                comp_idx = (comp_idx - 1) % len(comp_items)
                            else:
                                comp_idx = (comp_idx + 1) % len(comp_items)
                        else:
                            comp_items = self._command_completer(cmd, query)
                            comp_at_pos = start_pos
                            comp_idx = 0 if comp_items else -1
                        if comp_idx >= 0 and comp_items:
                            del buf[start_pos:cursor]
                            new_text = comp_items[comp_idx]
                            for i, ch in enumerate(new_text):
                                buf.insert(start_pos + i, ch)
                            cursor = start_pos + len(new_text)
                            self._draw(prompt, buf, cursor)
                        completed = True

                # @path completion
                if not completed:
                    at_pos, query = self._find_at_query(buf, cursor)
                    if at_pos >= 0:
                        if comp_idx >= 0 and comp_items and comp_at_pos == at_pos:
                            if key == KEY_SHIFT_TAB:
                                comp_idx = (comp_idx - 1) % len(comp_items)
                            else:
                                comp_idx = (comp_idx + 1) % len(comp_items)
                        else:
                            comp_items = path_completer(self.work_dir, query)
                            comp_at_pos = at_pos
                            comp_idx = 0 if comp_items else -1
                        if comp_idx >= 0 and comp_items:
                            new_text = "@" + comp_items[comp_idx]
                            del buf[at_pos:cursor]
                            for i, ch in enumerate(new_text):
                                buf.insert(at_pos + i, ch)
                            cursor = at_pos + len(new_text)
                            self._draw(prompt, buf, cursor)
                continue

            # --- Character input ---
            if isinstance(key, str) and len(key) == 1 and ord(key) >= 32:
                # Don't allow typing inside a paste region
                region = _find_region(buf, cursor)
                if region:
                    cursor = region[1]  # move past region
                buf.insert(cursor, key)
                cursor += 1
                self._draw(prompt, buf, cursor)

    def _read_paste(self):
        """Read bracketed paste until \\033[201~."""
        chars = []
        while True:
            if not _has_byte(0.5):
                break
            key = read_key()
            if key == _PASTE_END:
                break
            if key == KEY_ENTER:
                chars.append("\n")
            elif isinstance(key, str):
                chars.append(key)
        return "".join(chars)

    # All available /commands for name completion
    _ALL_COMMANDS = [
        "/help", "/clear", "/compact", "/undo", "/drop",
        "/rename", "/resume", "/branch", "/export",
        "/model", "/skill", "/agent",
        "/tools", "/mcp", "/commands",
        "/stats", "/context", "/history", "/exit",
    ]

    def _complete_command_name(self, prefix: str) -> list:
        """Complete /command name from prefix (without the /)."""
        prefix_lower = prefix.lower()
        all_cmds = self._ALL_COMMANDS + self._extra_commands
        return [c for c in all_cmds if c[1:].startswith(prefix_lower)]

    # Commands that support Tab completion for their first argument
    _COMPLETABLE_CMDS = {
        "/agent": "agents",
        "/skill": "skills",
        "/resume": "sessions",
        "/model": "models",
        "/mcp": "mcp",
    }

    @staticmethod
    def _find_cmd_query(text, cursor, buf):
        """Check if text starts with a completable command.
        Returns (cmd, start_pos, query) or None.
        start_pos points to the start of the last word being completed.
        query is the full argument string (for multi-level completers)."""
        for cmd, kind in LineEditor._COMPLETABLE_CMDS.items():
            prefix = cmd + " "
            if text.startswith(prefix):
                full_query = text[len(prefix):cursor]
                # For multi-level commands (/mcp), replace only the last word
                if kind == "mcp":
                    # Find start of last word
                    last_space = text.rfind(" ", len(prefix), cursor)
                    if last_space >= 0:
                        start_pos = last_space + 1
                    else:
                        start_pos = len(prefix)
                else:
                    start_pos = len(prefix)
                return cmd, start_pos, full_query
        return None

    def _command_completer(self, cmd, query):
        """Complete command arguments."""
        kind = self._COMPLETABLE_CMDS.get(cmd, "")
        query_lower = query.lower()

        if kind == "agents":
            # Use injected dirs list; fall back to built-in location
            dirs = self._agents_dirs or [
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "agents"),
            ]
            names: dict[str, None] = {}
            for d in dirs:
                names.update({n: None for n in self._list_md_names(str(d), query_lower)})
            return sorted(names)

        if kind == "skills":
            dirs = self._skills_dirs or [
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills"),
            ]
            names: dict[str, None] = {}
            for d in dirs:
                names.update({n: None for n in self._list_md_names(str(d), query_lower)})
            return sorted(names)

        if kind == "sessions":
            sessions_dir = os.path.join(str(self.work_dir), ".emelya", "sessions")
            if os.path.isdir(sessions_dir):
                names = sorted(os.listdir(sessions_dir))
                if query_lower:
                    names = [n for n in names if n.lower().startswith(query_lower)]
                return names
            return []

        if kind == "models":
            import json as _json
            models_path = os.path.join(str(self.work_dir), ".emelya", "models.json")
            names = ["local", "auto"]
            try:
                data = _json.loads(open(models_path).read())
                names.extend(data.get("models", {}).keys())
            except Exception:
                pass
            # Also complete subcommands: add, remove
            names.extend(["add", "remove"])
            if query_lower:
                names = [n for n in names if n.lower().startswith(query_lower)]
            return names

        if kind == "mcp":
            return self._complete_mcp(query, query_lower)

        return []

    def _complete_mcp(self, query, query_lower):
        """Multi-level completion for /mcp subcommands.
        /mcp <TAB>            → add, remove, refresh, tools, call
        /mcp tools <TAB>      → server names
        /mcp call <TAB>       → server names
        /mcp call <name> <TAB> → tool names for that server
        /mcp remove <TAB>     → server names
        /mcp refresh <TAB>    → server names
        """
        parts = query.split()
        # No subcommand yet — complete subcommand
        if not parts or (len(parts) == 1 and not query.endswith(" ")):
            subcmds = ["add", "remove", "refresh", "tools", "call"]
            prefix = parts[0].lower() if parts else ""
            return [s for s in subcmds if s.startswith(prefix)]

        subcmd = parts[0].lower()
        server_names = self._list_mcp_servers()

        # /mcp tools|call|remove|refresh <TAB> → server names
        if subcmd in ("tools", "call", "remove", "refresh"):
            if len(parts) == 1 and query.endswith(" "):
                # No server name typed yet
                return server_names
            if len(parts) == 2 and not query.endswith(" "):
                # Partial server name
                prefix = parts[1].lower()
                return [n for n in server_names if n.lower().startswith(prefix)]
            # /mcp call <name> <TAB> → tool names
            if subcmd == "call" and len(parts) >= 2:
                srv_name = parts[1]
                tool_names = self._list_mcp_tools(srv_name)
                if len(parts) == 2 and query.endswith(" "):
                    return tool_names
                if len(parts) == 3 and not query.endswith(" "):
                    prefix = parts[2].lower()
                    return [t for t in tool_names if t.lower().startswith(prefix)]

        return []

    def _list_mcp_servers(self):
        """List configured MCP server names from mcp.json."""
        import json as _json
        config_path = os.path.join(str(self.work_dir), ".emelya", "mcp.json")
        try:
            data = _json.loads(open(config_path).read())
            return sorted(data.get("servers", {}).keys())
        except Exception:
            return []

    def _list_mcp_tools(self, server_name):
        """List cached tool names for an MCP server."""
        import json as _json
        cache_path = os.path.join(str(self.work_dir), ".emelya", "mcp_cache", f"{server_name}.json")
        try:
            tools = _json.loads(open(cache_path).read())
            return sorted(t.get("name", "") for t in tools if t.get("name"))
        except Exception:
            return []

    @staticmethod
    def _list_md_names(directory, prefix=""):
        """List .md file stems from directory, filtered by prefix."""
        try:
            names = sorted(
                f[:-3] for f in os.listdir(directory)
                if f.endswith(".md") and not f.startswith(".")
            )
            if prefix:
                names = [n for n in names if n.lower().startswith(prefix)]
            return names
        except OSError:
            return []

    @staticmethod
    def _find_at_query(buf, cursor):
        """Find @path query before cursor."""
        pos = cursor - 1
        while pos >= 0:
            ch = buf[pos]
            if ch in (MARKER_START, MARKER_END):
                return -1, ""
            if ch == " " or ch == "\t":
                return -1, ""
            if ch == "@":
                query = "".join(buf[pos + 1:cursor])
                return pos, query
            pos -= 1
        return -1, ""

    # --- Display ---

    def _buf_to_display(self, buf):
        """Render buffer for terminal: replace marker regions with placeholders."""
        parts = []
        i = 0
        while i < len(buf):
            if buf[i] == MARKER_START:
                # Collect content until MARKER_END
                content = []
                i += 1
                while i < len(buf) and buf[i] != MARKER_END:
                    content.append(buf[i])
                    i += 1
                if i < len(buf):
                    i += 1  # skip MARKER_END
                text = "".join(content)
                placeholder = _make_placeholder(text)
                parts.append(f"\033[7m{placeholder}\033[0m")
            elif buf[i] == MARKER_END:
                i += 1  # orphaned end marker, skip
            else:
                parts.append(buf[i])
                i += 1
        return "".join(parts)

    def _display_len(self, buf):
        """Visible length of displayed buffer (placeholders counted by their display length)."""
        length = 0
        i = 0
        while i < len(buf):
            if buf[i] == MARKER_START:
                content = []
                i += 1
                while i < len(buf) and buf[i] != MARKER_END:
                    content.append(buf[i])
                    i += 1
                if i < len(buf):
                    i += 1
                length += len(_make_placeholder("".join(content)))
            elif buf[i] == MARKER_END:
                i += 1
            else:
                length += 1
                i += 1
        return length

    def _cursor_to_display_pos(self, buf, cursor):
        """Map buffer cursor to display column position."""
        pos = 0
        i = 0
        while i < len(buf) and i < cursor:
            if buf[i] == MARKER_START:
                content = []
                i += 1
                while i < len(buf) and buf[i] != MARKER_END:
                    content.append(buf[i])
                    i += 1
                if i < len(buf):
                    i += 1
                if i <= cursor:
                    # Cursor is past the entire region
                    pos += len(_make_placeholder("".join(content)))
                else:
                    # Cursor is inside region — show at end of placeholder
                    pos += len(_make_placeholder("".join(content)))
            elif buf[i] == MARKER_END:
                i += 1
            else:
                pos += 1
                i += 1
        return pos

    def _get_term_width(self):
        try:
            return shutil.get_terminal_size().columns
        except Exception:
            return 80

    @staticmethod
    def _visible_len(s):
        """Length excluding ANSI escape codes."""
        import re
        return len(re.sub(r"\033\[[0-9;]*[A-Za-z]", "", s))

    def _draw(self, prompt, buf, cursor):
        display = self._buf_to_display(buf)
        prompt_len = self._visible_len(prompt)
        full = prompt + display
        display_len = prompt_len + self._display_len(buf)
        width = self._get_term_width()

        cur_row = self._prev_wrap_lines
        if cur_row > 0:
            sys.stdout.write(f"\033[{cur_row}A")

        sys.stdout.write(f"\r{full}\033[J")

        abs_pos = prompt_len + self._cursor_to_display_pos(buf, cursor)
        wrap_lines = max(0, (display_len - 1) // width) if width > 0 else 0
        cursor_row = abs_pos // width if width > 0 else 0
        cursor_col = abs_pos % width if width > 0 else abs_pos

        lines_from_end = wrap_lines - cursor_row
        if lines_from_end > 0:
            sys.stdout.write(f"\033[{lines_from_end}A")
        elif lines_from_end < 0:
            sys.stdout.write(f"\033[{-lines_from_end}B")
        sys.stdout.write(f"\r\033[{cursor_col + 1}G")
        sys.stdout.flush()

        self._prev_wrap_lines = cursor_row

    def _erase(self, prompt, buf):
        prev = self._prev_wrap_lines
        if prev > 0:
            sys.stdout.write(f"\033[{prev}A")
        sys.stdout.write("\r\033[J")
        sys.stdout.flush()
        self._prev_wrap_lines = 0

    @staticmethod
    def _word_left(buf, cursor):
        pos = cursor
        # Skip markers and spaces
        while pos > 0 and buf[pos - 1] in (" ", MARKER_START, MARKER_END):
            pos -= 1
        while pos > 0 and buf[pos - 1] not in (" ", MARKER_START, MARKER_END):
            pos -= 1
        return pos

    @staticmethod
    def _word_right(buf, cursor):
        pos = cursor
        n = len(buf)
        while pos < n and buf[pos] not in (" ", MARKER_START, MARKER_END):
            pos += 1
        while pos < n and buf[pos] in (" ", MARKER_START, MARKER_END):
            pos += 1
        return pos
