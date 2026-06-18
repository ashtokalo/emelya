"""Tests for line_editor — completion, paste markers, key functions."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from emelya.line_editor import (
    LineEditor, strip_markers, MARKER_START, MARKER_END,
    _find_region,
)


class TestStripMarkers(unittest.TestCase):

    def test_strip_empty(self):
        self.assertEqual(strip_markers(""), "")

    def test_strip_no_markers(self):
        self.assertEqual(strip_markers("hello world"), "hello world")

    def test_strip_markers(self):
        text = f"before{MARKER_START}inside{MARKER_END}after"
        result = strip_markers(text)
        self.assertEqual(result, "beforeinsideafter")

    def test_strip_multiple(self):
        text = f"a{MARKER_START}b{MARKER_END}c{MARKER_START}d{MARKER_END}e"
        self.assertEqual(strip_markers(text), "abcde")


class TestFindRegion(unittest.TestCase):

    def test_no_region(self):
        buf = list("hello")
        self.assertIsNone(_find_region(buf, 2))

    def test_inside_region(self):
        buf = ["a", MARKER_START, "b", "c", MARKER_END, "d"]
        region = _find_region(buf, 2)  # cursor at 'b' inside region
        self.assertIsNotNone(region)
        self.assertEqual(region, (1, 5))  # start, end

    def test_outside_region(self):
        buf = ["a", MARKER_START, "b", MARKER_END, "c"]
        self.assertIsNone(_find_region(buf, 0))  # cursor at 'a'
        self.assertIsNone(_find_region(buf, 4))  # cursor at 'c'


class TestVisibleLen(unittest.TestCase):

    def test_plain(self):
        self.assertEqual(LineEditor._visible_len("hello"), 5)

    def test_with_ansi(self):
        # ANSI codes should be stripped for length calculation
        s = "\033[31mred\033[0m"
        self.assertEqual(LineEditor._visible_len(s), 3)


class TestCommandCompletion(unittest.TestCase):

    def setUp(self):
        self.editor = LineEditor(work_dir=Path("."))

    def test_find_cmd_query_agent(self):
        result = LineEditor._find_cmd_query("/agent expl", 11, list("/agent expl"))
        self.assertIsNotNone(result)
        cmd, start, query = result
        self.assertEqual(cmd, "/agent")
        self.assertEqual(query, "expl")

    def test_find_cmd_query_no_match(self):
        result = LineEditor._find_cmd_query("hello world", 11, list("hello world"))
        self.assertIsNone(result)

    def test_find_cmd_query_mcp_multilevel(self):
        result = LineEditor._find_cmd_query("/mcp tools sh", 13, list("/mcp tools sh"))
        self.assertIsNotNone(result)
        cmd, start, query = result
        self.assertEqual(cmd, "/mcp")
        self.assertEqual(query, "tools sh")
        # start should point to "sh" (last word)
        self.assertEqual(start, 11)

    def test_mcp_subcmd_completion(self):
        items = self.editor._complete_mcp("", "")
        self.assertIn("tools", items)
        self.assertIn("call", items)
        self.assertIn("add", items)

    def test_mcp_subcmd_prefix(self):
        items = self.editor._complete_mcp("to", "to")
        self.assertEqual(items, ["tools"])

    def test_command_completer_agents(self):
        # Should return agent names from agents/ directory
        items = self.editor._command_completer("/agent", "")
        # We know agents/ exists in the project
        self.assertIsInstance(items, list)

    def test_command_completer_skills(self):
        items = self.editor._command_completer("/skill", "")
        self.assertIsInstance(items, list)


class TestAtQuery(unittest.TestCase):

    def test_find_at_query(self):
        buf = list("text @src/re")
        pos, query = LineEditor._find_at_query(buf, 12)
        self.assertEqual(pos, 5)  # position of @
        self.assertEqual(query, "src/re")

    def test_no_at(self):
        buf = list("no at here")
        pos, query = LineEditor._find_at_query(buf, 10)
        self.assertEqual(pos, -1)


if __name__ == "__main__":
    unittest.main()
