"""Tests for LLM client — HTTP communication with mock server."""

import json
import sys
import unittest
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from emelya.llm import LLMClient, LLMError


class _MockHandler(BaseHTTPRequestHandler):
    """Mock OpenAI-compatible API server."""

    # Class-level response config — set before each test
    response_body = None
    response_code = 200
    requests_log = []

    def do_POST(self, *args):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        _MockHandler.requests_log.append({
            "path": self.path,
            "body": body,
            "headers": dict(self.headers),
        })

        self.send_response(_MockHandler.response_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        resp = _MockHandler.response_body or self._default_response(body)
        self.wfile.write(json.dumps(resp).encode())

    def do_GET(self, *args):
        if self.path == "/v1/models":
            resp = {"data": [{"id": "test-model"}]}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass  # suppress output

    @staticmethod
    def _default_response(request):
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Hello from mock!",
                }
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "test-model",
        }


class TestLLMClient(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _MockHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _MockHandler.response_body = None
        _MockHandler.response_code = 200
        _MockHandler.requests_log.clear()
        self.client = LLMClient(self.base_url, model="test")

    def test_check_connection(self):
        ok, info = self.client.check_connection()
        self.assertTrue(ok)
        self.assertIn("test-model", info)

    def test_chat_basic(self):
        result = self.client.chat(
            messages=[{"role": "user", "content": "hi"}],
        )
        self.assertEqual(result["content"], "Hello from mock!")
        self.assertIsNone(result["tool_calls"])
        self.assertEqual(result["usage"]["total_tokens"], 15)
        self.assertEqual(result["model_id"], "test-model")

    def test_chat_with_tools(self):
        _MockHandler.response_body = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "grep",
                            "arguments": '{"pattern": "test"}',
                        },
                    }],
                }
            }],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }
        tools = [{"type": "function", "function": {"name": "grep", "parameters": {}}}]
        result = self.client.chat(
            messages=[{"role": "user", "content": "search"}],
            tools=tools,
        )
        self.assertIsNotNone(result["tool_calls"])
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["name"], "grep")
        self.assertEqual(result["tool_calls"][0]["args"]["pattern"], "test")

    def test_chat_400_raises(self):
        _MockHandler.response_code = 400
        _MockHandler.response_body = {"error": "bad request"}
        with self.assertRaises(LLMError):
            self.client.chat(
                messages=[{"role": "user", "content": "hi"}],
            )

    def test_chat_sends_correct_payload(self):
        self.client.chat(
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.5,
            max_tokens=100,
        )
        self.assertEqual(len(_MockHandler.requests_log), 1)
        body = _MockHandler.requests_log[0]["body"]
        self.assertEqual(body["model"], "test")
        self.assertEqual(body["temperature"], 0.5)
        self.assertEqual(body["max_tokens"], 100)
        self.assertFalse(body["stream"])

    def test_api_key_header(self):
        client = LLMClient(self.base_url, model="test", api_key="sk-secret")
        client.chat(messages=[{"role": "user", "content": "hi"}])
        auth = _MockHandler.requests_log[0]["headers"].get("Authorization", "")
        self.assertEqual(auth, "Bearer sk-secret")

    def test_native_format_fallback(self):
        """If model returns tool calls in Qwen native format, they get parsed."""
        _MockHandler.response_body = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": 'Let me search.\n<tool_call>\n{"name": "grep", "arguments": {"pattern": "test"}}\n</tool_call>',
                }
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }
        tools = [{"type": "function", "function": {"name": "grep", "parameters": {}}}]
        result = self.client.chat(
            messages=[{"role": "user", "content": "search"}],
            tools=tools,
        )
        self.assertIsNotNone(result["tool_calls"])
        self.assertEqual(result["tool_calls"][0]["name"], "grep")
        # Content should be stripped of tool_call markers
        self.assertEqual(result["content"], "Let me search.")

    def test_native_format_stripped_but_no_tool_calls_without_tools(self):
        """Native markers stripped from content, but tool_calls stays None when tools=None."""
        _MockHandler.response_body = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": 'Some text\n<tool_call>{"name": "grep", "arguments": {}}</tool_call>',
                }
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        result = self.client.chat(
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
        )
        # tool_calls should remain None (tools not provided)
        self.assertIsNone(result["tool_calls"])
        # But markers should be stripped from content
        self.assertNotIn("<tool_call>", result["content"] or "")
        self.assertEqual(result["content"], "Some text")

    def test_json_repair_in_tool_args(self):
        """Truncated JSON in tool arguments should be repaired."""
        _MockHandler.response_body = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read",
                            "arguments": '{"path": "test.py"',  # missing closing brace
                        },
                    }],
                }
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        result = self.client.chat(
            messages=[{"role": "user", "content": "read"}],
        )
        self.assertEqual(result["tool_calls"][0]["args"]["path"], "test.py")


if __name__ == "__main__":
    unittest.main()
