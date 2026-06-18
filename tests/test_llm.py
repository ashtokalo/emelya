"""Tests for llm.py — native format parsing, JSON repair, validation."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from emelya.llm import LLMClient


SAMPLE_TOOLS = [
    {"type": "function", "function": {"name": "grep", "parameters": {}}},
    {"type": "function", "function": {"name": "read", "parameters": {}}},
    {"type": "function", "function": {"name": "glob", "parameters": {}}},
    {"type": "function", "function": {"name": "edit", "parameters": {}}},
    {"type": "function", "function": {"name": "bash", "parameters": {}}},
]


class TestNativeToolCallParsing(unittest.TestCase):
    """Test _parse_native_tool_calls for all supported formats."""

    # --- DeepSeek DSML ---

    def test_dsml_single(self):
        content = (
            '<｜｜DSML｜｜tool_calls>'
            '<｜｜DSML｜｜invoke name="grep">'
            '<｜｜DSML｜｜parameter name="pattern" string="true">test</｜｜DSML｜｜parameter>'
            '<｜｜DSML｜｜parameter name="context" string="false">5</｜｜DSML｜｜parameter>'
            '</｜｜DSML｜｜invoke>'
            '</｜｜DSML｜｜tool_calls>'
        )
        calls = LLMClient._parse_native_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "grep")
        self.assertEqual(calls[0]["args"]["pattern"], "test")
        self.assertEqual(calls[0]["args"]["context"], 5)

    def test_dsml_multiple(self):
        content = (
            '<｜｜DSML｜｜invoke name="read">'
            '<｜｜DSML｜｜parameter name="path" string="true">a.py</｜｜DSML｜｜parameter>'
            '</｜｜DSML｜｜invoke>'
            '<｜｜DSML｜｜invoke name="glob">'
            '<｜｜DSML｜｜parameter name="pattern" string="true">*.md</｜｜DSML｜｜parameter>'
            '</｜｜DSML｜｜invoke>'
        )
        calls = LLMClient._parse_native_tool_calls(content)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["name"], "read")
        self.assertEqual(calls[1]["name"], "glob")

    def test_dsml_boolean(self):
        content = (
            '<｜｜DSML｜｜invoke name="grep">'
            '<｜｜DSML｜｜parameter name="ignore_case" string="false">true</｜｜DSML｜｜parameter>'
            '</｜｜DSML｜｜invoke>'
        )
        calls = LLMClient._parse_native_tool_calls(content)
        self.assertTrue(calls[0]["args"]["ignore_case"])

    # --- Qwen / Hermes ---

    def test_qwen_single(self):
        content = '<tool_call>\n{"name": "grep", "arguments": {"pattern": "test"}}\n</tool_call>'
        calls = LLMClient._parse_native_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "grep")
        self.assertEqual(calls[0]["args"]["pattern"], "test")
        self.assertTrue(calls[0]["id"].startswith("qwen_"))

    def test_qwen_multiple(self):
        content = (
            '<tool_call>{"name": "read", "arguments": {"path": "a.py"}}</tool_call>\n'
            '<tool_call>{"name": "glob", "arguments": {"pattern": "*.md"}}</tool_call>'
        )
        calls = LLMClient._parse_native_tool_calls(content)
        self.assertEqual(len(calls), 2)

    def test_qwen_truncated(self):
        content = '<tool_call>\n{"name": "read", "arguments": {"path": "file.py"}}'
        calls = LLMClient._parse_native_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "read")

    def test_qwen_parameters_key(self):
        content = '<tool_call>{"name": "grep", "parameters": {"pattern": "x"}}</tool_call>'
        calls = LLMClient._parse_native_tool_calls(content)
        self.assertEqual(calls[0]["args"]["pattern"], "x")

    # --- Llama 3.1+ ---

    def test_llama_call_format(self):
        content = '<|python_tag|>grep.call(pattern="provider", path="src/main.py")'
        calls = LLMClient._parse_native_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "grep")
        self.assertEqual(calls[0]["args"]["pattern"], "provider")
        self.assertEqual(calls[0]["args"]["path"], "src/main.py")

    def test_llama_call_typed_args(self):
        content = '<|python_tag|>read.call(path="file.py", offset=100, limit=50)'
        calls = LLMClient._parse_native_tool_calls(content)
        self.assertEqual(calls[0]["args"]["offset"], 100)
        self.assertEqual(calls[0]["args"]["limit"], 50)

    def test_llama_call_multiple(self):
        content = '<|python_tag|>read.call(path="a.py")\nglob.call(pattern="*.md")'
        calls = LLMClient._parse_native_tool_calls(content)
        self.assertEqual(len(calls), 2)

    def test_llama_json_format(self):
        content = '<|python_tag|>{"name": "grep", "parameters": {"pattern": "test"}}'
        calls = LLMClient._parse_native_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["args"]["pattern"], "test")

    def test_llama_json_multiple(self):
        content = (
            '<|python_tag|>{"name": "read", "parameters": {"path": "a.py"}}\n'
            '{"name": "glob", "parameters": {"pattern": "*.md"}}'
        )
        calls = LLMClient._parse_native_tool_calls(content)
        self.assertEqual(len(calls), 2)

    # --- Mistral ---

    def test_mistral_single(self):
        content = '[TOOL_CALLS] [{"name": "grep", "arguments": {"pattern": "test"}}]'
        calls = LLMClient._parse_native_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "grep")
        self.assertTrue(calls[0]["id"].startswith("mistral_"))

    def test_mistral_multiple(self):
        content = '[TOOL_CALLS] [{"name": "read", "arguments": {"path": "a.py"}}, {"name": "glob", "arguments": {"pattern": "*.md"}}]'
        calls = LLMClient._parse_native_tool_calls(content)
        self.assertEqual(len(calls), 2)

    # --- No markers ---

    def test_plain_text(self):
        self.assertEqual(LLMClient._parse_native_tool_calls("plain text"), [])

    def test_empty(self):
        self.assertEqual(LLMClient._parse_native_tool_calls(""), [])


class TestValidateNativeCalls(unittest.TestCase):
    """Test _validate_native_calls — false positive prevention."""

    def test_valid_tool_passes(self):
        calls = [{"id": "x", "name": "grep", "args": {"pattern": "x"}}]
        valid = LLMClient._validate_native_calls(calls, SAMPLE_TOOLS)
        self.assertEqual(len(valid), 1)

    def test_unknown_tool_filtered(self):
        calls = [{"id": "x", "name": "fake_tool", "args": {}}]
        valid = LLMClient._validate_native_calls(calls, SAMPLE_TOOLS)
        self.assertEqual(len(valid), 0)

    def test_mixed_keeps_valid(self):
        calls = [
            {"id": "1", "name": "grep", "args": {}},
            {"id": "2", "name": "nonexistent", "args": {}},
            {"id": "3", "name": "read", "args": {}},
        ]
        valid = LLMClient._validate_native_calls(calls, SAMPLE_TOOLS)
        self.assertEqual(len(valid), 2)
        self.assertEqual(valid[0]["name"], "grep")
        self.assertEqual(valid[1]["name"], "read")

    def test_empty_calls(self):
        self.assertEqual(LLMClient._validate_native_calls([], SAMPLE_TOOLS), [])

    def test_discussion_false_positive(self):
        """Model discussing tool_call format should not trigger."""
        text = '<tool_call>{"name": "my_custom_func", "arguments": {"q": "hello"}}</tool_call>'
        calls = LLMClient._parse_native_tool_calls(text)
        valid = LLMClient._validate_native_calls(calls, SAMPLE_TOOLS)
        self.assertEqual(len(valid), 0)


class TestStripNativeMarkers(unittest.TestCase):

    def test_strip_tool_call(self):
        self.assertEqual(LLMClient._strip_native_markers("Hello\n<tool_call>..."), "Hello")

    def test_strip_tool_calls(self):
        self.assertEqual(LLMClient._strip_native_markers("Check this.\n[TOOL_CALLS] [...]"), "Check this.")

    def test_strip_python_tag(self):
        self.assertEqual(LLMClient._strip_native_markers("Hello\n<|python_tag|>x"), "Hello")

    def test_strip_dsml(self):
        self.assertEqual(LLMClient._strip_native_markers("Text\n<｜｜DSML｜｜invoke>"), "Text")

    def test_only_markers_returns_none(self):
        self.assertIsNone(LLMClient._strip_native_markers("<tool_call>{...}</tool_call>"))

    def test_closing_tags_cleaned(self):
        result = LLMClient._strip_native_markers("Hi</tool_call>")
        self.assertEqual(result, "Hi")


class TestRepairJson(unittest.TestCase):

    def test_valid_json(self):
        result = LLMClient._repair_json('{"key": "value"}')
        self.assertEqual(result, {"key": "value"})

    def test_trailing_comma(self):
        result = LLMClient._repair_json('{"key": "value",}')
        self.assertEqual(result, {"key": "value"})

    def test_unclosed_brace(self):
        result = LLMClient._repair_json('{"key": "value"')
        self.assertEqual(result, {"key": "value"})

    def test_unparseable_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            LLMClient._repair_json("not json at all")


if __name__ == "__main__":
    unittest.main()
