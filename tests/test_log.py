"""Tests for log.py — SessionLog and UsageTracker."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from emelya.log import SessionLog, UsageTracker


class TestSessionLog(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.log_path = self.tmpdir / "session" / "log.txt"
        self.log = SessionLog(self.log_path)

    def tearDown(self):
        self.log.close()

    def test_creates_directories(self):
        self.assertTrue(self.log_path.parent.is_dir())

    def test_session_start(self):
        self.log.session_start("http://localhost:8080", "default", "/tmp", "test_session")
        content = self.log_path.read_text()
        self.assertIn("test_session", content)
        self.assertIn("localhost:8080", content)

    def test_event(self):
        self.log.event("Something happened")
        content = self.log_path.read_text()
        self.assertIn("Something happened", content)

    def test_request_response(self):
        self.log.request(
            "http://localhost/v1/chat/completions",
            [{"role": "user", "content": "hello"}],
        )
        self.log.response(
            "world",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            model="test-model",
        )
        content = self.log_path.read_text()
        self.assertIn("Request #1", content)
        self.assertIn("USAGE", content)
        self.assertIn("test-model", content)

    def test_tool_exec(self):
        self.log.tool_exec("grep", {"pattern": "test"}, "match found", 42)
        content = self.log_path.read_text()
        self.assertIn("grep", content)
        self.assertIn("42ms", content)
        self.assertIn("match found", content)

    def test_session_end(self):
        self.log.session_end()
        content = self.log_path.read_text()
        self.assertIn("Session ended", content)


class TestUsageTracker(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.usage_path = self.tmpdir / "usage.jsonl"
        self.tracker = UsageTracker(self.usage_path)

    def tearDown(self):
        self.tracker.close()

    def test_record_creates_file(self):
        self.tracker.record("test-model", 100, 50, 150, "session1")
        self.assertTrue(self.usage_path.exists())

    def test_record_writes_jsonl(self):
        self.tracker.record("model-a", 100, 50, 150, "s1")
        self.tracker.record("model-b", 200, 100, 300, "s1")
        lines = self.usage_path.read_text().strip().split("\n")
        self.assertEqual(len(lines), 2)
        entry = json.loads(lines[0])
        self.assertEqual(entry["model"], "model-a")
        self.assertEqual(entry["total"], 150)

    def test_load_stats(self):
        self.tracker.record("model-a", 100, 50, 150, "s1")
        self.tracker.record("model-a", 200, 100, 300, "s1")
        self.tracker.record("model-b", 50, 25, 75, "s1")
        self.tracker.close()

        stats = UsageTracker.load_stats(self.usage_path)
        self.assertEqual(stats["totals"]["requests"], 3)
        self.assertEqual(stats["totals"]["total"], 525)
        self.assertIn("model-a", stats["by_model"])
        self.assertEqual(stats["by_model"]["model-a"]["requests"], 2)

    def test_load_stats_empty(self):
        stats = UsageTracker.load_stats(self.tmpdir / "nonexistent.jsonl")
        self.assertEqual(stats["totals"]["requests"], 0)


if __name__ == "__main__":
    unittest.main()
