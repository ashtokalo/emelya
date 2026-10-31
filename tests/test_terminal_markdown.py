"""Tests for terminal Markdown table rendering."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from emelya.terminal_markdown import render_terminal_markdown, TerminalMarkdownStream


TABLE = """| Месяц | Ясных дней (оценка) |
|---|---|
| Январь | 3 |
| **Год** | **~76** |"""


class TestTerminalMarkdown(unittest.TestCase):

    def test_renders_markdown_table(self):
        rendered = render_terminal_markdown(TABLE, width=60)
        self.assertIn("┌", rendered)
        self.assertIn("Месяц", rendered)
        self.assertIn("Год", rendered)
        self.assertNotIn("|---|", rendered)
        self.assertNotIn("**", rendered)

    def test_wraps_to_terminal_width(self):
        rendered = render_terminal_markdown(TABLE, width=30)
        self.assertLessEqual(max(map(len, rendered.splitlines())), 30)

    def test_keeps_invalid_table_unchanged(self):
        text = "| not | a table |\nordinary text"
        self.assertEqual(render_terminal_markdown(text, 40), text)

    def test_keeps_tables_inside_code_fences(self):
        text = "```\n" + TABLE + "\n```"
        self.assertEqual(render_terminal_markdown(text, 60), text)

    def test_stream_renders_table_across_chunks(self):
        chunks = []
        stream = TerminalMarkdownStream(chunks.append, width=40)
        for part in ("До таблицы\n| Ме", "сяц | Дни |\n|---|---|\n", "| Май | 6 |"):
            stream.feed(part)
        stream.finish()
        rendered = "".join(chunks)
        self.assertIn("До таблицы", rendered)
        self.assertIn("┌", rendered)
        self.assertNotIn("|---|", rendered)


if __name__ == "__main__":
    unittest.main()
