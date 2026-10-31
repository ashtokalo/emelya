"""Tests for agent tool — sub-agent loop, duplicate detection, forced last step."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import tempfile

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from emelya.tools import ToolContext, ToolRegistry
from emelya.sandbox import Sandbox


class MockLLMClient:
    """Mock LLM client that returns pre-configured responses in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.model = "test-model"

    def chat(self, messages, tools=None, temperature=0.7, max_tokens=8192):
        self.calls.append({
            "n_messages": len(messages),
            "has_tools": tools is not None,
            "messages": messages,
            "tools": tools,
        })
        if not self.responses:
            return _make_response(content="No more responses")
        return self.responses.pop(0)


def _make_response(content=None, tool_calls=None):
    """Build a mock LLM response."""
    raw = {"role": "assistant", "content": content}
    parsed_tc = None
    if tool_calls:
        raw["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["args"]),
                },
            }
            for tc in tool_calls
        ]
        parsed_tc = tool_calls
    return {
        "content": content,
        "tool_calls": parsed_tc,
        "raw": raw,
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        "model_id": "test-model",
    }


def _make_ctx(tmpdir, client, registry):
    """Create a ToolContext wired to mock client and registry."""
    return ToolContext(
        work_dir=tmpdir,
        sandbox=Sandbox(tmpdir),
        llm_client=client,
        tool_registry=registry,
    )


class TestAgentDuplicateDetection(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.registry = ToolRegistry()
        self.registry.register("grep", {
            "type": "function",
            "function": {"name": "grep", "parameters": {
                "type": "object", "properties": {}, "required": [],
            }},
        }, lambda args, ctx: "match: line 42")

    def test_duplicate_returns_error(self):
        from emelya.tools.agent import _run_agent_loop

        # Step 1: grep call → executed
        # Step 2: same grep call → DUPLICATE error
        # Step 3: model gives text answer
        client = MockLLMClient([
            _make_response(tool_calls=[
                {"id": "c1", "name": "grep", "args": {"pattern": "test"}},
            ]),
            _make_response(tool_calls=[
                {"id": "c2", "name": "grep", "args": {"pattern": "test"}},
            ]),
            _make_response(content="Found it."),
        ])
        ctx = _make_ctx(self.tmpdir, client, self.registry)

        with patch("builtins.print"):
            result = _run_agent_loop(
                prompt="search",
                allowed_tools=["grep"],
                max_steps=5,
                ctx=ctx,
                depth=1,
                agent_name="test",
            )

        self.assertEqual(result, "Found it.")

    def test_different_args_not_duplicate(self):
        from emelya.tools.agent import _run_agent_loop

        client = MockLLMClient([
            _make_response(tool_calls=[
                {"id": "c1", "name": "grep", "args": {"pattern": "foo"}},
            ]),
            _make_response(tool_calls=[
                {"id": "c2", "name": "grep", "args": {"pattern": "bar"}},
            ]),
            _make_response(content="Done."),
        ])
        ctx = _make_ctx(self.tmpdir, client, self.registry)

        with patch("builtins.print"):
            result = _run_agent_loop(
                prompt="search",
                allowed_tools=["grep"],
                max_steps=5,
                ctx=ctx,
                depth=1,
                agent_name="test",
            )

        # Both should execute (different args), then text answer
        self.assertEqual(result, "Done.")


class TestAgentForcedLastStep(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.registry = ToolRegistry()
        self.registry.register("grep", {
            "type": "function",
            "function": {"name": "grep", "parameters": {
                "type": "object", "properties": {}, "required": [],
            }},
        }, lambda args, ctx: "match")

    def test_last_step_tools_none(self):
        """The final two attempts reserve budget for synthesis."""
        from emelya.tools.agent import _run_agent_loop

        client = MockLLMClient([
            # Step 0: tool call
            _make_response(tool_calls=[
                {"id": "c1", "name": "grep", "args": {"pattern": "x"}},
            ]),
            # Step 1 (last, max_steps=2): model should get tools=None → text only
            _make_response(content="Final answer."),
        ])
        ctx = _make_ctx(self.tmpdir, client, self.registry)

        with patch("builtins.print"):
            result = _run_agent_loop(
                prompt="test",
                allowed_tools=["grep"],
                max_steps=2,
                ctx=ctx,
                depth=1,
                agent_name="test",
            )

        self.assertEqual(result, "Final answer.")
        self.assertTrue(client.calls[0]["has_tools"])
        self.assertFalse(client.calls[-1]["has_tools"])

    def test_non_last_step_has_tools(self):
        """Steps before the last should have tools."""
        from emelya.tools.agent import _run_agent_loop

        client = MockLLMClient([
            _make_response(tool_calls=[
                {"id": "c1", "name": "grep", "args": {"pattern": "x"}},
            ]),
            _make_response(tool_calls=[
                {"id": "c2", "name": "grep", "args": {"pattern": "y"}},
            ]),
            _make_response(content="Done."),
        ])
        ctx = _make_ctx(self.tmpdir, client, self.registry)

        with patch("builtins.print"):
            result = _run_agent_loop(
                prompt="test",
                allowed_tools=["grep"],
                max_steps=4,
                ctx=ctx,
                depth=1,
                agent_name="test",
            )

        # First two calls should have tools, last should not (but model returned text, so it's fine)
        self.assertTrue(client.calls[0]["has_tools"])   # step 0
        self.assertTrue(client.calls[1]["has_tools"])   # step 1 (remaining=2)


class TestAgentTextResponse(unittest.TestCase):

    def test_immediate_text(self):
        """Agent returns text on first call without tool calls."""
        from emelya.tools.agent import _run_agent_loop

        tmpdir = Path(tempfile.mkdtemp())
        registry = ToolRegistry()
        client = MockLLMClient([
            _make_response(content="The answer is 42."),
        ])
        ctx = _make_ctx(tmpdir, client, registry)

        with patch("builtins.print"):
            result = _run_agent_loop(
                prompt="what is the answer?",
                allowed_tools=[],
                max_steps=5,
                ctx=ctx,
                depth=1,
                agent_name="test",
            )

        self.assertEqual(result, "The answer is 42.")

    def test_step_limit_forces_text(self):
        """When last step has tools=None, model must return text."""
        from emelya.tools.agent import _run_agent_loop

        tmpdir = Path(tempfile.mkdtemp())
        registry = ToolRegistry()
        registry.register("grep", {
            "type": "function",
            "function": {"name": "grep", "parameters": {
                "type": "object", "properties": {}, "required": [],
            }},
        }, lambda args, ctx: "result")

        # Steps 0,1: tool calls. Step 2 (last): tools=None → text response
        client = MockLLMClient([
            _make_response(tool_calls=[{"id": "c0", "name": "grep", "args": {"pattern": "0"}}]),
            _make_response(tool_calls=[{"id": "c1", "name": "grep", "args": {"pattern": "1"}}]),
            _make_response(content="Here is the answer."),  # last step, tools=None
        ])

        ctx = _make_ctx(tmpdir, client, registry)

        with patch("builtins.print"):
            result = _run_agent_loop(
                prompt="search everything",
                allowed_tools=["grep"],
                max_steps=3,
                ctx=ctx,
                depth=1,
                agent_name="test",
            )

        self.assertEqual(result, "Here is the answer.")
        # Last call should have been with tools=None
        self.assertFalse(client.calls[-1]["has_tools"])

    def test_plain_text_tool_call_is_failure(self):
        """Tool markup emitted without a structured call must not count as work."""
        from emelya.tools.agent import _run_agent_loop

        tmpdir = Path(tempfile.mkdtemp())
        registry = ToolRegistry()
        client = MockLLMClient([
            _make_response(content=(
                "<tool_call><function=read><parameter=path>src/x.py"
                "</parameter></function></tool_call>"
            )),
        ])
        ctx = _make_ctx(tmpdir, client, registry)

        with patch("builtins.print"):
            result = _run_agent_loop(
                prompt="inspect", allowed_tools=[], max_steps=1,
                ctx=ctx, depth=1, agent_name="test",
            )

        self.assertTrue(result.startswith("Error:"))
        self.assertIn("plain text", result)

    def test_progress_only_response_gets_one_summary_retry(self):
        """A promise to continue is repaired within the reserved final attempt."""
        from emelya.tools.agent import _run_agent_loop

        tmpdir = Path(tempfile.mkdtemp())
        registry = ToolRegistry()
        client = MockLLMClient([
            _make_response(content="Let me inspect one more directory."),
            _make_response(content="Partial: inspected the available project files."),
        ])
        ctx = _make_ctx(tmpdir, client, registry)

        with patch("builtins.print"):
            result = _run_agent_loop(
                prompt="inspect", allowed_tools=[], max_steps=2,
                ctx=ctx, depth=1, agent_name="test",
            )

        self.assertEqual(result, "Partial: inspected the available project files.")
        self.assertEqual(len(client.calls), 2)

    def test_execution_contract_preserves_user_language(self):
        from emelya.tools.agent import (
            _detect_task_language, _execution_contract, _language_bound_tools,
            _language_system_message,
        )

        contract = _execution_contract()
        self.assertIn("same language as the user's task", contract)
        self.assertIn("tool descriptions", contract)
        self.assertEqual(_detect_task_language({"task": "проверь проект"}), "ru")
        self.assertIn("поля description/title/summary", _language_system_message("ru"))
        tools = _language_bound_tools([{
            "type": "function",
            "function": {
                "name": "bash",
                "parameters": {
                    "type": "object",
                    "properties": {"description": {"type": "string"}},
                },
            },
        }], "ru")
        description = tools[0]["function"]["parameters"]["properties"]["description"]
        self.assertIn("по-русски", description["description"])

    def test_subagent_receives_russian_system_language(self):
        from emelya.tools.agent import _run_agent_loop

        tmpdir = Path(tempfile.mkdtemp())
        registry = ToolRegistry()
        client = MockLLMClient([_make_response(content="Готово.")])
        ctx = _make_ctx(tmpdir, client, registry)

        with patch("builtins.print"):
            result = _run_agent_loop(
                prompt="Проверь проект", allowed_tools=[], max_steps=2,
                ctx=ctx, depth=1, agent_name="test", user_language="ru",
            )

        self.assertEqual(result, "Готово.")
        first_message = client.calls[0]["messages"][0]
        self.assertEqual(first_message["role"], "system")
        self.assertIn("Язык пользователя — русский", first_message["content"])

    def test_parent_language_overrides_english_agent_arguments(self):
        """Nested agents inherit the user's language instead of re-guessing it."""
        from emelya.tools.agent import execute

        tmpdir = Path(tempfile.mkdtemp())
        agents_dir = tmpdir / "agents"
        agents_dir.mkdir()
        (agents_dir / "review.md").write_text(
            "---\ntools: []\nmax_steps: 2\n---\nReview {target}",
            encoding="utf-8",
        )
        registry = ToolRegistry()
        registry.register("agent", {
            "type": "function",
            "function": {"name": "agent", "parameters": {
                "type": "object", "properties": {}, "required": [],
            }},
        }, execute)
        client = MockLLMClient([_make_response(content="Проверено.")])
        ctx = _make_ctx(tmpdir, client, registry)
        ctx.agents_dirs = [agents_dir]
        ctx.user_language = "ru"

        with patch("builtins.print"):
            result = execute({
                "name": "review",
                "args": {"target": "src/module.py", "focus": "security"},
            }, ctx)

        self.assertEqual(result, "Проверено.")
        self.assertIn(
            "Язык пользователя — русский",
            client.calls[0]["messages"][0]["content"],
        )

    def test_large_tool_result_is_compacted_in_history(self):
        """Large tool output must not grow every following model request."""
        from emelya.tools.agent import _run_agent_loop

        tmpdir = Path(tempfile.mkdtemp())
        registry = ToolRegistry()
        registry.register("grep", {
            "type": "function",
            "function": {"name": "grep", "parameters": {
                "type": "object", "properties": {}, "required": [],
            }},
        }, lambda args, ctx: "x" * 20000)

        testcase = self

        class InspectingClient(MockLLMClient):
            def chat(inner_self, messages, **kwargs):
                if len(inner_self.calls) == 1:
                    tool_message = next(
                        message for message in reversed(messages)
                        if message["role"] == "tool"
                    )
                    testcase.assertLess(len(tool_message["content"]), 9000)
                    testcase.assertIn(
                        "omitted from agent context", tool_message["content"]
                    )
                return super().chat(messages, **kwargs)

        client = InspectingClient([
            _make_response(tool_calls=[
                {"id": "c1", "name": "grep", "args": {"pattern": "x"}},
            ]),
            _make_response(content="Done."),
        ])
        ctx = _make_ctx(tmpdir, client, registry)

        with patch("builtins.print"):
            result = _run_agent_loop(
                prompt="search", allowed_tools=["grep"], max_steps=3,
                ctx=ctx, depth=1, agent_name="test",
            )

        self.assertEqual(result, "Done.")


if __name__ == "__main__":
    unittest.main()
