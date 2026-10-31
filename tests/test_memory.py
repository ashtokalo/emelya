"""Tests for session-scoped file memory and its tool wrapper."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from emelya.memory import FileMemoryStore
from emelya.sandbox import Sandbox
from emelya.tools import ToolContext, ToolRegistry


class TestFileMemoryStore(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / "memory"
        self.store = FileMemoryStore(self.root)

    def test_put_get_and_search(self):
        record = self.store.put(
            kind="finding",
            summary="Bash pipeline bypasses confirmation",
            scope="src/emelya/tools/bash.py",
            evidence={"line": 137},
            tags=["security", "bash"],
        )

        loaded = self.store.get(record["id"])
        self.assertEqual(loaded["summary"], record["summary"])
        self.assertEqual(loaded["evidence"], {"line": 137})
        self.assertEqual(self.store.search(query="pipeline confirmation")[0]["id"], record["id"])
        self.assertEqual(self.store.search(tags=["security"])[0]["id"], record["id"])
        self.assertEqual(self.store.search(scope="context.py"), [])

    def test_link(self):
        finding = self.store.put(summary="Potential defect", kind="finding")
        proof = self.store.put(summary="Regression test", kind="artifact")
        link = self.store.link(finding["id"], proof["id"], "verified_by")

        loaded = self.store.get(finding["id"])
        self.assertEqual(loaded["links"][0]["id"], link["id"])

    def test_review_preserves_status_history(self):
        record = self.store.put(summary="Potential defect", kind="finding")
        reviewed = self.store.review(
            record["id"], "verified", note="Confirmed by regression test", author="verifier"
        )

        self.assertEqual(reviewed["status"], "verified")
        self.assertEqual(reviewed["reviews"][0]["from"], "proposed")
        self.assertEqual(reviewed["reviews"][0]["to"], "verified")
        self.assertEqual(self.store.search(status="verified")[0]["id"], record["id"])

    def test_callable_root_follows_session_switch(self):
        session = {"name": "one"}
        store = FileMemoryStore(lambda: self.root / session["name"])
        first = store.put(summary="first")
        session["name"] = "two"
        second = store.put(summary="second")

        self.assertTrue((self.root / "one" / "entries" / f"{first['id']}.json").is_file())
        self.assertTrue((self.root / "two" / "entries" / f"{second['id']}.json").is_file())
        self.assertEqual(store.search(query="first"), [])

    def test_invalid_id_cannot_escape_store(self):
        with self.assertRaises(ValueError):
            self.store.get("../../context.json")


class TestMemoryTool(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.store = FileMemoryStore(self.tmpdir / "memory")
        self.ctx = ToolContext(
            work_dir=self.tmpdir,
            sandbox=Sandbox(self.tmpdir),
            memory_store=self.store,
        )

    def test_tool_put_search_get(self):
        from emelya.tools.memory import execute

        result = execute({
            "action": "put",
            "kind": "decision",
            "summary": "Use file-backed memory",
            "tags": ["architecture"],
        }, self.ctx)
        record_id = result.split()[2].rstrip(":")

        search = json.loads(execute({
            "action": "search",
            "query": "file-backed",
        }, self.ctx))
        self.assertEqual(search["entries"][0]["id"], record_id)
        loaded = json.loads(execute({"action": "get", "id": record_id}, self.ctx))
        self.assertEqual(loaded["summary"], "Use file-backed memory")
        reviewed = execute({
            "action": "review",
            "id": record_id,
            "status": "verified",
            "note": "Confirmed",
        }, self.ctx)
        self.assertIn("verified", reviewed)

    def test_registry_loads_memory_as_core_tool(self):
        registry = ToolRegistry()
        registry.load_builtin(Path(__file__).parent.parent / "src" / "emelya")
        self.assertIn("memory", registry.names)

    def test_subagent_receives_shared_memory_tool(self):
        from emelya.tools.agent import execute as agent_execute

        agents_dir = self.tmpdir / "agents"
        agents_dir.mkdir()
        (agents_dir / "reader.md").write_text(
            "---\ntools: read\nmax_steps: 2\n---\nInspect: {task}\n",
            encoding="utf-8",
        )

        class Client:
            model = "test"

            def __init__(self):
                self.tools = []

            def chat(self, messages, tools=None, **kwargs):
                self.tools = tools or []
                return {
                    "content": "done",
                    "tool_calls": None,
                    "raw": {"role": "assistant", "content": "done"},
                    "usage": {},
                    "model_id": "test",
                }

        client = Client()
        registry = ToolRegistry()
        registry.load_builtin(Path(__file__).parent.parent / "src" / "emelya")
        self.ctx.llm_client = client
        self.ctx.tool_registry = registry
        self.ctx.agents_dirs = [agents_dir]

        result = agent_execute({"name": "reader", "args": {"task": "project"}}, self.ctx)
        names = [tool["function"]["name"] for tool in client.tools]
        self.assertEqual(result, "done")
        self.assertIn("read", names)
        self.assertIn("memory", names)


if __name__ == "__main__":
    unittest.main()
