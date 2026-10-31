"""Tests for model-facing confirmation denial feedback."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from emelya.repl import REPL
from emelya.sandbox import Sandbox
from emelya.tools import ToolContext
from emelya.tools.bash import execute as bash_execute


def make_repl():
    repl = REPL.__new__(REPL)
    repl.config = SimpleNamespace(confirm_mode="confirm_all")
    repl._last_cancellation_comment = ""
    return repl


class TestConfirmationFeedback(unittest.TestCase):

    def test_plain_deny_returns_explicit_error(self):
        repl = make_repl()
        with patch("builtins.input", return_value="н"):
            self.assertFalse(repl._confirm("test"))
        self.assertEqual(
            repl._get_cancellation_result(),
            "Error: operation cancelled by user. "
            "The command was NOT executed and no changes were made.",
        )

    def test_deny_with_comment_is_forwarded_to_model(self):
        repl = make_repl()
        with patch("builtins.input", side_effect=["к", "используй edit, не bash"]):
            self.assertFalse(repl._confirm("test"))
        result = repl._get_cancellation_result()
        self.assertIn("no changes were made", result)
        self.assertIn("User comment: используй edit, не bash", result)

    def test_allow_executes_normally(self):
        repl = make_repl()
        with patch("builtins.input", return_value="д"):
            self.assertTrue(repl._confirm("test"))

    def test_bash_uses_context_cancellation_result(self):
        root = Path("/tmp")
        ctx = ToolContext(
            work_dir=root,
            sandbox=Sandbox(root),
            confirm=lambda *args: False,
            get_cancellation_result=lambda: "Error: denied with feedback",
            config=SimpleNamespace(confirm_mode="confirm_all"),
        )
        result = bash_execute({"command": "touch should-not-exist"}, ctx)
        self.assertEqual(result, "Error: denied with feedback")


if __name__ == "__main__":
    unittest.main()
