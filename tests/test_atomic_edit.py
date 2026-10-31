"""Tests for atomic structured edit."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from emelya.sandbox import Sandbox
from emelya.tools import ToolContext, ToolRegistry
from emelya.tools.edit import execute


class TestAtomicEdit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ctx = ToolContext(self.root, Sandbox(self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def _write_read(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.ctx.files_read.add(str(target.resolve()))
        return target

    def test_update_add_delete_and_single_undo(self):
        first = self._write_read("first.py", "old\n")
        removed = self._write_read("removed.py", "gone\n")
        result = execute({"changes": [
            {"update": "first.py", "old_string": "old", "new_string": "new"},
            {"create": "added.py", "content": "added\n"},
            {"delete": "removed.py"},
        ]}, self.ctx)
        self.assertIn("3 files, 3 changes", result)
        self.assertEqual(first.read_text(), "new\n")
        self.assertEqual((self.root / "added.py").read_text(), "added\n")
        self.assertFalse(removed.exists())

        self.ctx.undo()
        self.assertEqual(first.read_text(), "old\n")
        self.assertFalse((self.root / "added.py").exists())
        self.assertEqual(removed.read_text(), "gone\n")

    def test_two_updates_same_file_are_sequential(self):
        target = self._write_read("a", "ONE\nTWO\n")
        result = execute({"changes": [
            {"update": "a", "old_string": "ONE", "new_string": "one"},
            {"update": "a", "old_string": "TWO", "new_string": "two"},
        ]}, self.ctx)
        self.assertIn("1 files, 2 changes", result)
        self.assertEqual(target.read_text(), "one\ntwo\n")

    def test_conflict_in_last_change_is_atomic(self):
        first = self._write_read("a", "one\n")
        second = self._write_read("b", "two\n")
        result = execute({"changes": [
            {"update": "a", "old_string": "one", "new_string": "ONE"},
            {"update": "b", "old_string": "missing", "new_string": "TWO"},
        ]}, self.ctx)
        self.assertTrue(result.startswith("Error:"), result)
        self.assertEqual(first.read_text(), "one\n")
        self.assertEqual(second.read_text(), "two\n")
        self.assertFalse(self.ctx.undo_stack)

    def test_update_and_delete_require_read(self):
        (self.root / "a").write_text("old")
        for operation in (
            {"update": "a", "old_string": "old", "new_string": "new"},
            {"delete": "a"},
        ):
            result = execute({"changes": [operation]}, self.ctx)
            self.assertIn("not read first", result)

    def test_ambiguous_match_rejected(self):
        target = self._write_read("a", "same\nsame\n")
        result = execute({"changes": [
            {"update": "a", "old_string": "same", "new_string": "new"},
        ]}, self.ctx)
        self.assertIn("matches 2 times", result)
        self.assertEqual(target.read_text(), "same\nsame\n")

    def test_rollback_after_second_replace_fails(self):
        first = self._write_read("a", "old-a\n")
        second = self._write_read("b", "old-b\n")
        real_replace = os.replace
        calls = {"count": 0}

        def fail_second(src, dst):
            if Path(src).name.startswith(".emelya-edit-"):
                calls["count"] += 1
                if calls["count"] == 2:
                    raise OSError("injected")
            return real_replace(src, dst)

        with patch("emelya.tools.edit.os.replace", side_effect=fail_second):
            result = execute({"changes": [
                {"update": "a", "old_string": "old-a", "new_string": "new-a"},
                {"update": "b", "old_string": "old-b", "new_string": "new-b"},
            ]}, self.ctx)
        self.assertTrue(result.startswith("Error:"), result)
        self.assertEqual(first.read_text(), "old-a\n")
        self.assertEqual(second.read_text(), "old-b\n")
        self.assertFalse(self.ctx.undo_stack)

    def test_plan_mode_blocks_edit(self):
        from emelya.tools.plan_mode import execute as plan_execute, reset

        registry = ToolRegistry()
        registry.load_builtin(Path(__file__).parent.parent / "src" / "emelya")
        plan_execute({"action": "enter"}, self.ctx)
        try:
            result = registry.execute("edit", {"changes": []}, self.ctx)
            self.assertIn("blocked in plan mode", result)
        finally:
            reset()

    def test_registry_has_no_patch_or_multi_edit(self):
        registry = ToolRegistry()
        registry.load_builtin(Path(__file__).parent.parent / "src" / "emelya")
        self.assertIn("edit", registry.names)
        self.assertNotIn("patch", registry.names)
        self.assertNotIn("multi_edit", registry.names)
        self.assertNotIn("write", registry.names)

    def test_write_existing_and_create_new_are_distinct(self):
        target = self._write_read("existing", "old")
        result = execute({"changes": [
            {"write": "existing", "content": "complete"},
            {"create": "new", "content": "created"},
        ]}, self.ctx)
        self.assertIn("2 files, 2 changes", result)
        self.assertEqual(target.read_text(), "complete")
        self.assertEqual((self.root / "new").read_text(), "created")

    def test_create_existing_and_write_missing_are_rejected(self):
        self._write_read("existing", "old")
        create_result = execute({"changes": [
            {"create": "existing", "content": "new"},
        ]}, self.ctx)
        write_result = execute({"changes": [
            {"write": "missing", "content": "new"},
        ]}, self.ctx)
        self.assertIn("create target already exists", create_result)
        self.assertIn("file not found", write_result)

    def test_exactly_one_operation_key_required(self):
        result = execute({"changes": [
            {"create": "a", "delete": "b", "content": "x"},
        ]}, self.ctx)
        self.assertIn("exactly one", result)

    def test_absolute_path_inside_workdir_is_allowed(self):
        target = self.root / "nested" / "created.txt"
        result = execute({"changes": [
            {"create": str(target), "content": "created"},
        ]}, self.ctx)
        self.assertIn("Applied edits", result)
        self.assertEqual(target.read_text(), "created")

    def test_absolute_path_outside_workdir_is_rejected(self):
        outside = Path("/etc/outside-emelya-edit.txt")
        result = execute({"changes": [
            {"create": str(outside), "content": "forbidden"},
        ]}, self.ctx)
        self.assertIn("outside working directory", result)
        self.assertFalse(outside.exists())

    def test_parent_segments_are_allowed_when_result_stays_inside(self):
        target = self.root / "created.txt"
        result = execute({"changes": [
            {"create": "nested/../created.txt", "content": "created"},
        ]}, self.ctx)
        self.assertIn("Applied edits", result)
        self.assertEqual(target.read_text(), "created")


if __name__ == "__main__":
    unittest.main()
