"""Tests for the hooks system — HookResult, HookRunner, and tool integrations."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from emelya.hooks import HookResult, HookRunner
from emelya.sandbox import Sandbox
from emelya.tools import ToolContext


# ---------------------------------------------------------------------------
# HookResult
# ---------------------------------------------------------------------------

class TestHookResult(unittest.TestCase):

    def test_defaults(self):
        hr = HookResult()
        self.assertFalse(hr.block)
        self.assertEqual(hr.reason, "")
        self.assertEqual(hr.message, "")
        self.assertEqual(hr.context, "")
        self.assertIsNone(hr.result)
        self.assertIsNone(hr.command)

    def test_explicit_fields(self):
        hr = HookResult(block=True, reason="test", result="ok", command="ls")
        self.assertTrue(hr.block)
        self.assertEqual(hr.reason, "test")
        self.assertEqual(hr.result, "ok")
        self.assertEqual(hr.command, "ls")


# ---------------------------------------------------------------------------
# HookRunner — loading
# ---------------------------------------------------------------------------

class TestHookRunnerLoad(unittest.TestCase):

    def _runner(self, tmpdir):
        return HookRunner(work_dir=Path(tmpdir))

    def test_no_files_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._runner(tmpdir)
            self.assertEqual(runner._hooks, {})

    def test_local_hooks_loaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            emelya_dir = Path(tmpdir) / ".emelya"
            emelya_dir.mkdir()
            hooks_data = {
                "PreBash": [{"type": "command", "command": "echo hi"}]
            }
            (emelya_dir / "hooks.json").write_text(json.dumps(hooks_data))
            runner = self._runner(tmpdir)
            self.assertIn("PreBash", runner._hooks)
            self.assertEqual(len(runner._hooks["PreBash"]), 1)

    def test_global_and_local_merged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            emelya_dir = Path(tmpdir) / ".emelya"
            emelya_dir.mkdir()
            # Local file
            local_hooks = {"PreBash": [{"type": "command", "command": "local"}]}
            (emelya_dir / "hooks.json").write_text(json.dumps(local_hooks))

            # Simulate global via patching Path.home
            global_dir = Path(tmpdir) / "global_home" / ".emelya"
            global_dir.mkdir(parents=True)
            global_hooks = {"PreBash": [{"type": "command", "command": "global"}]}
            (global_dir / "hooks.json").write_text(json.dumps(global_hooks))

            with patch("emelya.hooks.Path") as MockPath:
                # Make Path.home() return our fake home
                real_path = Path
                def path_factory(*args):
                    if not args:
                        return real_path()
                    return real_path(*args)
                MockPath.side_effect = path_factory
                MockPath.home.return_value = real_path(tmpdir) / "global_home"

                runner = HookRunner(work_dir=real_path(tmpdir))

            # Both hooks should be present, global first
            self.assertIn("PreBash", runner._hooks)
            cmds = [h["command"] for h in runner._hooks["PreBash"]]
            self.assertEqual(cmds, ["global", "local"])

    def test_invalid_json_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            emelya_dir = Path(tmpdir) / ".emelya"
            emelya_dir.mkdir()
            (emelya_dir / "hooks.json").write_text("not json")
            runner = self._runner(tmpdir)
            self.assertEqual(runner._hooks, {})

    def test_non_list_event_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            emelya_dir = Path(tmpdir) / ".emelya"
            emelya_dir.mkdir()
            # hooks must be lists; dict value should be skipped
            (emelya_dir / "hooks.json").write_text(json.dumps({"PreBash": "bad"}))
            runner = self._runner(tmpdir)
            self.assertEqual(runner._hooks, {})


# ---------------------------------------------------------------------------
# HookRunner — run() with no hooks
# ---------------------------------------------------------------------------

class TestHookRunnerRun(unittest.TestCase):

    def _runner(self, tmpdir):
        return HookRunner(work_dir=Path(tmpdir))

    def test_no_hooks_returns_default_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._runner(tmpdir)
            hr = runner.run("PreBash", {"command": "ls"})
            self.assertFalse(hr.block)
            self.assertIsNone(hr.result)

    def test_tools_filter_skips_non_matching(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            emelya_dir = Path(tmpdir) / ".emelya"
            emelya_dir.mkdir()
            hooks_data = {
                "PostBash": [{
                    "type": "command",
                    "command": "exit 2",  # would block
                    "tools": ["write"],   # but only for "write" tool
                }]
            }
            (emelya_dir / "hooks.json").write_text(json.dumps(hooks_data))
            runner = self._runner(tmpdir)
            hr = runner.run("PostBash", {"tool": "bash", "stdout": "hello"})
            self.assertFalse(hr.block)

    def test_tools_filter_matches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            emelya_dir = Path(tmpdir) / ".emelya"
            emelya_dir.mkdir()
            # Hook that returns JSON block=true for "bash" tool
            script = Path(tmpdir) / "hook.sh"
            script.write_text('#!/bin/sh\necho \'{"block":true,"reason":"test"}\'\n')
            script.chmod(0o755)
            hooks_data = {
                "PostBash": [{
                    "type": "command",
                    "command": str(script),
                    "tools": ["bash"],
                }]
            }
            (emelya_dir / "hooks.json").write_text(json.dumps(hooks_data))
            runner = self._runner(tmpdir)
            hr = runner.run("PostBash", {"tool": "bash", "stdout": "hello"})
            self.assertTrue(hr.block)


# ---------------------------------------------------------------------------
# Command hook
# ---------------------------------------------------------------------------

class TestCommandHook(unittest.TestCase):

    def _runner_with_hooks(self, tmpdir, hooks_data):
        emelya_dir = Path(tmpdir) / ".emelya"
        emelya_dir.mkdir(exist_ok=True)
        (emelya_dir / "hooks.json").write_text(json.dumps(hooks_data))
        return HookRunner(work_dir=Path(tmpdir))

    def test_exit_code_2_blocks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "hook.sh"
            script.write_text("#!/bin/sh\nexit 2\n")
            script.chmod(0o755)
            runner = self._runner_with_hooks(tmpdir, {
                "PreBash": [{"type": "command", "command": str(script)}]
            })
            hr = runner.run("PreBash", {"command": "rm -rf /"})
            self.assertTrue(hr.block)

    def test_json_block_response(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "hook.sh"
            script.write_text(
                '#!/bin/sh\necho \'{"block":true,"reason":"denied","message":"Not allowed"}\'\n'
            )
            script.chmod(0o755)
            runner = self._runner_with_hooks(tmpdir, {
                "PreToolUse": [{"type": "command", "command": str(script)}]
            })
            hr = runner.run("PreToolUse", {"tool": "bash"})
            self.assertTrue(hr.block)
            self.assertEqual(hr.reason, "denied")
            self.assertEqual(hr.message, "Not allowed")

    def test_result_substitution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "hook.sh"
            script.write_text(
                '#!/bin/sh\necho \'{"result":"filtered output"}\'\n'
            )
            script.chmod(0o755)
            runner = self._runner_with_hooks(tmpdir, {
                "PostBash": [{"type": "command", "command": str(script)}]
            })
            hr = runner.run("PostBash", {"command": "ls", "stdout": "original", "returncode": 0})
            self.assertEqual(hr.result, "filtered output")
            self.assertFalse(hr.block)

    def test_command_substitution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "hook.sh"
            script.write_text(
                '#!/bin/sh\necho \'{"command":"rtk git diff"}\'\n'
            )
            script.chmod(0o755)
            runner = self._runner_with_hooks(tmpdir, {
                "PreBash": [{"type": "command", "command": str(script)}]
            })
            hr = runner.run("PreBash", {"command": "git diff"})
            self.assertEqual(hr.command, "rtk git diff")
            self.assertFalse(hr.block)

    def test_env_vars_set(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "env_out.txt"
            script = Path(tmpdir) / "hook.sh"
            script.write_text(
                f'#!/bin/sh\necho "$EMELYA_EVENT $EMELYA_TOOL" > {output_file}\n'
            )
            script.chmod(0o755)
            runner = self._runner_with_hooks(tmpdir, {
                "PreBash": [{"type": "command", "command": str(script)}]
            })
            runner.run("PreBash", {"command": "ls", "tool": "bash"})
            content = output_file.read_text().strip()
            self.assertEqual(content, "PreBash bash")

    def test_timeout_silently_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "hook.sh"
            script.write_text("#!/bin/sh\nsleep 100\n")
            script.chmod(0o755)
            runner = self._runner_with_hooks(tmpdir, {
                "PreBash": [{"type": "command", "command": str(script), "timeout": 1}]
            })
            # Should not raise, just return default HookResult
            hr = runner.run("PreBash", {"command": "ls"})
            self.assertFalse(hr.block)


# ---------------------------------------------------------------------------
# HTTP hook
# ---------------------------------------------------------------------------

class TestHttpHook(unittest.TestCase):

    def _runner_with_hooks(self, tmpdir, hooks_data):
        emelya_dir = Path(tmpdir) / ".emelya"
        emelya_dir.mkdir(exist_ok=True)
        (emelya_dir / "hooks.json").write_text(json.dumps(hooks_data))
        return HookRunner(work_dir=Path(tmpdir))

    def test_http_hook_posts_and_parses_response(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._runner_with_hooks(tmpdir, {
                "PreToolUse": [{
                    "type": "http",
                    "url": "http://fake.example/hook",
                }]
            })
            response_body = json.dumps({"block": True, "reason": "policy"}).encode()
            mock_resp = MagicMock()
            mock_resp.read.return_value = response_body
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)

            with patch("urllib.request.urlopen", return_value=mock_resp):
                hr = runner.run("PreToolUse", {"tool": "bash"})

            self.assertTrue(hr.block)
            self.assertEqual(hr.reason, "policy")

    def test_http_hook_allowed_env_var_interpolation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._runner_with_hooks(tmpdir, {
                "PreWrite": [{
                    "type": "http",
                    "url": "http://fake.example/hook",
                    "headers": {"Authorization": "Bearer ${MY_TOKEN}"},
                    "allowedEnvVars": ["MY_TOKEN"],
                }]
            })
            mock_resp = MagicMock()
            mock_resp.read.return_value = b"{}"
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)

            captured = {}
            original_request = __import__("urllib.request", fromlist=["Request"]).Request

            def capture_request(url, data=None, headers=None, method=None):
                captured["headers"] = headers
                return original_request(url, data=data, headers=headers, method=method)

            with patch.dict("os.environ", {"MY_TOKEN": "secret123"}):
                with patch("urllib.request.Request", side_effect=capture_request):
                    with patch("urllib.request.urlopen", return_value=mock_resp):
                        runner.run("PreWrite", {"path": "foo.py"})

            self.assertEqual(captured.get("headers", {}).get("Authorization"), "Bearer secret123")

    def test_http_hook_disallowed_env_var_not_interpolated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._runner_with_hooks(tmpdir, {
                "PreWrite": [{
                    "type": "http",
                    "url": "http://fake.example/hook",
                    "headers": {"X-Secret": "${PRIVATE_KEY}"},
                    "allowedEnvVars": [],  # not allowed
                }]
            })
            mock_resp = MagicMock()
            mock_resp.read.return_value = b"{}"
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)

            captured = {}
            original_request = __import__("urllib.request", fromlist=["Request"]).Request

            def capture_request(url, data=None, headers=None, method=None):
                captured["headers"] = headers
                return original_request(url, data=data, headers=headers, method=method)

            with patch.dict("os.environ", {"PRIVATE_KEY": "topsecret"}):
                with patch("urllib.request.Request", side_effect=capture_request):
                    with patch("urllib.request.urlopen", return_value=mock_resp):
                        runner.run("PreWrite", {"path": "foo.py"})

            # Placeholder should remain unexpanded
            self.assertEqual(captured.get("headers", {}).get("X-Secret"), "${PRIVATE_KEY}")

    def test_http_hook_error_silently_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._runner_with_hooks(tmpdir, {
                "PreBash": [{"type": "http", "url": "http://fake.example/hook"}]
            })
            with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
                hr = runner.run("PreBash", {"command": "ls"})
            self.assertFalse(hr.block)


# ---------------------------------------------------------------------------
# Prompt hook
# ---------------------------------------------------------------------------

class TestPromptHook(unittest.TestCase):

    def _runner_with_prompt_hook(self, tmpdir, prompt_template, model="default"):
        emelya_dir = Path(tmpdir) / ".emelya"
        emelya_dir.mkdir(exist_ok=True)
        hooks_data = {
            "PreBash": [{
                "type": "prompt",
                "prompt": prompt_template,
                "model": model,
            }]
        }
        (emelya_dir / "hooks.json").write_text(json.dumps(hooks_data))
        mock_client = MagicMock()
        llm_getter = lambda name: mock_client
        runner = HookRunner(work_dir=Path(tmpdir), llm_getter=llm_getter)
        return runner, mock_client

    def test_prompt_hook_calls_llm_and_parses(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner, mock_client = self._runner_with_prompt_hook(
                tmpdir, "Analyse: {command} event={event}"
            )
            mock_client.chat.return_value = {
                "content": '{"block": false, "result": "clean"}'
            }
            hr = runner.run("PreBash", {"command": "ls -la"})
            self.assertFalse(hr.block)
            self.assertEqual(hr.result, "clean")
            # Verify placeholders were substituted
            call_args = mock_client.chat.call_args
            messages = call_args[1]["messages"] if call_args[1] else call_args[0][0]
            prompt_sent = messages[0]["content"]
            self.assertIn("ls -la", prompt_sent)
            self.assertIn("PreBash", prompt_sent)

    def test_prompt_hook_block(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner, mock_client = self._runner_with_prompt_hook(
                tmpdir, "Evaluate: {command}"
            )
            mock_client.chat.return_value = {
                "content": '{"block": true, "reason": "dangerous"}'
            }
            hr = runner.run("PreBash", {"command": "rm -rf /"})
            self.assertTrue(hr.block)
            self.assertEqual(hr.reason, "dangerous")

    def test_prompt_hook_no_llm_getter_returns_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            emelya_dir = Path(tmpdir) / ".emelya"
            emelya_dir.mkdir()
            hooks_data = {"PreBash": [{"type": "prompt", "prompt": "Check: {command}"}]}
            (emelya_dir / "hooks.json").write_text(json.dumps(hooks_data))
            runner = HookRunner(work_dir=Path(tmpdir), llm_getter=None)
            hr = runner.run("PreBash", {"command": "ls"})
            self.assertFalse(hr.block)

    def test_prompt_hook_llm_error_returns_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner, mock_client = self._runner_with_prompt_hook(
                tmpdir, "Check: {command}"
            )
            mock_client.chat.side_effect = Exception("LLM error")
            hr = runner.run("PreBash", {"command": "ls"})
            self.assertFalse(hr.block)

    def test_prompt_hook_extracts_json_from_prose(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner, mock_client = self._runner_with_prompt_hook(
                tmpdir, "Check: {command}"
            )
            mock_client.chat.return_value = {
                "content": 'Sure! Here is my answer:\n{"block": true, "reason": "bad"}\nThat is all.'
            }
            hr = runner.run("PreBash", {"command": "ls"})
            self.assertTrue(hr.block)


# ---------------------------------------------------------------------------
# Chain behaviour
# ---------------------------------------------------------------------------

class TestChainBehaviour(unittest.TestCase):

    def _runner_with_scripts(self, tmpdir, scripts):
        """Create hooks from list of (script_body, event) tuples."""
        hooks_list = []
        for i, body in enumerate(scripts):
            script = Path(tmpdir) / f"hook{i}.sh"
            script.write_text("#!/bin/sh\n" + body + "\n")
            script.chmod(0o755)
            hooks_list.append({"type": "command", "command": str(script)})
        emelya_dir = Path(tmpdir) / ".emelya"
        emelya_dir.mkdir(exist_ok=True)
        (emelya_dir / "hooks.json").write_text(json.dumps({"PreBash": hooks_list}))
        return HookRunner(work_dir=Path(tmpdir))

    def test_command_substitution_flows_to_next_hook(self):
        """Second hook receives updated command from first hook via EMELYA_COMMAND."""
        with tempfile.TemporaryDirectory() as tmpdir:
            seen_file = Path(tmpdir) / "seen_cmd.txt"
            runner = self._runner_with_scripts(tmpdir, [
                # First hook substitutes the command
                'echo \'{"command":"rtk git diff"}\'',
                # Second hook writes the env var it received to a file
                f'echo "$EMELYA_COMMAND" > {seen_file}',
            ])
            runner.run("PreBash", {"command": "git diff"})
            seen = seen_file.read_text().strip()
            self.assertEqual(seen, "rtk git diff")

    def test_result_substitution_flows_to_next_hook(self):
        """Second hook receives updated stdout from first hook."""
        with tempfile.TemporaryDirectory() as tmpdir:
            seen_file = Path(tmpdir) / "seen_result.txt"
            # We need to pass stdout via stdin JSON, not env
            # First hook: substitute result to "filtered"
            # Second hook: read stdin and write the stdout field to file
            script0 = Path(tmpdir) / "hook0.sh"
            script0.write_text('#!/bin/sh\necho \'{"result":"filtered"}\'\n')
            script0.chmod(0o755)
            script1 = Path(tmpdir) / "hook1.sh"
            script1.write_text(
                f'#!/bin/sh\npython3 -c "import json,sys; d=json.load(sys.stdin); open(\'{seen_file}\',\'w\').write(d[\'stdout\'])"\n'
            )
            script1.chmod(0o755)
            emelya_dir = Path(tmpdir) / ".emelya"
            emelya_dir.mkdir(exist_ok=True)
            hooks_data = {
                "PostBash": [
                    {"type": "command", "command": str(script0)},
                    {"type": "command", "command": str(script1)},
                ]
            }
            (emelya_dir / "hooks.json").write_text(json.dumps(hooks_data))
            runner = HookRunner(work_dir=Path(tmpdir))
            runner.run("PostBash", {"command": "ls", "stdout": "original", "returncode": 0})
            seen = seen_file.read_text().strip()
            self.assertEqual(seen, "filtered")

    def test_block_stops_chain(self):
        """Once block=True, remaining hooks are not executed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ran_file = Path(tmpdir) / "ran.txt"
            runner = self._runner_with_scripts(tmpdir, [
                # First hook blocks
                'echo \'{"block":true}\'',
                # Second hook should NOT run
                f'touch {ran_file}',
            ])
            hr = runner.run("PreBash", {"command": "ls"})
            self.assertTrue(hr.block)
            self.assertFalse(ran_file.exists())

    def test_context_accumulated_across_chain(self):
        """context strings from multiple hooks are concatenated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = self._runner_with_scripts(tmpdir, [
                'echo \'{"context":"first"}\'',
                'echo \'{"context":"second"}\'',
            ])
            hr = runner.run("PreBash", {"command": "ls"})
            self.assertIn("first", hr.context)
            self.assertIn("second", hr.context)


# ---------------------------------------------------------------------------
# bash.py integration
# ---------------------------------------------------------------------------

class TestBashHookIntegration(unittest.TestCase):

    def _ctx(self, tmpdir, hook_runner=None):
        sandbox = Sandbox(Path(tmpdir))
        ctx = ToolContext(
            work_dir=Path(tmpdir),
            sandbox=sandbox,
            hook_runner=hook_runner,
        )
        return ctx

    def test_prebash_block(self):
        from emelya.tools.bash import execute
        with tempfile.TemporaryDirectory() as tmpdir:
            hr = HookResult(block=True, reason="blocked")
            mock_runner = MagicMock()
            mock_runner.run.return_value = hr
            ctx = self._ctx(tmpdir, hook_runner=mock_runner)
            result = execute({"command": "rm -rf /"}, ctx)
            self.assertIn("Blocked", result)
            mock_runner.run.assert_called_once()
            call_event = mock_runner.run.call_args[0][0]
            self.assertEqual(call_event, "PreBash")

    def test_prebash_command_substitution(self):
        from emelya.tools.bash import execute
        with tempfile.TemporaryDirectory() as tmpdir:
            # PreBash substitutes command, PostBash returns default
            pre_hr = HookResult(command="echo substituted")
            post_hr = HookResult()

            mock_runner = MagicMock()
            mock_runner.run.side_effect = lambda event, data: (
                pre_hr if event == "PreBash" else post_hr
            )
            ctx = self._ctx(tmpdir, hook_runner=mock_runner)
            result = execute({"command": "echo original"}, ctx)
            self.assertIn("substituted", result)

    def test_postbash_result_substitution(self):
        from emelya.tools.bash import execute
        with tempfile.TemporaryDirectory() as tmpdir:
            pre_hr = HookResult()
            post_hr = HookResult(result="hook filtered output")

            mock_runner = MagicMock()
            mock_runner.run.side_effect = lambda event, data: (
                pre_hr if event == "PreBash" else post_hr
            )
            ctx = self._ctx(tmpdir, hook_runner=mock_runner)
            result = execute({"command": "echo hello"}, ctx)
            self.assertEqual(result, "hook filtered output")

    def test_no_hook_runner_works_normally(self):
        from emelya.tools.bash import execute
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = self._ctx(tmpdir, hook_runner=None)
            result = execute({"command": "echo hello"}, ctx)
            self.assertIn("hello", result)


# ---------------------------------------------------------------------------
# write.py integration
# ---------------------------------------------------------------------------

class TestWriteHookIntegration(unittest.TestCase):

    def _ctx(self, tmpdir, hook_runner=None):
        sandbox = Sandbox(Path(tmpdir))
        ctx = ToolContext(
            work_dir=Path(tmpdir),
            sandbox=sandbox,
            hook_runner=hook_runner,
        )
        return ctx

    def test_prewrite_block_prevents_write(self):
        from emelya.tools.write import execute
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "output.txt"
            hr = HookResult(block=True, reason="no writes allowed")
            mock_runner = MagicMock()
            mock_runner.run.return_value = hr
            ctx = self._ctx(tmpdir, hook_runner=mock_runner)
            result = execute({"path": "output.txt", "content": "hello"}, ctx)
            self.assertIn("Blocked", result)
            self.assertFalse(target.exists())

    def test_prewrite_hook_called_with_path_and_content(self):
        from emelya.tools.write import execute
        with tempfile.TemporaryDirectory() as tmpdir:
            hr = HookResult()  # don't block
            mock_runner = MagicMock()
            mock_runner.run.return_value = hr
            ctx = self._ctx(tmpdir, hook_runner=mock_runner)
            execute({"path": "foo.txt", "content": "bar"}, ctx)
            mock_runner.run.assert_called_once()
            call_event, call_data = mock_runner.run.call_args[0]
            self.assertEqual(call_event, "PreWrite")
            self.assertIn("foo.txt", call_data.get("path", ""))
            self.assertEqual(call_data.get("content"), "bar")

    def test_no_hook_runner_writes_normally(self):
        from emelya.tools.write import execute
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = self._ctx(tmpdir, hook_runner=None)
            result = execute({"path": "out.txt", "content": "hello"}, ctx)
            self.assertIn("Written", result)
            self.assertEqual((Path(tmpdir) / "out.txt").read_text(), "hello")


# ---------------------------------------------------------------------------
# edit.py integration
# ---------------------------------------------------------------------------

class TestEditHookIntegration(unittest.TestCase):

    def _ctx(self, tmpdir, hook_runner=None):
        sandbox = Sandbox(Path(tmpdir))
        ctx = ToolContext(
            work_dir=Path(tmpdir),
            sandbox=sandbox,
            hook_runner=hook_runner,
        )
        return ctx

    def test_prewrite_block_prevents_edit(self):
        from emelya.tools.edit import execute
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "file.txt"
            target.write_text("old content here")
            hr = HookResult(block=True, reason="edits blocked")
            mock_runner = MagicMock()
            mock_runner.run.return_value = hr
            ctx = self._ctx(tmpdir, hook_runner=mock_runner)
            ctx.files_read.add(str(target))
            result = execute({
                "path": "file.txt",
                "old_string": "old content",
                "new_string": "new content",
            }, ctx)
            self.assertIn("Blocked", result)
            # File must not be modified
            self.assertEqual(target.read_text(), "old content here")

    def test_no_hook_runner_edits_normally(self):
        from emelya.tools.edit import execute
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "file.txt"
            target.write_text("hello world")
            ctx = self._ctx(tmpdir, hook_runner=None)
            ctx.files_read.add(str(target))
            result = execute({
                "path": "file.txt",
                "old_string": "hello",
                "new_string": "goodbye",
            }, ctx)
            self.assertIn("Replaced", result)
            self.assertIn("goodbye", target.read_text())


if __name__ == "__main__":
    unittest.main()
