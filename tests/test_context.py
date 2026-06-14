"""Tests for context.py — message management, trimming, save/load."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from context import Context


class TestContext(unittest.TestCase):

    def test_add_messages(self):
        ctx = Context()
        ctx.set_system("system prompt")
        ctx.add_user("hello")
        ctx.add_assistant_text("hi there")
        msgs = ctx.get_messages()
        self.assertEqual(len(msgs), 3)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[1]["role"], "user")
        self.assertEqual(msgs[2]["role"], "assistant")

    def test_clear(self):
        ctx = Context()
        ctx.set_system("sys")
        ctx.add_user("q")
        ctx.add_assistant_text("a")
        ctx.clear()
        msgs = ctx.get_messages()
        self.assertEqual(len(msgs), 1)  # system only
        self.assertEqual(msgs[0]["role"], "system")

    def test_pop_last_user(self):
        ctx = Context()
        ctx.add_user("orphan")
        ctx.pop_last_user()
        self.assertEqual(len(ctx.messages), 0)

    def test_pop_last_user_no_op(self):
        ctx = Context()
        ctx.add_assistant_text("not user")
        ctx.pop_last_user()
        self.assertEqual(len(ctx.messages), 1)

    def test_drop(self):
        ctx = Context()
        ctx.set_system("sys")
        ctx.add_user("q1")
        ctx.add_assistant_text("a1")
        ctx.add_user("q2")
        # Drop index 0 = system → blocked
        self.assertIn("Cannot", ctx.drop(0))
        # Drop index 2 (assistant a1)
        result = ctx.drop(2)
        self.assertIn("assistant", result)
        self.assertEqual(len(ctx.messages), 2)

    def test_estimate_tokens(self):
        ctx = Context()
        ctx.set_system("a" * 350)  # ~100 tokens
        tokens = ctx.estimate_tokens()
        self.assertGreater(tokens, 50)
        self.assertLess(tokens, 200)

    def test_update_usage_overrides_estimate(self):
        ctx = Context()
        ctx.set_system("short")
        ctx.update_usage({"prompt_tokens": 5000})
        self.assertEqual(ctx.estimate_tokens(), 5000)

    def test_save_load(self):
        ctx = Context(context_limit=4000)
        ctx.set_system("sys prompt")
        ctx.add_user("question")
        ctx.add_assistant_text("answer")

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = Path(f.name)

        ctx.save(path)

        ctx2 = Context()
        ctx2.load(path)
        self.assertEqual(ctx2.system_prompt, ctx.system_prompt)
        self.assertEqual(len(ctx2.messages), 2)
        self.assertEqual(ctx2.context_limit, 4000)
        path.unlink()

    def test_compact(self):
        ctx = Context()
        ctx.add_user("q1")
        ctx.add_assistant_text("a1")
        ctx.add_user("q2")
        ctx.add_assistant_text("a2")
        ctx.apply_compact("summary of conversation")
        self.assertEqual(len(ctx.messages), 2)
        self.assertIn("summary", ctx.messages[1]["content"])

    def test_trim(self):
        ctx = Context(context_limit=100)
        ctx.set_system("s" * 200)  # system alone ~57 tokens
        ctx.add_user("u" * 200)
        ctx.add_assistant_text("a" * 200)
        ctx.add_user("u2" * 200)
        ctx.add_assistant_text("a2" * 200)
        ctx.trim()
        # Should have removed oldest messages
        self.assertLess(len(ctx.messages), 4)


if __name__ == "__main__":
    unittest.main()
