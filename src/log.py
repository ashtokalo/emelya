"""Session logging — detailed human-readable log."""

import json
from datetime import datetime
from pathlib import Path


class SessionLog:
    """Writes detailed session log to a text file."""

    def __init__(self, log_path: Path):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = log_path
        self._file = open(self.path, "a", encoding="utf-8")
        self._request_num = 0
        self._cumulative_tokens = 0

    def _write(self, text: str):
        self._file.write(text + "\n")
        self._file.flush()

    def _ts(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return int(len(text) / 3.5) if text else 0

    def session_start(self, server: str, model: str, work_dir: str, session_name: str):
        self._write(f"=== Session: {session_name} ===")
        self._write(f"Started: {self._ts()}")
        self._write(f"Server: {server}")
        self._write(f"Model: {model}")
        self._write(f"Work dir: {work_dir}")
        self._write("")

    def session_end(self):
        self._write(f"--- Session ended [{self._ts()}] ---")
        self._write(f"Total estimated tokens: {self._cumulative_tokens}")
        self._write("")

    def request(self, url: str, messages: list, tools: list = None):
        """Log an LLM request."""
        self._request_num += 1
        self._write(f"--- Request #{self._request_num} [{self._ts()}] ---")
        self._write(f"URL: {url}")

        if tools:
            tool_names = [t["function"]["name"] for t in tools]
            self._write(f"Tools: {', '.join(tool_names)}")

        msg_tokens = 0
        for msg in messages:
            role = msg["role"].upper()
            content = msg.get("content") or ""
            tokens = self._estimate_tokens(content)
            tc = msg.get("tool_calls")
            if tc:
                tokens += self._estimate_tokens(json.dumps(tc))

            msg_tokens += tokens

            if role == "SYSTEM":
                self._write(f"\n>> SYSTEM ({tokens} tok):")
                self._write(content[:500] + ("..." if len(content) > 500 else ""))
            elif role == "USER":
                self._write(f"\n>> USER ({tokens} tok):")
                self._write(content)
            elif role == "ASSISTANT":
                if tc:
                    tc_desc = ", ".join(
                        f"{c['function']['name']}(...)" for c in tc
                    )
                    self._write(f"\n>> ASSISTANT ({tokens} tok, tool_calls: {tc_desc}):")
                else:
                    self._write(f"\n>> ASSISTANT ({tokens} tok):")
                if content:
                    self._write(content[:300] + ("..." if len(content) > 300 else ""))
            elif role == "TOOL":
                tid = msg.get("tool_call_id", "?")
                self._write(f"\n>> TOOL id={tid} ({tokens} tok):")
                self._write(content[:300] + ("..." if len(content) > 300 else ""))

        self._write(f"\nMessages: {len(messages)}, est. tokens: {msg_tokens}")
        self._write("")

    def response(self, content: str = None, tool_calls: list = None):
        """Log an LLM response."""
        tokens = 0
        if content:
            tokens += self._estimate_tokens(content)
        if tool_calls:
            tokens += self._estimate_tokens(json.dumps(tool_calls))

        self._cumulative_tokens += tokens

        if tool_calls and not content:
            tc_desc = ", ".join(
                f"{tc['name']}({json.dumps(tc['args'], ensure_ascii=False)[:100]})"
                for tc in tool_calls
            )
            self._write(f"<< ASSISTANT tool_calls ({tokens} tok): {tc_desc}")
        elif tool_calls and content:
            tc_desc = ", ".join(f"{tc['name']}(...)" for tc in tool_calls)
            self._write(f"<< ASSISTANT ({tokens} tok, +tool_calls: {tc_desc}):")
            self._write(content[:300] + ("..." if len(content) > 300 else ""))
        else:
            self._write(f"<< ASSISTANT ({tokens} tok):")
            self._write((content or "(empty)")[:500] + ("..." if content and len(content) > 500 else ""))

        self._write(f"Cumulative tokens: {self._cumulative_tokens}")
        self._write("")

    def tool_exec(self, name: str, args: dict, result: str, elapsed_ms: int = 0):
        """Log tool execution."""
        result_tokens = self._estimate_tokens(result)
        self._write(f"  Tool: {name}")
        self._write(f"  Args: {json.dumps(args, ensure_ascii=False)[:200]}")
        self._write(f"  Result ({result_tokens} tok, {elapsed_ms}ms):")
        for line in result[:500].split("\n"):
            self._write(f"    {line}")
        if len(result) > 500:
            self._write("    ...")
        self._write("")

    def event(self, text: str):
        """Log a misc event."""
        self._write(f"[{self._ts()}] {text}")
        self._write("")

    def close(self):
        self._file.close()
