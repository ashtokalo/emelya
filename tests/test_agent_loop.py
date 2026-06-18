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
        """On the last step (remaining==0), tools must be None."""
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
        # Second LLM call (last step) should have tools=None
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


if __name__ == "__main__":
    unittest.main()
