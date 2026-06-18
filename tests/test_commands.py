"""Tests for commands — /save, /load, /new, /model, /mcp, /compact."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from emelya.context import Context
from emelya.log import SessionLog, UsageTracker
from emelya.sandbox import Sandbox
from emelya.tools import ToolContext, ToolRegistry
from emelya.models import ModelMixin
from emelya.mcp_manager import MCPMixin
from emelya.commands import CommandsMixin


class MockREPL(ModelMixin, MCPMixin, CommandsMixin):
    """Minimal mock that satisfies CommandsMixin/ModelMixin interface."""

    def __init__(self, tmpdir):
        self.config = MagicMock()
        self.config.work_dir = tmpdir
        self.config.context_limit = 8000
        self.config.confirm_mode = "no_confirm"
        self.config.base_url = "http://localhost:8080"
        self.config.model = "default"
        self.config.model_name = None
        self.config.temperature = 0.7
        self.config.max_tokens = 8192
        self.config.max_iterations = 10
        self.config.stream = False
        self.config.config_file = tmpdir / "emelya.md"

        self.context = Context(context_limit=8000)
        self.context.set_system("You are a test assistant.")
        self.sandbox = Sandbox(tmpdir)
        self.registry = ToolRegistry()

        self.session_name = "test_session"
        self.sessions_dir = tmpdir / ".emelya" / "sessions"
        self.app_dir = tmpdir
        self.skills_dir = tmpdir / "skills"
        self.agents_dir = tmpdir / "agents"

        self.log = SessionLog(self.sessions_dir / self.session_name / "log.txt")
        self.usage_tracker = UsageTracker(tmpdir / ".emelya" / "usage.jsonl")
        self.input_history = MagicMock()

        self.tool_ctx = ToolContext(
            work_dir=tmpdir,
            sandbox=self.sandbox,
        )

        self._model_display = "test-model"
        self._models = {"local": {"base_url": "http://localhost:8080", "model": "default"}}
        self._models_path = tmpdir / ".emelya" / "models.json"
        self._routing = {}
        self._manual_model = False
        self._mcp_clients = {}
        self._mcp_config = {}
        self.active_model = "local"
        self.llm = MagicMock()
        self._use_compact_tools = False

    def _session_dir(self, name=None):
        return self.sessions_dir / (name or self.session_name)

    def _create_log(self):
        return SessionLog(self._session_dir() / "log.txt")

    def _init_system_prompt(self):
        self.context.set_system("Test system prompt.")

    def _update_dynamic_descriptions(self):
        pass

    def _agent_loop(self, user_input):
        pass

    def cleanup(self):
        self.log.close()
        self.usage_tracker.close()


class TestHandleCommand(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.repl = MockREPL(self.tmpdir)

    def tearDown(self):
        self.repl.cleanup()

    def test_clear(self):
        from emelya.commands import CommandsMixin
        self.repl.context.add_user("hello")
        self.repl.context.add_assistant_text("hi")
        self.repl._handle_command( "/clear")
        self.assertEqual(len(self.repl.context.messages), 0)

    def test_undo_empty(self):
        from emelya.commands import CommandsMixin
        with patch("builtins.print") as mock_print:
            self.repl._handle_command( "/undo")
        mock_print.assert_called()

    def test_tools(self):
        from emelya.commands import CommandsMixin
        with patch("builtins.print"):
            result = self.repl._handle_command( "/tools")
        self.assertTrue(result)

    def test_context(self):
        from emelya.commands import CommandsMixin
        with patch("builtins.print"):
            result = self.repl._handle_command( "/context")
        self.assertTrue(result)

    def test_unknown_returns_false(self):
        from emelya.commands import CommandsMixin
        result = self.repl._handle_command( "/nonexistent_cmd")
        self.assertFalse(result)


class TestSaveLoad(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.repl = MockREPL(self.tmpdir)

    def tearDown(self):
        self.repl.cleanup()

    def test_save_creates_context_file(self):
        from emelya.commands import CommandsMixin
        self.repl.context.add_user("hello")
        self.repl.context.add_assistant_text("world")
        with patch("builtins.print"):
            self.repl._cmd_save( "my_session")
        ctx_file = self.repl.sessions_dir / "my_session" / "context.json"
        self.assertTrue(ctx_file.exists())

    def test_load_restores_context(self):
        from emelya.commands import CommandsMixin
        # Save first
        self.repl.context.add_user("hello")
        self.repl.context.add_assistant_text("world")
        with patch("builtins.print"):
            self.repl._cmd_save( "saved")
        # Clear and load
        self.repl.context.clear()
        self.assertEqual(len(self.repl.context.messages), 0)
        with patch("builtins.print"):
            self.repl._cmd_load( "saved")
        self.assertEqual(len(self.repl.context.messages), 2)

    def test_load_nonexistent(self):
        with patch("builtins.print") as mock_print:
            self.repl._cmd_load("nonexistent")
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("nonexistent", printed)

    def test_new_clears_context(self):
        from emelya.commands import CommandsMixin
        self.repl.context.add_user("hello")
        with patch("builtins.print"):
            self.repl._cmd_new( "fresh")
        self.assertEqual(len(self.repl.context.messages), 0)
        self.assertEqual(self.repl.session_name, "fresh")


class TestModelCommand(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.repl = MockREPL(self.tmpdir)

    def tearDown(self):
        self.repl.cleanup()

    def test_list_models(self):
        from emelya.commands import CommandsMixin
        with patch("builtins.print") as mock_print:
            self.repl._cmd_model( "")
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("local", printed)

    def test_unknown_model(self):
        from emelya.commands import CommandsMixin
        with patch("builtins.print") as mock_print:
            self.repl._cmd_model( "nonexistent")
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("nonexistent", printed)


class TestMCPCommand(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.repl = MockREPL(self.tmpdir)

    def tearDown(self):
        self.repl.cleanup()

    def test_mcp_status_empty(self):
        from emelya.mcp_manager import MCPMixin
        with patch("builtins.print") as mock_print:
            self.repl._mcp_status()
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        # Should say no MCP or show empty status
        self.assertTrue(len(mock_print.call_args_list) > 0)

    def test_mcp_tools_no_server(self):
        with patch("builtins.print") as mock_print:
            self.repl._mcp_tools("nonexistent")
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("nonexistent", printed)

    def test_mcp_call_no_server(self):
        with patch("builtins.print") as mock_print:
            self.repl._mcp_call("nonexistent some_tool")
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("nonexistent", printed)

    def test_mcp_format_result_json(self):
        from emelya.mcp_manager import MCPMixin
        result = MCPMixin._format_mcp_result('{"key": "value"}')
        self.assertIn("key", result)
        self.assertIn("\n", result)  # pretty-printed

    def test_mcp_format_result_plain(self):
        from emelya.mcp_manager import MCPMixin
        result = MCPMixin._format_mcp_result("plain text")
        self.assertEqual(result, "plain text")


if __name__ == "__main__":
    unittest.main()
