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
from emelya.repl import REPL, _find_git_root


class TestExecutionEnvironment(unittest.TestCase):

    def test_finds_git_root_from_nested_workdir(self):
        root = Path(tempfile.mkdtemp())
        (root / ".git").mkdir()
        nested = root / "src" / "package"
        nested.mkdir(parents=True)
        self.assertEqual(_find_git_root(nested), root)

    def test_git_root_is_optional(self):
        root = Path(tempfile.mkdtemp())
        self.assertIsNone(_find_git_root(root))


class TestShellEscape(unittest.TestCase):

    def test_runs_with_workdir_and_inherited_stdio(self):
        repl = REPL.__new__(REPL)
        repl.config = MagicMock(work_dir=Path("/tmp/project"))
        repl.context = Context()
        repl.conversation_log = []
        repl._autosave = MagicMock()
        repl.memory_store = MagicMock()
        repl.memory_store.put.return_value = {"id": "mem-shell-1"}
        completed = MagicMock(returncode=0)
        with patch("emelya.repl.subprocess.run", return_value=completed) as run:
            self.assertEqual(repl._run_shell_escape("mc"), 0)
        run.assert_called_once_with("mc", shell=True, cwd="/tmp/project")
        self.assertIn("mem-shell-1", repl.context.messages[-1]["content"])
        self.assertNotIn("STDOUT", repl.context.messages[-1]["content"])
        repl._autosave.assert_called_once()

    def test_ctrl_c_returns_to_repl(self):
        repl = REPL.__new__(REPL)
        repl.config = MagicMock(work_dir=Path("/tmp/project"))
        with patch("emelya.repl.subprocess.run", side_effect=KeyboardInterrupt), \
                patch("builtins.print"):
            self.assertEqual(repl._run_shell_escape("vim"), 130)

    def test_empty_shell_escape_shows_usage(self):
        repl = REPL.__new__(REPL)
        with patch("builtins.print") as output:
            self.assertEqual(repl._run_shell_escape(""), 2)
        self.assertIn("Usage", str(output.call_args))

    def test_noninteractive_output_goes_to_memory_not_context(self):
        repl = REPL.__new__(REPL)
        repl.config = MagicMock(work_dir=Path("/tmp/project"))
        repl.context = Context()
        repl.conversation_log = []
        repl._autosave = MagicMock()
        repl.memory_store = MagicMock()
        repl.memory_store.put.return_value = {"id": "mem-shell-2"}
        completed = MagicMock(returncode=0, stdout="secret output\n", stderr="")
        with patch("emelya.repl.subprocess.run", return_value=completed) as run, \
                patch("builtins.print"):
            self.assertEqual(repl._run_shell_escape("ls"), 0)
        run.assert_called_once_with(
            "ls", shell=True, cwd="/tmp/project",
            capture_output=True, text=True, errors="replace",
        )
        memory_details = repl.memory_store.put.call_args.kwargs["details"]
        context_entry = repl.context.messages[-1]["content"]
        self.assertIn("secret output", memory_details)
        self.assertNotIn("secret output", context_entry)
        self.assertIn("memory(action=get, id=mem-shell-2)", context_entry)


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
        self.skills_dirs = [tmpdir / "skills"]
        self.agents_dirs = [tmpdir / "agents"]

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
        self._mcp_cache_dirs = {}
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

    def test_resume_replays_saved_dialog(self):
        self.repl.context.add_user("какая погода?")
        self.repl.context.add_assistant_text("Солнечно.")
        self.repl._autosave()
        saved_name = self.repl.session_name
        with patch("builtins.print") as mock_print:
            self.repl._cmd_resume(saved_name)
        printed = "\n".join(
            " ".join(str(arg) for arg in call.args)
            for call in mock_print.call_args_list
        )
        self.assertIn("какая погода?", printed)
        self.assertIn("Солнечно.", printed)

    def test_resume_replay_renders_markdown_table(self):
        self.repl.context.add_user("покажи данные")
        self.repl.context.add_assistant_text("| A | B |\n|---|---|\n| 1 | 2 |")
        self.repl._autosave()
        with patch("builtins.print") as mock_print:
            self.repl._cmd_resume(self.repl.session_name)
        printed = "\n".join(
            " ".join(str(arg) for arg in call.args)
            for call in mock_print.call_args_list
        )
        self.assertIn("┌", printed)
        self.assertNotIn("|---|", printed)

    def test_export_after_resume_contains_saved_dialog(self):
        self.repl.context.add_user("сделай таблицу")
        self.repl.context.add_assistant_text("| A | B |\n|---|---|\n| 1 | 2 |")
        self.repl._autosave()
        saved_name = self.repl.session_name
        self.repl.conversation_log = []

        with patch("builtins.print"):
            self.repl._cmd_resume(saved_name)
            self.repl._cmd_export("resumed.md")

        exported = (self.tmpdir / "resumed.md").read_text(encoding="utf-8")
        self.assertIn("сделай таблицу", exported)
        self.assertIn("| A | B |", exported)
        self.assertIn("## User", exported)
        self.assertIn("## Assistant", exported)

    def test_export_falls_back_to_context(self):
        self.repl.context.add_user("вопрос")
        self.repl.context.add_assistant_text("ответ")
        self.repl.conversation_log = []
        with patch("builtins.print"):
            self.repl._cmd_export("fallback.md")
        exported = (self.tmpdir / "fallback.md").read_text(encoding="utf-8")
        self.assertIn("вопрос", exported)
        self.assertIn("ответ", exported)

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


class TestLoadModelsGlobal(unittest.TestCase):
    """Test that _load_models merges global and local models.json."""

    def setUp(self):
        self.global_home = Path(tempfile.mkdtemp())
        self.local_dir = Path(tempfile.mkdtemp())
        self.global_emelya = self.global_home / ".emelya"
        self.global_emelya.mkdir()
        (self.local_dir / ".emelya").mkdir()
        self.repl = MockREPL(self.local_dir)

    def tearDown(self):
        self.repl.cleanup()

    def _write_models(self, path, models=None, routing=None):
        data = {}
        if models:
            data["models"] = models
        if routing:
            data["routing"] = routing
        path.write_text(json.dumps(data), encoding="utf-8")

    def _load(self):
        with patch("emelya.models.Path") as MockPath:
            MockPath.home.return_value = self.global_home
            MockPath.side_effect = lambda *a, **kw: Path(*a, **kw)
            return self.repl._load_models()

    def test_global_models_loaded(self):
        self._write_models(
            self.global_emelya / "models.json",
            models={"gpt4": {"base_url": "https://api.openai.com", "model": "gpt-4o"}},
        )
        models = self._load()
        self.assertIn("gpt4", models)
        self.assertEqual(models["gpt4"]["model"], "gpt-4o")

    def test_local_overrides_global_model(self):
        self._write_models(
            self.global_emelya / "models.json",
            models={"shared": {"base_url": "https://global.example.com", "model": "v1"}},
        )
        self._write_models(
            self.local_dir / ".emelya" / "models.json",
            models={"shared": {"base_url": "https://local.example.com", "model": "v2"}},
        )
        models = self._load()
        self.assertEqual(models["shared"]["base_url"], "https://local.example.com")
        self.assertEqual(models["shared"]["model"], "v2")

    def test_local_overrides_global_routing(self):
        self._write_models(
            self.global_emelya / "models.json",
            routing={"default": ["gpt4"], "agent": ["gpt4"]},
        )
        self._write_models(
            self.local_dir / ".emelya" / "models.json",
            routing={"default": ["local"]},
        )
        self._load()
        self.assertEqual(self.repl._routing["default"], ["local"])
        self.assertEqual(self.repl._routing["agent"], ["gpt4"])

    def test_no_global_file(self):
        self._write_models(
            self.local_dir / ".emelya" / "models.json",
            models={"mymodel": {"base_url": "http://localhost", "model": "x"}},
        )
        models = self._load()
        self.assertIn("mymodel", models)

    def test_global_and_local_models_merged(self):
        self._write_models(
            self.global_emelya / "models.json",
            models={"global_only": {"base_url": "https://g.example.com", "model": "g"}},
        )
        self._write_models(
            self.local_dir / ".emelya" / "models.json",
            models={"local_only": {"base_url": "https://l.example.com", "model": "l"}},
        )
        models = self._load()
        self.assertIn("global_only", models)
        self.assertIn("local_only", models)


class TestInitMCPGlobal(unittest.TestCase):
    """Test that _init_mcp merges global and local mcp.json with correct cache dirs."""

    def setUp(self):
        self.global_home = Path(tempfile.mkdtemp())
        self.local_dir = Path(tempfile.mkdtemp())
        self.global_emelya = self.global_home / ".emelya"
        self.global_emelya.mkdir()
        (self.local_dir / ".emelya").mkdir()
        self.repl = MockREPL(self.local_dir)
        self.repl._mcp_startup_info = []

    def tearDown(self):
        self.repl.cleanup()

    def _write_mcp(self, path, servers):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"servers": servers}, indent=2), encoding="utf-8"
        )

    def _init(self):
        # load_cache returns a stub so we always take the "cached" path —
        # no real connections or _mcp_clients lookups are attempted.
        stub_cached = [{"name": "stub_tool", "description": "stub",
                        "inputSchema": {"type": "object", "properties": {}}}]
        with patch("emelya.mcp_manager.Path") as MockPath, \
             patch("emelya.mcp_manager.load_cache", return_value=stub_cached):
            MockPath.home.return_value = self.global_home
            MockPath.side_effect = lambda *a, **kw: Path(*a, **kw)
            self.repl._register_mcp_lazy = lambda name, cached: None
            self.repl._init_mcp()

    def test_global_servers_loaded(self):
        self._write_mcp(
            self.global_emelya / "mcp.json",
            {"playwright": {"command": "npx", "args": ["-y", "@playwright/mcp"]}},
        )
        self._init()
        self.assertIn("playwright", self.repl._mcp_config)

    def test_local_overrides_global_server(self):
        self._write_mcp(
            self.global_emelya / "mcp.json",
            {"srv": {"command": "python", "args": ["global.py"]}},
        )
        self._write_mcp(
            self.local_dir / ".emelya" / "mcp.json",
            {"srv": {"command": "python", "args": ["local.py"]}},
        )
        self._init()
        self.assertEqual(self.repl._mcp_config["srv"]["args"], ["local.py"])

    def test_global_cache_dir_assigned(self):
        self._write_mcp(
            self.global_emelya / "mcp.json",
            {"global_srv": {"command": "node", "args": ["server.js"]}},
        )
        self._init()
        expected = self.global_emelya / "mcp_cache"
        self.assertEqual(self.repl._mcp_cache_dirs.get("global_srv"), expected)

    def test_local_cache_dir_assigned(self):
        self._write_mcp(
            self.local_dir / ".emelya" / "mcp.json",
            {"local_srv": {"command": "node", "args": ["server.js"]}},
        )
        self._init()
        expected = self.local_dir / ".emelya" / "mcp_cache"
        self.assertEqual(self.repl._mcp_cache_dirs.get("local_srv"), expected)

    def test_uncached_server_does_not_connect_during_startup(self):
        self._write_mcp(
            self.global_emelya / "mcp.json",
            {"remote": {"url": "https://example.invalid/mcp"}},
        )
        with patch("emelya.mcp_manager.Path") as MockPath, \
                patch("emelya.mcp_manager.load_cache", return_value=None), \
                patch.object(self.repl, "_mcp_connect") as connect:
            MockPath.home.return_value = self.global_home
            MockPath.side_effect = lambda *a, **kw: Path(*a, **kw)
            self.repl._init_mcp()
        connect.assert_not_called()
        self.assertIn("remote", self.repl._mcp_config)

    def test_local_override_uses_local_cache_dir(self):
        self._write_mcp(
            self.global_emelya / "mcp.json",
            {"srv": {"command": "python", "args": ["global.py"]}},
        )
        self._write_mcp(
            self.local_dir / ".emelya" / "mcp.json",
            {"srv": {"command": "python", "args": ["local.py"]}},
        )
        self._init()
        expected = self.local_dir / ".emelya" / "mcp_cache"
        self.assertEqual(self.repl._mcp_cache_dirs.get("srv"), expected)


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


class TestScanSkillsMultiDir(unittest.TestCase):
    """Test scan_skills with a list of directories."""

    def setUp(self):
        from emelya.tools.skill import scan_skills
        self.scan_skills = scan_skills
        self.builtin = Path(tempfile.mkdtemp())
        self.global_dir = Path(tempfile.mkdtemp())
        self.local_dir = Path(tempfile.mkdtemp())

    def test_single_dir_still_works(self):
        (self.builtin / "git.md").write_text("# Git\nGit skill.")
        skills = self.scan_skills(self.builtin)
        self.assertIn("git", skills)

    def test_all_dirs_merged(self):
        (self.builtin / "git.md").write_text("# Git\nBuiltin git.")
        (self.global_dir / "docker.md").write_text("# Docker\nGlobal docker.")
        (self.local_dir / "myskill.md").write_text("# My Skill\nLocal skill.")
        skills = self.scan_skills([self.builtin, self.global_dir, self.local_dir])
        self.assertIn("git", skills)
        self.assertIn("docker", skills)
        self.assertIn("myskill", skills)

    def test_local_overrides_builtin(self):
        (self.builtin / "git.md").write_text("# Git Builtin\nBuiltin.")
        (self.local_dir / "git.md").write_text("# Git Local\nLocal.")
        skills = self.scan_skills([self.builtin, self.local_dir])
        self.assertEqual(skills["git"], "Git Local")

    def test_nonexistent_dir_skipped(self):
        (self.builtin / "git.md").write_text("# Git\nBuiltin.")
        skills = self.scan_skills([self.builtin, Path("/nonexistent/path")])
        self.assertIn("git", skills)


class TestScanAgentsMultiDir(unittest.TestCase):
    """Test scan_agents with a list of directories."""

    def setUp(self):
        from emelya.tools.agent import scan_agents
        self.scan_agents = scan_agents
        self.builtin = Path(tempfile.mkdtemp())
        self.global_dir = Path(tempfile.mkdtemp())
        self.local_dir = Path(tempfile.mkdtemp())

    def _write_agent(self, d, name, desc):
        (d / f"{name}.md").write_text(
            f"---\ndescription: {desc}\n---\nDo the task."
        )

    def test_all_dirs_merged(self):
        self._write_agent(self.builtin, "explore_code", "Explore codebase")
        self._write_agent(self.global_dir, "weekly_review", "Weekly code review")
        self._write_agent(self.local_dir, "deploy_check", "Deployment checks")
        agents = self.scan_agents([self.builtin, self.global_dir, self.local_dir])
        self.assertIn("explore_code", agents)
        self.assertIn("weekly_review", agents)
        self.assertIn("deploy_check", agents)

    def test_local_overrides_builtin(self):
        self._write_agent(self.builtin, "review_file", "Builtin review")
        self._write_agent(self.local_dir, "review_file", "Custom review")
        agents = self.scan_agents([self.builtin, self.local_dir])
        self.assertEqual(agents["review_file"], "Custom review")

    def test_nonexistent_dir_skipped(self):
        self._write_agent(self.builtin, "explore_code", "Explore")
        agents = self.scan_agents([self.builtin, Path("/nonexistent")])
        self.assertIn("explore_code", agents)


class TestCustomCommandsGlobal(unittest.TestCase):
    """Test global ~/.emelya/commands/ merging with local commands."""

    def setUp(self):
        self.global_home = Path(tempfile.mkdtemp())
        self.global_cmds = self.global_home / ".emelya" / "commands"
        self.global_cmds.mkdir(parents=True)

        self.local_dir = Path(tempfile.mkdtemp())
        self.local_cmds = self.local_dir / ".emelya" / "commands"
        self.local_cmds.mkdir(parents=True)

        self.repl = MockREPL(self.local_dir)

    def tearDown(self):
        self.repl.cleanup()

    def _scan(self):
        # Patch Path.home() so global dir points to our temp home
        with patch("emelya.commands.custom.Path") as MockPath:
            MockPath.home.return_value = self.global_home
            MockPath.side_effect = lambda *a, **kw: Path(*a, **kw)
            return self.repl._scan_custom_commands()

    def test_global_command_loaded(self):
        (self.global_cmds / "weekly.md").write_text("# Weekly Report\nGenerate report.")
        cmds = self._scan()
        self.assertIn("/weekly", cmds)
        self.assertEqual(cmds["/weekly"]["desc"], "Weekly Report")

    def test_local_overrides_global(self):
        (self.global_cmds / "deploy.md").write_text("# Deploy Global\nGlobal deploy.")
        (self.local_cmds / "deploy.md").write_text("# Deploy Local\nLocal deploy.")
        cmds = self._scan()
        self.assertIn("/deploy", cmds)
        self.assertEqual(cmds["/deploy"]["desc"], "Deploy Local")
        self.assertEqual(cmds["/deploy"]["path"], self.local_cmds / "deploy.md")

    def test_global_and_local_merged(self):
        (self.global_cmds / "global_only.md").write_text("# Global Only\nGlobal cmd.")
        (self.local_cmds / "local_only.md").write_text("# Local Only\nLocal cmd.")
        cmds = self._scan()
        self.assertIn("/global_only", cmds)
        self.assertIn("/local_only", cmds)

    def test_no_global_dir(self):
        # ~/.emelya/commands/ doesn't exist — only local matters
        import shutil
        shutil.rmtree(self.global_cmds)
        (self.local_cmds / "deploy.md").write_text("# Deploy\nLocal only.")
        cmds = self._scan()
        self.assertIn("/deploy", cmds)


if __name__ == "__main__":
    unittest.main()
