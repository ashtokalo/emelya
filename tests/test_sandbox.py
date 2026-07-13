"""Tests for sandbox.py — path validation and suggest_path."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from emelya.sandbox import Sandbox


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


class TestSandboxExtraAllowed(unittest.TestCase):

    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp())
        # Use a non-/tmp path so extra_allowed is the only reason it'd be accessible.
        # We point to an existing directory that is not under /tmp: /var/cache or similar.
        # For portability, we just test _allowed contents and /etc rejection.
        self.extra_path = Path("/var/cache")  # exists on Linux, outside /tmp and workdir
        self.sandbox = Sandbox(self.workdir, extra_allowed=[self.extra_path])

    def test_extra_allowed_added_to_internal_list(self):
        self.assertIn(str(self.extra_path.resolve()), self.sandbox._allowed)

    def test_extra_allowed_permits_file_inside(self):
        # /var/cache itself resolves as an allowed prefix
        resolved = self.sandbox.validate(str(self.extra_path))
        self.assertEqual(resolved, self.extra_path.resolve())

    def test_extra_allowed_does_not_open_unrelated(self):
        # /etc is not in extra_allowed, must still be rejected
        with self.assertRaises(PermissionError):
            self.sandbox.validate("/etc/passwd")

    def test_no_extra_allowed_rejects_outside_workdir(self):
        sandbox = Sandbox(self.workdir)
        with self.assertRaises(PermissionError):
            sandbox.validate("/etc/passwd")


if __name__ == "__main__":
    unittest.main()
