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
        """Check server availability. Returns (ok, info_message).
        Also sets self.detected_context_limit if discoverable."""
        self.detected_context_limit = None

        # Try /v1/models (OpenAI-compatible)
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
                        # Some APIs return context_length in model info
                        for m in models:
                            ctx = m.get("context_length") or m.get("context_window")
                            if ctx and isinstance(ctx, int):
                                self.detected_context_limit = ctx
                                break
                        return True, f"models: {', '.join(ids)}"
                    return True, "OK"
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return False, "Authentication failed (invalid API key)"
        except Exception:
            pass

        # Try /props (llama.cpp — returns n_ctx)
        try:
            req = urllib.request.Request(f"{self.base_url}/props")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    n_ctx = data.get("n_ctx")
                    if n_ctx and isinstance(n_ctx, int):
                        self.detected_context_limit = n_ctx
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
    def _parse_native_tool_calls(content: str) -> list:
        """Parse native tool-call formats that leak through instead of
        proper OpenAI function calling.

        Supported formats:
          - DeepSeek DSML: <｜｜DSML｜｜invoke name="...">
          - Qwen/Hermes: <tool_call>{"name":"...","arguments":{...}}</tool_call>
          - Mistral: [TOOL_CALLS] [{"name":"...","arguments":{...}}]
          - Llama 3.1+: <|python_tag|>tool.call(arg="val") or {"name":"...","parameters":{...}}

        Returns list of tool call dicts or empty list.
        """
        import re

        # --- DeepSeek DSML ---
        if "DSML" in content:
            results = []
            invoke_re = re.compile(
                r'invoke\s+name="([^"]+)"[^>]*>(.*?)(?:</[^>]*invoke>|$)',
                re.DOTALL,
            )
            param_re = re.compile(
                r'parameter\s+name="([^"]+)"[^>]*>([^<]*)',
            )
            for m in invoke_re.finditer(content):
                tool_name = m.group(1)
                body = m.group(2)
                args = {}
                for pm in param_re.finditer(body):
                    key = pm.group(1)
                    value = pm.group(2).strip()
                    if value.isdigit():
                        args[key] = int(value)
                    elif value.lower() in ("true", "false"):
                        args[key] = value.lower() == "true"
                    else:
                        args[key] = value
                if tool_name:
                    results.append({
                        "id": f"dsml_{tool_name}_{len(results)}",
                        "name": tool_name,
                        "args": args,
                    })
            if results:
                return results

        # --- Qwen / Hermes: <tool_call>{"name":...}</tool_call> ---
        if "<tool_call>" in content:
            results = []
            # Match JSON blocks between <tool_call> tags (or unclosed at end)
            tc_re = re.compile(
                r'<tool_call>\s*(\{.*?\})\s*(?:</tool_call>|$)',
                re.DOTALL,
            )
            for m in tc_re.finditer(content):
                try:
                    obj = json.loads(m.group(1))
                    name = obj.get("name") or obj.get("function", "")
                    args = obj.get("arguments") or obj.get("parameters") or {}
                    if isinstance(args, str):
                        args = json.loads(args)
                    if name:
                        results.append({
                            "id": f"qwen_{name}_{len(results)}",
                            "name": name,
                            "args": args,
                        })
                except (json.JSONDecodeError, ValueError):
                    continue
            if results:
                return results

        # --- Llama 3.1+: <|python_tag|> or raw JSON tool calls ---
        if "<|python_tag|>" in content:
            results = []
            tag = "<|python_tag|>"
            idx = content.find(tag)
            body = content[idx + len(tag):].strip()
            # Format 1: tool_name.call(key="val", key2=val2)
            call_re = re.compile(
                r'(\w+)\.call\(([^)]*)\)',
            )
            for m in call_re.finditer(body):
                tool_name = m.group(1)
                args_str = m.group(2).strip()
                args = {}
                if args_str:
                    # Parse key=value pairs: key="val", key2=123, key3=True
                    kv_re = re.compile(r'(\w+)\s*=\s*("(?:[^"\\]|\\.)*"|[^,]+)')
                    for kv in kv_re.finditer(args_str):
                        key = kv.group(1)
                        val = kv.group(2).strip()
                        if val.startswith('"') and val.endswith('"'):
                            val = val[1:-1].replace('\\"', '"')
                            args[key] = val
                        elif val.isdigit():
                            args[key] = int(val)
                        elif val.lower() in ("true", "false"):
                            args[key] = val.lower() == "true"
                        else:
                            args[key] = val
                if tool_name:
                    results.append({
                        "id": f"llama_{tool_name}_{len(results)}",
                        "name": tool_name,
                        "args": args,
                    })
            # Format 2: JSON object(s) after the tag
            if not results:
                # Try parsing each line as JSON (one call per line)
                candidates = body.strip().splitlines()
                for candidate in candidates:
                    candidate = candidate.strip()
                    if not candidate.startswith("{"):
                        continue
                    try:
                        obj = json.loads(candidate)
                        name = obj.get("name", "")
                        args = obj.get("parameters") or obj.get("arguments") or {}
                        if isinstance(args, str):
                            args = json.loads(args)
                        if name:
                            results.append({
                                "id": f"llama_{name}_{len(results)}",
                                "name": name,
                                "args": args,
                            })
                    except (json.JSONDecodeError, ValueError):
                        continue
            if results:
                return results

        # --- Mistral: [TOOL_CALLS] [{...}, ...] ---
        if "[TOOL_CALLS]" in content:
            marker = content.find("[TOOL_CALLS]")
            rest = content[marker + len("[TOOL_CALLS]"):].strip()
            # Find the JSON array
            try:
                calls = json.loads(rest) if rest.startswith("[") else None
            except json.JSONDecodeError:
                # Try to find array boundaries
                bracket_start = rest.find("[")
                if bracket_start >= 0:
                    try:
                        calls = json.loads(rest[bracket_start:])
                    except json.JSONDecodeError:
                        calls = None
                else:
                    calls = None
            if calls and isinstance(calls, list):
                results = []
                for obj in calls:
                    name = obj.get("name", "")
                    args = obj.get("arguments") or obj.get("parameters") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (json.JSONDecodeError, ValueError):
                            args = {}
                    if name:
                        results.append({
                            "id": f"mistral_{name}_{len(results)}",
                            "name": name,
                            "args": args,
                        })
                if results:
                    return results

        return []

    @staticmethod
    def _validate_native_calls(calls: list, tools: list) -> list:
        """Filter parsed native tool calls — keep only those whose name
        matches an actually available tool.  Returns empty list if none match,
        which prevents false positives when models mention format markers in text."""
        if not calls:
            return []
        tool_names = set()
        for t in tools:
            name = t.get("function", {}).get("name", "")
            if name:
                tool_names.add(name)
        valid = [c for c in calls if c["name"] in tool_names]
        return valid

    @staticmethod
    def _strip_native_markers(content: str) -> str:
        """Strip native tool-call markers from content, keep text before them."""
        import re
        # Remove everything from the first marker to end
        # For DSML, the tag starts with '<' before the Unicode chars
        for marker in ("<tool_call>", "[TOOL_CALLS]", "<|python_tag|>"):
            idx = content.find(marker)
            if idx >= 0:
                content = content[:idx].strip()
                break
        else:
            # DSML: find the '<' that starts the DSML tag block
            idx = content.find("DSML")
            if idx >= 0:
                # Walk back to find the opening '<'
                start = content.rfind("<", 0, idx)
                if start >= 0:
                    content = content[:start].strip()
                else:
                    content = content[:idx].strip()
        # Clean up any remaining closing tags
        content = re.sub(r'</tool_call>', '', content).strip()
        return content or None

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
             temperature: float = 0.7, max_tokens: int = 8192) -> dict:
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
                content = msg.get("content")
                tool_calls = self._parse_tool_calls(msg["tool_calls"]) if msg.get("tool_calls") else None

                # Native format fallback: some models output tool calls
                # in their native format instead of OpenAI function calling
                if not tool_calls and content and tools:
                    native_calls = self._validate_native_calls(
                        self._parse_native_tool_calls(content), tools,
                    )
                    if native_calls:
                        tool_calls = native_calls
                        content = self._strip_native_markers(content)
                        msg = {"role": "assistant", "content": content}
                        msg["tool_calls"] = [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": json.dumps(tc["args"]),
                                },
                            }
                            for tc in tool_calls
                        ]

                return {
                    "content": content,
                    "tool_calls": tool_calls,
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
        """Dump failed request summary to /tmp for debugging.
        Only metadata is saved — no full message content (may contain secrets)."""
        from pathlib import Path
        dump_dir = Path("/tmp/emelya_debug")
        dump_dir.mkdir(exist_ok=True, mode=0o700)
        ts = time.strftime("%H%M%S")
        dump_path = dump_dir / f"request_{ts}.json"
        try:
            # Rotate — keep max 20 files
            existing = sorted(dump_dir.glob("request_*.json"))
            for old in existing[:-19]:
                old.unlink(missing_ok=True)

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
                summary["messages"].append(entry)
            dump_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def stream_chat(self, messages: list, tools: list = None,
                    temperature: float = 0.7, max_tokens: int = 8192,
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

                # Native format fallback for streaming
                if not parsed_tc and content and tools:
                    native_calls = self._validate_native_calls(
                        self._parse_native_tool_calls(content), tools,
                    )
                    if native_calls:
                        parsed_tc = native_calls
                        content = self._strip_native_markers(content)

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
