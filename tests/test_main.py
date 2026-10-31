"""Tests for top-level CLI process behavior."""

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from emelya.__main__ import main


class TestMain(unittest.TestCase):

    def test_keyboard_interrupt_exits_quietly_with_130(self):
        stderr = io.StringIO()
        with patch("emelya.cli.dispatch", side_effect=KeyboardInterrupt), \
                patch.object(sys, "argv", ["emelya", "mcp", "call"]), \
                contextlib.redirect_stderr(stderr):
            self.assertEqual(main(), 130)
        self.assertIn("interrupted", stderr.getvalue().lower())
        self.assertNotIn("traceback", stderr.getvalue().lower())

    def test_handled_cli_command_exits_zero(self):
        with patch("emelya.cli.dispatch", return_value=True), \
                patch.object(sys, "argv", ["emelya", "help"]):
            self.assertEqual(main(), 0)


if __name__ == "__main__":
    unittest.main()
