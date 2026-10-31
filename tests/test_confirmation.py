"""Tests for model-facing confirmation denial feedback."""

import sys
import unittest
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from emelya.repl import REPL
from emelya.sandbox import Sandbox
from emelya.tools import ToolContext
from emelya.tools.bash import execute as bash_execute


def make_repl():
    repl = REPL.__new__(REPL)
    repl.config = SimpleNamespace(confirm_mode="confirm_all")
    repl._last_cancellation_comment = ""
    repl._session_command_permissions = set()
    return repl


class TestConfirmationFeedback(unittest.TestCase):

    def test_confirmation_rings_terminal_bell(self):
        repl = make_repl()

        class Terminal(io.StringIO):
            def isatty(self):
                return True

        output = Terminal()
        with patch("sys.stdout", output), patch("builtins.input", return_value="н"):
            self.assertFalse(repl._confirm("test"))
        self.assertIn("\a", output.getvalue())

    def test_confirmation_does_not_ring_when_redirected(self):
        repl = make_repl()
        output = io.StringIO()
        with patch("sys.stdout", output), patch("builtins.input", return_value="н"):
            self.assertFalse(repl._confirm("test"))
        self.assertNotIn("\a", output.getvalue())

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

    def test_allow_similar_bash_commands_for_session(self):
        repl = make_repl()
        with patch("builtins.input", return_value="в") as ask:
            self.assertTrue(repl._confirm("bash: remove generated file", "$ rm one.tmp"))
            self.assertTrue(repl._confirm("bash: remove another file", "$ rm two.tmp"))
        ask.assert_called_once()

    def test_session_permission_is_scoped_to_command_family(self):
        repl = make_repl()
        with patch("builtins.input", side_effect=["в", "н"]) as ask:
            self.assertTrue(repl._confirm("bash: run tests", "$ npm test"))
            self.assertFalse(repl._confirm("bash: install package", "$ npm install foo"))
        self.assertEqual(ask.call_count, 2)

    def test_compound_command_permission_requires_exact_match(self):
        repl = make_repl()
        first = "$ cd /tmp; for f in a; do grep x $f; done"
        changed = "$ cd /tmp; for f in a; do rm $f; done"
        with patch("builtins.input", side_effect=["в", "н"]) as ask:
            self.assertTrue(repl._confirm("bash: inspect logs", first))
            self.assertTrue(repl._confirm("bash: inspect logs again", first))
            self.assertFalse(repl._confirm("bash: remove files", changed))
        self.assertEqual(ask.call_count, 2)

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

    def test_bash_rejects_path_probe_before_confirmation(self):
        confirm = Mock(return_value=True)
        root = Path("/tmp/project")
        ctx = ToolContext(
            work_dir=root,
            sandbox=Sandbox(root),
            confirm=confirm,
            config=SimpleNamespace(confirm_mode="confirm_all"),
        )
        result = bash_execute({
            "command": "cd /wrong; cd /also-wrong; git status",
        }, ctx)
        self.assertIn("command rejected", result)
        confirm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
