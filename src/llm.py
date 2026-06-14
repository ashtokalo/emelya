"""LLM client — HTTP communication with llama.cpp server via urllib."""

import json
import socket
import time
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


class LLMClient:
    """HTTP client for OpenAI-compatible llama.cpp server."""

    def __init__(self, base_url: str, model: str = "default",
                 retry: int = 2, retry_delay: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.retry = retry
        self.retry_delay = retry_delay

    def check_connection(self) -> tuple:
        """Check server availability. Returns (ok, message)."""
        try:
            req = urllib.request.Request(f"{self.base_url}/health")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return True, "OK"
                return False, f"Health check returned {resp.status}"
        except Exception as e:
            return False, f"Server unavailable: {e}"

    def _post(self, url: str, data: dict, timeout: int = 120):
        """Raw POST, returns http.client.HTTPResponse (not closed)."""
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return urllib.request.urlopen(req, timeout=timeout)

    def _post_json(self, url: str, data: dict, timeout: int = 120) -> dict:
        """POST and parse JSON response."""
        with self._post(url, data, timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _repair_json(raw: str) -> dict:
        """Try to parse JSON, attempt simple repairs if it fails."""
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Remove trailing commas before } or ]
        import re
        cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        # Try closing unclosed braces
        opens = raw.count("{") - raw.count("}")
        if opens > 0:
            cleaned = raw + "}" * opens
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass
        raise json.JSONDecodeError(f"Cannot parse tool arguments", raw, 0)

    @staticmethod
    def _parse_tool_calls(raw_calls: list) -> list:
        """Parse tool_calls from API response into clean dicts."""
        result = []
        for tc in raw_calls:
            fn = tc.get("function", {})
            args_raw = fn.get("arguments", "{}")
            try:
                args = LLMClient._repair_json(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {"_raw_error": args_raw}
            result.append({
                "id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "args": args,
            })
        return result

    def chat(self, messages: list, tools: list = None,
             temperature: float = 0.7, max_tokens: int = 1024) -> dict:
        """Non-streaming chat completion.

        Returns dict with:
          content: str | None
          tool_calls: list[dict] | None  (each: {id, name, args})
          raw: dict (full API response message)
        """
        url = f"{self.base_url}/v1/chat/completions"
        data = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            data["tools"] = tools

        last_err = None
        for attempt in range(self.retry + 1):
            try:
                resp = self._post_json(url, data)
                msg = resp["choices"][0]["message"]
                usage = resp.get("usage")  # {prompt_tokens, completion_tokens, total_tokens}
                return {
                    "content": msg.get("content"),
                    "tool_calls": self._parse_tool_calls(msg["tool_calls"]) if msg.get("tool_calls") else None,
                    "raw": msg,
                    "usage": usage,
                }
            except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
                last_err = e
                if attempt < self.retry:
                    time.sleep(self.retry_delay)
            except (KeyError, IndexError) as e:
                raise LLMError(f"Unexpected API response format: {e}")

        raise LLMError(f"Connection failed after {self.retry + 1} attempts: {last_err}")

    def stream_chat(self, messages: list, tools: list = None,
                    temperature: float = 0.7, max_tokens: int = 1024,
                    on_content=None) -> dict:
        """Streaming chat completion.

        on_content(text) is called for each content delta (for terminal output).

        Returns same format as chat().
        """
        url = f"{self.base_url}/v1/chat/completions"
        data = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            data["tools"] = tools

        last_err = None
        for attempt in range(self.retry + 1):
            try:
                resp = self._post(url, data, timeout=300)
                content, tool_calls, usage = self._read_stream(resp, on_content)
                resp.close()

                parsed_tc = None
                if tool_calls:
                    finalized = []
                    for tc in tool_calls:
                        args_raw = tc.get("_arguments", "")
                        try:
                            args = self._repair_json(args_raw) if args_raw else {}
                        except json.JSONDecodeError:
                            args = {"_raw_error": args_raw}
                        finalized.append({
                            "id": tc.get("id", ""),
                            "name": tc.get("name", ""),
                            "args": args,
                        })
                    parsed_tc = finalized

                raw = {"role": "assistant", "content": content or None}
                if parsed_tc:
                    raw["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["args"]),
                            },
                        }
                        for tc in parsed_tc
                    ]

                return {
                    "content": content or None,
                    "tool_calls": parsed_tc,
                    "raw": raw,
                    "usage": usage,
                }
            except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
                last_err = e
                if attempt < self.retry:
                    time.sleep(self.retry_delay)

        raise LLMError(f"Connection failed after {self.retry + 1} attempts: {last_err}")

    def _read_stream(self, resp, on_content) -> tuple:
        """Read SSE stream, return (content_str, tool_calls_list, usage_dict)."""
        content_parts = []
        # tool_calls accumulated by index: {index: {id, name, _arguments}}
        tc_accum = {}
        usage = None

        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if line == "data: [DONE]":
                break
            if not line.startswith("data: "):
                continue

            try:
                chunk = json.loads(line[6:])
            except json.JSONDecodeError:
                continue

            # Usage may come in final chunk
            if "usage" in chunk:
                usage = chunk["usage"]

            choices = chunk.get("choices")
            if not choices:
                continue
            delta = choices[0].get("delta", {})

            # Content delta
            text = delta.get("content")
            if text:
                content_parts.append(text)
                if on_content:
                    on_content(text)

            # Tool call deltas
            tc_deltas = delta.get("tool_calls")
            if tc_deltas:
                for tcd in tc_deltas:
                    idx = tcd.get("index", 0)
                    if idx not in tc_accum:
                        tc_accum[idx] = {
                            "id": tcd.get("id", ""),
                            "name": "",
                            "_arguments": "",
                        }
                    entry = tc_accum[idx]
                    if tcd.get("id"):
                        entry["id"] = tcd["id"]
                    fn = tcd.get("function", {})
                    if fn.get("name"):
                        entry["name"] = fn["name"]
                    if fn.get("arguments"):
                        entry["_arguments"] += fn["arguments"]

        content = "".join(content_parts)
        tool_calls = [tc_accum[i] for i in sorted(tc_accum)] if tc_accum else None
        return content, tool_calls, usage
