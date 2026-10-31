"""Tests for web_fetch and web_search tools."""

import json
import sys
import unittest
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from emelya.sandbox import Sandbox
from emelya.tools import ToolContext


def make_ctx():
    import tempfile
    tmpdir = Path(tempfile.mkdtemp())
    return ToolContext(work_dir=tmpdir, sandbox=Sandbox(tmpdir))


class _MockWebHandler(BaseHTTPRequestHandler):
    """Mock web server for testing web_fetch."""

    def do_GET(self, *args):
        if self.path == "/text":
            self._respond(200, "text/plain", "Hello plain text")
        elif self.path == "/html":
            self._respond(200, "text/html", "<html><body><h1>Title</h1><p>Content</p></body></html>")
        elif self.path == "/json":
            self._respond(200, "application/json", '{"key": "value", "n": 42}')
        elif self.path == "/big":
            self._respond(200, "text/plain", "x" * 20000)
        elif self.path == "/error":
            self._respond(500, "text/plain", "Internal error")
        else:
            self._respond(404, "text/plain", "Not found")

    def _respond(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


class TestWebFetch(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _MockWebHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"
        # Patch SSRF check — our test server is on localhost
        import emelya.tools.web_fetch as wf
        cls._orig_is_private = wf._is_private_url
        wf._is_private_url = lambda url: False

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        import emelya.tools.web_fetch as wf
        wf._is_private_url = cls._orig_is_private

    def test_fetch_plain_text(self):
        from emelya.tools.web_fetch import execute
        result = execute({"url": f"{self.base}/text"}, make_ctx())
        self.assertIn("Hello plain text", result)

    def test_fetch_html_extracts_text(self):
        from emelya.tools.web_fetch import execute
        result = execute({"url": f"{self.base}/html"}, make_ctx())
        self.assertIn("Title", result)
        self.assertIn("Content", result)
        self.assertNotIn("<html>", result)

    def test_fetch_json_pretty(self):
        from emelya.tools.web_fetch import execute
        result = execute({"url": f"{self.base}/json"}, make_ctx())
        self.assertIn('"key"', result)
        self.assertIn("42", result)

    def test_fetch_truncates(self):
        from emelya.tools.web_fetch import execute
        result = execute({"url": f"{self.base}/big", "max_length": 100}, make_ctx())
        self.assertIn("truncated", result)

    def test_fetch_error(self):
        from emelya.tools.web_fetch import execute
        result = execute({"url": f"{self.base}/error"}, make_ctx())
        self.assertIn("Error", result)
        self.assertIn("500", result)

    def test_fetch_no_url(self):
        from emelya.tools.web_fetch import execute
        result = execute({}, make_ctx())
        self.assertIn("Error", result)

    def test_fetch_bad_scheme(self):
        from emelya.tools.web_fetch import execute
        result = execute({"url": "ftp://example.com"}, make_ctx())
        self.assertIn("Error", result)

    def test_fetch_encodes_unicode_url(self):
        from emelya.tools.web_fetch import execute
        result = execute({"url": f"{self.base}/тест"}, make_ctx())
        self.assertNotIn("ascii codec", result)



class TestSSRFProtection(unittest.TestCase):
    """Test SSRF filtering — separate class so the patch doesn't interfere."""

    def test_loopback_blocked(self):
        from emelya.tools.web_fetch import _is_private_url
        self.assertTrue(_is_private_url("http://127.0.0.1/"))

    def test_rfc1918_blocked(self):
        from emelya.tools.web_fetch import _is_private_url
        self.assertTrue(_is_private_url("http://10.0.0.1/"))

    def test_link_local_blocked(self):
        from emelya.tools.web_fetch import _is_private_url
        self.assertTrue(_is_private_url("http://169.254.169.254/"))


class TestWebSearchDefinition(unittest.TestCase):
    """Test web_search tool definition (can't test actual DuckDuckGo easily)."""

    def test_definition_valid(self):
        from emelya.tools.web_search import DEFINITION
        fn = DEFINITION["function"]
        self.assertEqual(fn["name"], "web_search")
        self.assertIn("query", fn["parameters"]["required"])

    def test_execute_empty_query(self):
        from emelya.tools.web_search import execute
        result = execute({"query": ""}, make_ctx())
        self.assertIn("Error", result)


if __name__ == "__main__":
    unittest.main()
