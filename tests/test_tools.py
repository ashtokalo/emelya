"""Tests for tools — registry, read, edit, glob, grep, list_dir."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from emelya.sandbox import Sandbox
from emelya.tools import ToolContext, ToolRegistry


def make_ctx(tmpdir: Path) -> ToolContext:
    return ToolContext(
        work_dir=tmpdir,
        sandbox=Sandbox(tmpdir),
        confirm=lambda *a: True,
    )


class TestToolRegistry(unittest.TestCase):

    def test_load_builtin(self):
        r = ToolRegistry()
        r.load_builtin(Path(__file__).parent.parent / "src" / "emelya")
        self.assertIn("read", r.names)
        self.assertNotIn("write", r.names)
        self.assertIn("edit", r.names)
        self.assertIn("bash", r.names)
        self.assertIn("glob", r.names)
        self.assertIn("grep", r.names)

    def test_extensions_separate(self):
        r = ToolRegistry()
        r.load_builtin(Path(__file__).parent.parent / "src" / "emelya")
        # doc_read should be in extensions, not core
        self.assertNotIn("doc_read", r.names)
        ext = r.get_extension_summary()
        self.assertIn("documents", ext)
        self.assertIn("doc_read", ext["documents"])

    def test_load_category(self):
        r = ToolRegistry()
        r.load_builtin(Path(__file__).parent.parent / "src" / "emelya")
        result = r.load_category("documents")
        self.assertIn("doc_read", result)
        self.assertIn("doc_read", r.names)

    def test_required_validation(self):
        r = ToolRegistry()
        r.load_builtin(Path(__file__).parent.parent / "src" / "emelya")
        ctx = make_ctx(Path(tempfile.mkdtemp()))
        # Call edit without required params
        result = r.execute("edit", {}, ctx)
        self.assertIn("missing required", result)

    def test_unknown_tool(self):
        r = ToolRegistry()
        ctx = make_ctx(Path(tempfile.mkdtemp()))
        result = r.execute("nonexistent", {}, ctx)
        self.assertIn("unknown tool", result)

    def test_one_time_external_access_scope_ends_after_tool(self):
        requests = []
        tmpdir = Path(tempfile.mkdtemp())

        def request(path):
            requests.append(path)
            return "once" if len(requests) == 1 else "deny"

        sandbox = Sandbox(tmpdir, request_access=request)
        ctx = ToolContext(work_dir=tmpdir, sandbox=sandbox)
        registry = ToolRegistry()
        definition = {"type": "function", "function": {
            "name": "outside", "parameters": {"type": "object", "properties": {}}}}

        def execute_twice(args, tool_ctx):
            tool_ctx.sandbox.validate("/etc/passwd")
            tool_ctx.sandbox.validate("/etc/passwd")
            return "ok"

        registry.register("outside", definition, execute_twice)
        self.assertEqual(registry.execute("outside", {}, ctx), "ok")
        self.assertIn("Access denied", registry.execute("outside", {}, ctx))
        self.assertEqual(len(requests), 2)


class TestReadTool(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.ctx = make_ctx(self.tmpdir)
        (self.tmpdir / "test.txt").write_text("line1\nline2\nline3\n")
        (self.tmpdir / "binary.bin").write_bytes(b"\x00\x01\x02\x03")

    def test_read_file(self):
        from emelya.tools.read import execute
        result = execute({"path": "test.txt"}, self.ctx)
        self.assertIn("line1", result)
        self.assertIn("line2", result)
        self.assertIn("[Lines 1-3 of 3]", result)

    def test_read_with_offset(self):
        from emelya.tools.read import execute
        result = execute({"path": "test.txt", "offset": 2, "limit": 1}, self.ctx)
        self.assertIn("line2", result)
        self.assertNotIn("line1", result)

    def test_read_binary_rejected(self):
        from emelya.tools.read import execute
        result = execute({"path": "binary.bin"}, self.ctx)
        self.assertIn("binary", result.lower())

    def test_read_not_found(self):
        from emelya.tools.read import execute
        result = execute({"path": "nope.txt"}, self.ctx)
        self.assertIn("not found", result)

    def test_read_tracks_files(self):
        from emelya.tools.read import execute
        execute({"path": "test.txt"}, self.ctx)
        self.assertTrue(any("test.txt" in f for f in self.ctx.files_read))


class TestEditWriteOperations(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.ctx = make_ctx(self.tmpdir)

    def test_create_new_file(self):
        from emelya.tools.edit import execute
        result = execute({"changes": [{"create": "new.txt", "content": "hello\nworld"}]}, self.ctx)
        self.assertIn("Applied edits", result)
        self.assertEqual((self.tmpdir / "new.txt").read_text(), "hello\nworld")

    def test_overwrite_requires_read(self):
        from emelya.tools.edit import execute
        (self.tmpdir / "existing.txt").write_text("old")
        result = execute({"changes": [{"write": "existing.txt", "content": "new"}]}, self.ctx)
        self.assertIn("not read first", result)

    def test_overwrite_after_read(self):
        from emelya.tools.edit import execute
        from emelya.tools.read import execute as read_exec
        (self.tmpdir / "existing.txt").write_text("old")
        read_exec({"path": "existing.txt"}, self.ctx)
        result = execute({"changes": [{"write": "existing.txt", "content": "new"}]}, self.ctx)
        self.assertIn("Applied edits", result)

    def test_creates_parent_dirs(self):
        from emelya.tools.edit import execute
        result = execute({"changes": [{"create": "sub/dir/file.txt", "content": "deep"}]}, self.ctx)
        self.assertIn("Applied edits", result)
        self.assertTrue((self.tmpdir / "sub" / "dir" / "file.txt").exists())


class TestEditTool(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.ctx = make_ctx(self.tmpdir)
        (self.tmpdir / "code.py").write_text("def hello():\n    return 'hello'\n")
        from emelya.tools.read import execute as read_exec
        read_exec({"path": "code.py"}, self.ctx)

    def test_replace(self):
        from emelya.tools.edit import execute
        result = execute({
            "changes": [{"update": "code.py", "old_string": "return 'hello'",
                         "new_string": "return 'world'"}],
        }, self.ctx)
        self.assertIn("Applied edits", result)
        self.assertIn("world", (self.tmpdir / "code.py").read_text())

    def test_not_found_string(self):
        from emelya.tools.edit import execute
        result = execute({
            "changes": [{"update": "code.py", "old_string": "nonexistent",
                         "new_string": "x"}],
        }, self.ctx)
        self.assertIn("not found", result)

    def test_requires_read(self):
        from emelya.tools.edit import execute
        (self.tmpdir / "unread.py").write_text("x")
        result = execute({
            "changes": [{"update": "unread.py", "old_string": "x", "new_string": "y"}],
        }, self.ctx)
        self.assertIn("not read first", result)

    def test_path_hint(self):
        from emelya.tools.edit import execute
        (self.tmpdir / "src").mkdir(exist_ok=True)
        (self.tmpdir / "src" / "file.py").write_text("x")
        result = execute({
            "changes": [{"update": "src/aigent/file.py", "old_string": "x",
                         "new_string": "y"}],
        }, self.ctx)
        self.assertIn("file not found", result)


class TestGlobTool(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.ctx = make_ctx(self.tmpdir)
        (self.tmpdir / "a.py").touch()
        (self.tmpdir / "b.py").touch()
        (self.tmpdir / "sub").mkdir()
        (self.tmpdir / "sub" / "c.py").touch()

    def test_simple_glob(self):
        from emelya.tools.glob_tool import execute
        result = execute({"pattern": "*.py"}, self.ctx)
        self.assertIn("a.py", result)
        self.assertIn("b.py", result)

    def test_recursive_glob(self):
        from emelya.tools.glob_tool import execute
        result = execute({"pattern": "**/*.py"}, self.ctx)
        self.assertIn("c.py", result)


class TestGrepTool(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.ctx = make_ctx(self.tmpdir)
        (self.tmpdir / "file.py").write_text("def hello():\n    pass\ndef world():\n    pass\n")

    def test_search(self):
        from emelya.tools.grep import execute
        result = execute({"pattern": "def.*hello"}, self.ctx)
        self.assertIn("hello", result)

    def test_no_match(self):
        from emelya.tools.grep import execute
        result = execute({"pattern": "zzzzz"}, self.ctx)
        self.assertIn("No matches", result)

    def test_ignore_case(self):
        from emelya.tools.grep import execute
        result = execute({"pattern": "HELLO", "ignore_case": True}, self.ctx)
        self.assertIn("hello", result)


class TestListDir(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.ctx = make_ctx(self.tmpdir)
        (self.tmpdir / "file.txt").write_text("x")
        (self.tmpdir / "sub").mkdir()

    def test_list(self):
        from emelya.tools.list_dir import execute
        result = execute({}, self.ctx)
        self.assertIn("file.txt", result)
        self.assertIn("sub/", result)


class TestUndoStack(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.ctx = make_ctx(self.tmpdir)

    def test_undo_new_file(self):
        from emelya.tools.edit import execute
        execute({"changes": [{"create": "new.txt", "content": "hello"}]}, self.ctx)
        self.assertTrue((self.tmpdir / "new.txt").exists())
        result = self.ctx.undo()
        self.assertIn("deleted", result)
        self.assertFalse((self.tmpdir / "new.txt").exists())

    def test_undo_edit(self):
        from emelya.tools.read import execute as read_exec
        from emelya.tools.edit import execute as edit_exec
        (self.tmpdir / "f.txt").write_text("original")
        read_exec({"path": "f.txt"}, self.ctx)
        edit_exec({"changes": [{"update": "f.txt", "old_string": "original",
                                "new_string": "changed"}]}, self.ctx)
        self.assertIn("changed", (self.tmpdir / "f.txt").read_text())
        self.ctx.undo()
        self.assertIn("original", (self.tmpdir / "f.txt").read_text())


if __name__ == "__main__":
    unittest.main()
