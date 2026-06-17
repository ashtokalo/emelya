"""Tests for sandbox.py — path validation and suggest_path."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from sandbox import Sandbox


class TestSandbox(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.sandbox = Sandbox(self.tmpdir)
        # Create test structure
        (self.tmpdir / "src").mkdir()
        (self.tmpdir / "src" / "repl.py").touch()
        (self.tmpdir / "src" / "tools").mkdir()
        (self.tmpdir / "src" / "tools" / "read.py").touch()
        (self.tmpdir / "test.txt").touch()

    def test_validate_relative(self):
        path = self.sandbox.validate("test.txt")
        self.assertEqual(path, self.tmpdir / "test.txt")

    def test_validate_nested(self):
        path = self.sandbox.validate("src/repl.py")
        self.assertEqual(path, self.tmpdir / "src" / "repl.py")

    def test_validate_tmp(self):
        path = self.sandbox.validate("/tmp/test")
        self.assertEqual(str(path), "/tmp/test")

    def test_reject_traversal(self):
        with self.assertRaises(PermissionError):
            self.sandbox.validate("../../etc/passwd")

    def test_reject_absolute(self):
        with self.assertRaises(PermissionError):
            self.sandbox.validate("/etc/passwd")

    def test_reject_empty(self):
        with self.assertRaises(PermissionError):
            self.sandbox.validate("")

    def test_suggest_path_removes_extra_dir(self):
        # src/aigent/repl.py → src/repl.py
        hint = self.sandbox.suggest_path("src/aigent/repl.py")
        self.assertEqual(hint, "src/repl.py")

    def test_suggest_path_nested(self):
        # src/tools/aigent/read.py → src/tools/read.py
        hint = self.sandbox.suggest_path("src/tools/aigent/read.py")
        self.assertEqual(hint, "src/tools/read.py")

    def test_suggest_path_no_match(self):
        hint = self.sandbox.suggest_path("totally/wrong/zzz_nonexistent.py")
        self.assertEqual(hint, "")

    def test_suggest_path_rglob(self):
        # app/controllers/read.py → should find src/tools/read.py via rglob
        hint = self.sandbox.suggest_path("app/controllers/read.py")
        self.assertIn("src/tools/read.py", hint)


if __name__ == "__main__":
    unittest.main()
