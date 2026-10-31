"""Tests for persistent tasks and memory-backed plan execution."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from emelya.memory import FileMemoryStore
from emelya.repl import REPL
from emelya.task_store import FileTaskStore
from emelya.sandbox import Sandbox
from emelya.tools import ToolContext


class TestFileTaskStore(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / "tasks"
        self.store = FileTaskStore(self.root)

    def test_create_update_and_reload(self):
        task = self.store.create("Inspect module", dependencies=[2])
        self.store.update(task["id"], status="done", artifact="result.md")

        reloaded = FileTaskStore(self.root).get(task["id"])
        self.assertEqual(reloaded["status"], "done")
        self.assertEqual(reloaded["artifact"], "result.md")
        self.assertEqual(reloaded["dependencies"], [2])

    def test_callable_root_follows_session(self):
        session = {"name": "one"}
        store = FileTaskStore(lambda: self.root / session["name"])
        first = store.create("first")
        session["name"] = "two"
        second = store.create("second")

        self.assertEqual(first["id"], 1)
        self.assertEqual(second["id"], 1)
        self.assertEqual([task["title"] for task in store.list()], ["second"])

    def test_task_tool_uses_persistent_store(self):
        from emelya.tools.task import execute

        ctx = ToolContext(
            work_dir=self.root.parent,
            sandbox=Sandbox(self.root.parent),
            task_store=self.store,
        )
        created = execute({"action": "create", "title": "Audit module"}, ctx)
        self.assertIn("Task #1", created)
        execute({"action": "complete", "id": 1}, ctx)

        self.assertEqual(FileTaskStore(self.root).get(1)["status"], "done")


class TestPlanPersistence(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.repl = object.__new__(REPL)
        self.repl.sessions_dir = self.root / "sessions"
        self.repl.session_name = "test"
        self.repl.memory_store = FileMemoryStore(
            lambda: self.repl._session_dir() / "memory"
        )
        self.repl.task_store = FileTaskStore(
            lambda: self.repl._session_dir() / "tasks"
        )
        self.repl.tool_ctx = object()

        class Registry:
            def __init__(self):
                self.calls = []

            def execute(inner_self, name, args, ctx):
                inner_self.calls.append((name, args))
                if len(inner_self.calls) == 1:
                    return "A" * 10000
                return "Final verified report"

        self.repl.registry = Registry()

    def test_plan_persists_tasks_memory_and_references(self):
        result = self.repl._execute_plan([
            {
                "description": "Inspect project",
                "agent": "explore_code",
                "args": {"task": "inspect"},
                "output": "inspection.md",
            },
            {
                "description": "Compile report",
                "agent": "report_compiler",
                "args": {"task": "compile"},
                "output": "report.md",
                "uses": ["inspection.md"],
            },
        ])

        tasks = self.repl.task_store.list()
        memories = self.repl.memory_store.search(tags=["plan-step"], limit=10)
        second_args = self.repl.registry.calls[1][1]["args"]

        self.assertEqual([task["status"] for task in tasks], ["done", "done"])
        self.assertEqual(len(memories), 2)
        self.assertIn("artifact=", second_args["task"])
        self.assertIn("memory_id=", second_args["task"])
        self.assertNotIn("A" * 100, second_args["task"])
        self.assertIn("Plan complete", result)
        self.assertIn("Final verified report", result)

    def test_failed_dependency_is_persisted_and_gates_completion(self):
        class FailingRegistry:
            def execute(inner_self, name, args, ctx):
                return "Error: audit failed"

        self.repl.registry = FailingRegistry()
        result = self.repl._execute_plan([
            {
                "description": "Audit",
                "agent": "review_file",
                "args": {"task": "audit"},
                "output": "audit.md",
            },
            {
                "description": "Compile",
                "agent": "report_compiler",
                "args": {"task": "compile"},
                "output": "report.md",
                "uses": ["audit.md"],
            },
        ])

        self.assertEqual(
            [task["status"] for task in self.repl.task_store.list()],
            ["failed", "skipped"],
        )
        self.assertIn("Plan incomplete", result)
        self.assertIn("Do not claim full completion", result)

    def test_invalid_dependencies_are_rejected_before_execution(self):
        result = self.repl._execute_plan([{
            "description": "Compile",
            "agent": "report_compiler",
            "args": {"task": "compile"},
            "uses": ["missing.md"],
        }])

        self.assertIn("unknown or future outputs", result)
        self.assertEqual(self.repl.task_store.list(), [])

    def test_agent_failure_text_is_not_marked_done(self):
        class FailedAgentRegistry:
            def execute(inner_self, name, args, ctx):
                return "Error: agent 'audit' failed at step 2/6. Error: timed out"

        self.repl.registry = FailedAgentRegistry()
        result = self.repl._execute_plan([{
            "description": "Audit source",
            "agent": "audit",
            "args": {"task": "audit"},
            "output": "audit.md",
        }])

        self.assertEqual(self.repl.task_store.list()[0]["status"], "failed")
        self.assertIn("Plan incomplete", result)


if __name__ == "__main__":
    unittest.main()
