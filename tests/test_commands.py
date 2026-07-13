"""Tests for commands — /rename, /resume, /clear, /model, /mcp, /compact."""

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

        self.conversation_log = []
        self._model_display = "test-model"
        self._models = {"local": {"base_url": "http://localhost:8080", "model": "default"}}
        self._models_path = tmpdir / ".emelya" / "models.json"
        self._routing = {}
        self._manual_model = False
        self._mcp_clients = {}
        self._mcp_config = {}
        self._custom_commands = {}
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

    def _autosave(self):
        try:
            self.context.save(self._session_dir() / "context.json")
        except Exception:
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
        self.repl.context.add_user("hello")
        self.repl.context.add_assistant_text("hi")
        with patch("builtins.print"):
            self.repl._handle_command("/clear")
        self.assertEqual(len(self.repl.context.messages), 0)

    def test_undo_empty(self):

        with patch("builtins.print") as mock_print:
            self.repl._handle_command( "/undo")
        mock_print.assert_called()

    def test_tools(self):

        with patch("builtins.print"):
            result = self.repl._handle_command( "/tools")
        self.assertTrue(result)

    def test_context(self):

        with patch("builtins.print"):
            result = self.repl._handle_command( "/context")
        self.assertTrue(result)

    def test_unknown_returns_false(self):

        result = self.repl._handle_command( "/nonexistent_cmd")
        self.assertFalse(result)


class TestRenameResumeClear(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.repl = MockREPL(self.tmpdir)

    def tearDown(self):
        self.repl.cleanup()

    def test_rename_changes_session(self):
        self.repl.context.add_user("hello")
        self.repl.context.add_assistant_text("world")
        with patch("builtins.print"):
            self.repl._cmd_rename("my_session")
        self.assertEqual(self.repl.session_name, "my_session")
        ctx_file = self.repl.sessions_dir / "my_session" / "context.json"
        self.assertTrue(ctx_file.exists())

    def test_rename_no_arg_shows_usage(self):
        with patch("builtins.print") as mock_print:
            self.repl._cmd_rename("")
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("rename", printed.lower())

    def test_resume_restores_context(self):
        # Save first via autosave
        self.repl.context.add_user("hello")
        self.repl.context.add_assistant_text("world")
        self.repl._autosave()
        saved_name = self.repl.session_name
        # Clear and resume
        with patch("builtins.print"):
            self.repl._cmd_clear()
        self.assertEqual(len(self.repl.context.messages), 0)
        with patch("builtins.print"):
            self.repl._cmd_resume(saved_name)
        self.assertEqual(len(self.repl.context.messages), 2)

    def test_resume_nonexistent(self):
        with patch("builtins.print") as mock_print:
            self.repl._cmd_resume("nonexistent")
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("nonexistent", printed)

    def test_resume_no_arg_lists_sessions(self):
        # Create a session first
        self.repl._autosave()
        with patch("builtins.print") as mock_print:
            self.repl._cmd_resume("")
        self.assertTrue(len(mock_print.call_args_list) > 0)

    def test_branch_copies_context(self):
        self.repl.context.add_user("hello")
        self.repl.context.add_assistant_text("world")
        old_name = self.repl.session_name
        with patch("builtins.print"):
            self.repl._cmd_branch("my-branch")
        # Session changed, context preserved
        self.assertEqual(self.repl.session_name, "my-branch")
        self.assertNotEqual(self.repl.session_name, old_name)
        self.assertEqual(len(self.repl.context.messages), 2)
        # Both sessions have context files
        self.assertTrue((self.repl.sessions_dir / old_name / "context.json").exists())
        self.assertTrue((self.repl.sessions_dir / "my-branch" / "context.json").exists())

    def test_branch_auto_name(self):
        self.repl.context.add_user("hello")
        with patch("builtins.print"):
            self.repl._cmd_branch("")
        self.assertIn("branch", self.repl.session_name)

    def test_clear_starts_fresh(self):
        self.repl.context.add_user("hello")
        old_name = self.repl.session_name
        with patch("builtins.print"):
            self.repl._cmd_clear()
        self.assertEqual(len(self.repl.context.messages), 0)
        self.assertNotEqual(self.repl.session_name, old_name)


class TestModelCommand(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.repl = MockREPL(self.tmpdir)

    def tearDown(self):
        self.repl.cleanup()

    def test_list_models(self):

        with patch("builtins.print") as mock_print:
            self.repl._cmd_model( "")
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("local", printed)

    def test_unknown_model(self):

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

        result = MCPMixin._format_mcp_result('{"key": "value"}')
        self.assertIn("key", result)
        self.assertIn("\n", result)  # pretty-printed

    def test_mcp_format_result_plain(self):

        result = MCPMixin._format_mcp_result("plain text")
        self.assertEqual(result, "plain text")


class TestCustomCommands(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.commands_dir = self.tmpdir / ".emelya" / "commands"
        self.commands_dir.mkdir(parents=True)
        self.repl = MockREPL(self.tmpdir)

    def tearDown(self):
        self.repl.cleanup()

    def test_scan_empty_dir(self):
        cmds = self.repl._scan_custom_commands()
        self.assertEqual(cmds, {})

    def test_scan_finds_md_file(self):
        (self.commands_dir / "deploy.md").write_text("# Deploy\nRun deployment steps.")
        cmds = self.repl._scan_custom_commands()
        self.assertIn("/deploy", cmds)
        self.assertEqual(cmds["/deploy"]["desc"], "Deploy")

    def test_scan_frontmatter_description(self):
        (self.commands_dir / "report.md").write_text(
            "---\ndescription: Weekly report\n---\nGenerate a weekly report."
        )
        cmds = self.repl._scan_custom_commands()
        self.assertIn("/report", cmds)
        self.assertEqual(cmds["/report"]["desc"], "Weekly report")

    def test_handle_custom_command_dispatches(self):
        (self.commands_dir / "greet.md").write_text("Say hello.")
        self.repl._custom_commands = self.repl._scan_custom_commands()
        calls = []
        self.repl._agent_loop = lambda msg: calls.append(msg)
        handled = self.repl._handle_command("/greet")
        self.assertTrue(handled)
        self.assertEqual(len(calls), 1)
        self.assertIn("Say hello.", calls[0])

    def test_handle_custom_command_passes_args(self):
        (self.commands_dir / "deploy.md").write_text("Deploy to {args}.")
        self.repl._custom_commands = self.repl._scan_custom_commands()
        calls = []
        self.repl._agent_loop = lambda msg: calls.append(msg)
        self.repl._handle_command("/deploy staging")
        self.assertIn("Deploy to staging.", calls[0])

    def test_list_commands_empty(self):
        with patch("builtins.print") as mock_print:
            self.repl._cmd_list_commands()
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("No custom commands", printed)

    def test_list_commands_shows_entries(self):
        (self.commands_dir / "deploy.md").write_text("# Deploy\nDeploy steps.")
        self.repl._custom_commands = self.repl._scan_custom_commands()
        with patch("builtins.print") as mock_print:
            self.repl._cmd_list_commands()
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("/deploy", printed)
        self.assertIn("Deploy", printed)


if __name__ == "__main__":
    unittest.main()
