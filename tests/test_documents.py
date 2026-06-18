"""Tests for doc_read and archive tools."""

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from emelya.sandbox import Sandbox
from emelya.tools import ToolContext


def make_ctx(tmpdir):
    return ToolContext(work_dir=tmpdir, sandbox=Sandbox(tmpdir))


class TestDocRead(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.ctx = make_ctx(self.tmpdir)

    def test_read_docx(self):
        """Create a minimal .docx and read it."""
        from emelya.tools.doc_read import execute
        # A .docx is a zip with word/document.xml
        docx_path = self.tmpdir / "test.docx"
        with zipfile.ZipFile(docx_path, "w") as zf:
            xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>Hello World</w:t></w:r></w:p></w:body>'
                '</w:document>'
            )
            zf.writestr("word/document.xml", xml)

        result = execute({"path": "test.docx"}, self.ctx)
        self.assertIn("Hello World", result)

    def test_read_nonexistent(self):
        from emelya.tools.doc_read import execute
        result = execute({"path": "nonexistent.docx"}, self.ctx)
        self.assertIn("Error", result)

    def test_read_unsupported(self):
        from emelya.tools.doc_read import execute
        (self.tmpdir / "test.xyz").write_text("data")
        result = execute({"path": "test.xyz"}, self.ctx)
        self.assertIn("Error", result)


class TestArchive(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.ctx = make_ctx(self.tmpdir)

    def test_list_zip(self):
        from emelya.tools.archive import execute
        zip_path = self.tmpdir / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("hello.txt", "Hello!")
            zf.writestr("sub/world.txt", "World!")

        result = execute({"path": "test.zip"}, self.ctx)
        self.assertIn("hello.txt", result)
        self.assertIn("sub/world.txt", result)

    def test_extract_zip_entry(self):
        from emelya.tools.archive import execute
        zip_path = self.tmpdir / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("data.txt", "Secret content")

        result = execute({"path": "test.zip", "entry": "data.txt"}, self.ctx)
        self.assertIn("Secret content", result)

    def test_nonexistent_archive(self):
        from emelya.tools.archive import execute
        result = execute({"path": "nope.zip"}, self.ctx)
        self.assertIn("Error", result)


if __name__ == "__main__":
    unittest.main()
