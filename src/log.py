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
        """Log an LLM request with full detail."""
        self._request_num += 1
        self._write(f"--- Request #{self._request_num} [{self._ts()}] ---")
        self._write(f"URL: {url}")

        # Full tool definitions
        if tools:
            self._write(f"\nTools ({len(tools)}):")
            self._write(json.dumps(tools, indent=2, ensure_ascii=False))

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
                self._write(content)
            elif role == "USER":
                self._write(f"\n>> USER ({tokens} tok):")
                self._write(content)
            elif role == "ASSISTANT":
                if tc:
                    self._write(f"\n>> ASSISTANT ({tokens} tok, tool_calls):")
                    if content:
                        self._write(content)
                    for c in tc:
                        fn = c.get("function", {})
                        self._write(f"  call: {fn.get('name', '?')}({fn.get('arguments', '{}')})")
                else:
                    self._write(f"\n>> ASSISTANT ({tokens} tok):")
                    if content:
                        self._write(content)
            elif role == "TOOL":
                tid = msg.get("tool_call_id", "?")
                self._write(f"\n>> TOOL id={tid} ({tokens} tok):")
                self._write(content)

        self._write(f"\nMessages: {len(messages)}, est. tokens: {msg_tokens}")
        self._write("")

    def response(self, content: str = None, tool_calls: list = None,
                 usage: dict = None, model: str = None):
        """Log an LLM response."""
        tokens = 0
        if content:
            tokens += self._estimate_tokens(content)
        if tool_calls:
            tokens += self._estimate_tokens(json.dumps(tool_calls))

        self._cumulative_tokens += tokens

        if tool_calls:
            self._write(f"<< ASSISTANT ({tokens} tok, {len(tool_calls)} tool_calls):")
            if content:
                self._write(content)
            for tc in tool_calls:
                self._write(f"  call id={tc.get('id', '?')}: {tc['name']}({json.dumps(tc['args'], ensure_ascii=False)})")
        else:
            self._write(f"<< ASSISTANT ({tokens} tok):")
            self._write(content or "(empty)")

        self._write(f"Cumulative tokens: {self._cumulative_tokens}")

        # Structured usage line for stats aggregation (always write, estimate if needed)
        if usage:
            u = {
                "prompt": usage.get("prompt_tokens", 0),
                "completion": usage.get("completion_tokens", 0),
                "total": usage.get("total_tokens", 0),
            }
        else:
            u = {
                "prompt": 0,
                "completion": self._estimate_tokens(content or ""),
                "total": self._estimate_tokens(content or ""),
            }
        if model:
            u["model"] = model
        self._write(f"USAGE: {json.dumps(u)}")
        self._write("")

    def tool_exec(self, name: str, args: dict, result: str, elapsed_ms: int = 0):
        """Log tool execution with full detail."""
        result_tokens = self._estimate_tokens(result)
        self._write(f"  Tool: {name}")
        self._write(f"  Args: {json.dumps(args, ensure_ascii=False)}")
        self._write(f"  Elapsed: {elapsed_ms}ms")
        self._write(f"  Result ({result_tokens} tok):")
        for line in result.split("\n"):
            self._write(f"    {line}")
        self._write("")

    def event(self, text: str):
        """Log a misc event."""
        self._write(f"[{self._ts()}] {text}")
        self._write("")

    def close(self):
        self._file.close()


def collect_stats(sessions_dir: Path) -> dict:
    """Parse all log files in sessions dir, aggregate token usage.

    Returns {
        "by_model": {model: {prompt, completion, total, requests}},
        "by_date":  {YYYY-MM-DD: {prompt, completion, total, requests}},
        "by_session": {session: {model_stats, date, requests}},
        "totals": {prompt, completion, total, requests},
    }
    """
    by_model = {}
    by_date = {}
    by_session = {}
    totals = {"prompt": 0, "completion": 0, "total": 0, "requests": 0}

    if not sessions_dir.is_dir():
        return {"by_model": by_model, "by_date": by_date,
                "by_session": by_session, "totals": totals}

    for session_dir in sorted(sessions_dir.iterdir()):
        log_path = session_dir / "log.txt"
        if not log_path.exists():
            continue

        session_name = session_dir.name
        session_date = None
        s_totals = {"requests": 0, "prompt": 0, "completion": 0, "total": 0}
        s_by_model = {}
        s_by_date = {}

        try:
            for line in log_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("Started: "):
                    session_date = line[9:19]

                if line.startswith("USAGE: "):
                    try:
                        u = json.loads(line[7:])
                    except json.JSONDecodeError:
                        continue

                    prompt = u.get("prompt", 0)
                    completion = u.get("completion", 0)
                    total = u.get("total", 0) or (prompt + completion)
                    model = u.get("model", "unknown")
                    date = session_date or "unknown"

                    def _add(d, key, p, c, t):
                        if key not in d:
                            d[key] = {"prompt": 0, "completion": 0, "total": 0, "requests": 0}
                        d[key]["prompt"] += p
                        d[key]["completion"] += c
                        d[key]["total"] += t
                        d[key]["requests"] += 1

                    _add(by_model, model, prompt, completion, total)
                    _add(by_date, date, prompt, completion, total)
                    _add(s_by_model, model, prompt, completion, total)
                    _add(s_by_date, date, prompt, completion, total)

                    s_totals["requests"] += 1
                    s_totals["prompt"] += prompt
                    s_totals["completion"] += completion
                    s_totals["total"] += total

                    totals["prompt"] += prompt
                    totals["completion"] += completion
                    totals["total"] += total
                    totals["requests"] += 1

        except OSError:
            continue

        if s_totals["requests"] > 0:
            by_session[session_name] = {
                **s_totals,
                "by_model": s_by_model,
                "by_date": s_by_date,
            }

    return {
        "by_model": by_model,
        "by_date": dict(sorted(by_date.items())),
        "by_session": by_session,
        "totals": totals,
    }
