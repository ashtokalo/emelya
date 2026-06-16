"""LLM client — HTTP communication with OpenAI-compatible API via urllib."""

import json
import socket
import ssl
import time
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


class LLMClient:
    """HTTP client for OpenAI-compatible API (llama.cpp, OpenAI, DeepSeek, OpenRouter)."""

    def __init__(self, base_url: str, model: str = "default",
                 api_key: str = None,
                 retry: int = 2, retry_delay: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.retry = retry
        self.retry_delay = retry_delay

    def check_connection(self) -> tuple:
        """Check server availability. Returns (ok, info_message)."""
        # Try /v1/models first (returns model list), then /health (llama.cpp)
        try:
            req = urllib.request.Request(f"{self.base_url}/v1/models")
            if self.api_key:
                req.add_header("Authorization", f"Bearer {self.api_key}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    models = data.get("data", [])
                    if models:
                        ids = [m.get("id", "?") for m in models[:5]]
                        return True, f"models: {', '.join(ids)}"
                    return True, "OK"
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return False, "Authentication failed (invalid API key)"
        except Exception:
            pass
        # Fallback: /health (llama.cpp)
        try:
            req = urllib.request.Request(f"{self.base_url}/health")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return True, "OK"
        except Exception:
            pass
        return False, "Server unavailable"

    def _post(self, url: str, data: dict, timeout: int = 120):
        """Raw POST, returns http.client.HTTPResponse (not closed)."""
        body = json.dumps(data).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            url, data=body, headers=headers, method="POST",
        )
        return urllib.request.urlopen(req, timeout=timeout)

    def _post_json(self, url: str, data: dict, timeout: int = 120) -> dict:
        """POST and parse JSON response."""
        with self._post(url, data, timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _repair_json(raw: str) -> dict:
        """Try to parse JSON, attempt simple repairs if it fails."""
        import re

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Remove trailing commas before } or ]
        cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Try closing unclosed braces/brackets
        opens = raw.count("{") - raw.count("}")
        if opens > 0:
            # Close unclosed string if needed
            quote_count = raw.count('"') - raw.count('\\"')
            suffix = '"' if quote_count % 2 != 0 else ""
            suffix += "}" * opens
            try:
                return json.loads(raw + suffix)
            except json.JSONDecodeError:
                pass

        # Truncated string value — find last complete key-value pair
        # Match pattern: {"key1": "val1", "key2": "val2", "key3": "trunca...
        # Strategy: find the last complete "key": "value" and close the object
        match = re.search(r'^(\{.*"[^"]+"\s*:\s*"(?:[^"\\]|\\.)*")\s*,\s*"[^"]*"?\s*:\s*"[^"]*$', raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1) + "}")
            except json.JSONDecodeError:
                pass

        # Last resort — try to find any valid JSON prefix
        for end_pos in range(len(raw) - 1, 0, -1):
            if raw[end_pos] in ('"', '}', ']'):
                candidate = raw[:end_pos + 1]
                # Try closing
                opens_c = candidate.count("{") - candidate.count("}")
                if opens_c >= 0:
                    try:
                        return json.loads(candidate + "}" * opens_c)
                    except json.JSONDecodeError:
                        continue

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
                usage = resp.get("usage")
                return {
                    "content": msg.get("content"),
                    "tool_calls": self._parse_tool_calls(msg["tool_calls"]) if msg.get("tool_calls") else None,
                    "raw": msg,
                    "usage": usage,
                    "model_id": resp.get("model"),
                }
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                last_err = e
                self._dump_failed_request(data, f"HTTP {e.code}: {body}")
                if e.code in (400, 422):
                    raise LLMError(f"API error {e.code}: {body}")
                if attempt < self.retry:
                    time.sleep(self.retry_delay)
            except (urllib.error.URLError, socket.timeout, ConnectionError, ssl.SSLError, OSError) as e:
                last_err = e
                if attempt < self.retry:
                    time.sleep(self.retry_delay)
            except (KeyError, IndexError) as e:
                raise LLMError(f"Unexpected API response format: {e}")

        raise LLMError(f"Connection failed after {self.retry + 1} attempts: {last_err}")

    @staticmethod
    def _dump_failed_request(data: dict, error: str):
        """Dump failed request to /tmp for debugging."""
        import os
        from pathlib import Path
        dump_dir = Path("/tmp/emelya_debug")
        dump_dir.mkdir(exist_ok=True)
        ts = time.strftime("%H%M%S")
        dump_path = dump_dir / f"request_{ts}.json"
        try:
            # Summarize messages to avoid huge dumps
            summary = {
                "error": error,
                "model": data.get("model"),
                "stream": data.get("stream"),
                "n_tools": len(data.get("tools") or []),
                "n_messages": len(data.get("messages", [])),
                "messages": [],
            }
            for m in data.get("messages", []):
                role = m.get("role", "?")
                content_len = len(m.get("content") or "")
                tc = len(m.get("tool_calls") or [])
                tid = m.get("tool_call_id", "")
                entry = {"role": role, "content_len": content_len}
                if tc:
                    entry["tool_calls"] = tc
                if tid:
                    entry["tool_call_id"] = tid
                # Include first/last messages in full
                if role in ("system",) or len(summary["messages"]) < 2:
                    entry["content_preview"] = (m.get("content") or "")[:200]
                summary["messages"].append(entry)
            # Also dump full request for detailed analysis
            dump_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
            full_path = dump_dir / f"request_{ts}_full.json"
            full_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

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
            "stream_options": {"include_usage": True},
        }
        if tools:
            data["tools"] = tools

        last_err = None
        for attempt in range(self.retry + 1):
            try:
                resp = self._post(url, data, timeout=300)
                content, tool_calls, usage, stream_model_id = self._read_stream(resp, on_content)
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
                    "model_id": stream_model_id,
                }
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                last_err = e
                self._dump_failed_request(data, f"HTTP {e.code}: {body}")
                if e.code in (400, 422):
                    raise LLMError(f"API error {e.code}: {body}")
                if attempt < self.retry:
                    time.sleep(self.retry_delay)
            except (urllib.error.URLError, socket.timeout, ConnectionError, ssl.SSLError, OSError) as e:
                last_err = e
                if attempt < self.retry:
                    time.sleep(self.retry_delay)

        raise LLMError(f"Connection failed after {self.retry + 1} attempts: {last_err}")

    def _read_stream(self, resp, on_content) -> tuple:
        """Read SSE stream, return (content_str, tool_calls_list, usage_dict, model_id)."""
        content_parts = []
        tc_accum = {}
        usage = None
        model_id = None

        try:
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

                if "usage" in chunk:
                    usage = chunk["usage"]
                if "model" in chunk and not model_id:
                    model_id = chunk["model"]

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
        except (ssl.SSLError, OSError, ConnectionError):
            # Stream interrupted — return what we have so far
            pass

        content = "".join(content_parts)
        tool_calls = [tc_accum[i] for i in sorted(tc_accum)] if tc_accum else None
        return content, tool_calls, usage, model_id
