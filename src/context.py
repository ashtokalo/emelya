"""Context manager — conversation history with trimming."""

import json
from pathlib import Path


class Context:
    """Manages conversation messages and token budget."""

    def __init__(self, context_limit: int = 8000):
        self.context_limit = context_limit
        self.messages = []  # list of message dicts
        self.system_prompt = None
        self.last_usage = None  # last usage dict from LLM response

    def set_system(self, content: str):
        self.system_prompt = {"role": "system", "content": content}

    def add_user(self, content: str):
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, message: dict):
        """Add assistant message (may contain content and/or tool_calls)."""
        self.messages.append(message)

    def add_assistant_text(self, content: str):
        self.messages.append({"role": "assistant", "content": content})

    def add_tool_result(self, tool_call_id: str, content: str):
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })

    def pop_last_user(self):
        """Remove the last user message if it's at the end (orphaned)."""
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()

    def drop(self, display_index: int) -> str:
        """Remove message by its display index (as shown in /history).
        Index 0 is system prompt (cannot be removed)."""
        if display_index == 0:
            return "Cannot remove system prompt"
        msg_index = display_index - 1  # offset for system prompt
        if msg_index < 0 or msg_index >= len(self.messages):
            return f"Invalid index: {display_index}"
        role = self.messages[msg_index].get("role", "?")
        self.messages.pop(msg_index)
        return f"Removed [{display_index}] {role}"

    def get_messages(self) -> list:
        """Return full message list for API (system + conversation)."""
        msgs = []
        if self.system_prompt:
            msgs.append(self.system_prompt)
        msgs.extend(self.messages)
        return msgs

    def clear(self):
        """Clear all messages (keep system prompt)."""
        self.messages.clear()

    def update_usage(self, usage: dict):
        """Update with real token counts from LLM response."""
        if usage:
            self.last_usage = usage

    def estimate_tokens(self) -> int:
        """Return token count: real if available, heuristic otherwise."""
        if self.last_usage and self.last_usage.get("prompt_tokens"):
            return self.last_usage["prompt_tokens"]
        # Fallback: rough estimate len(text) / 3.5
        total = 0
        if self.system_prompt:
            total += len(self.system_prompt["content"])
        for msg in self.messages:
            content = msg.get("content") or ""
            total += len(content)
            if msg.get("tool_calls"):
                total += len(json.dumps(msg["tool_calls"]))
        return int(total / 3.5)

    def trim(self):
        """Remove oldest messages to stay within context_limit."""
        while self.estimate_tokens() > self.context_limit and len(self.messages) > 1:
            # Find the first removable group:
            # A user message + its following assistant/tool messages
            remove_end = 1
            for i in range(1, len(self.messages)):
                if self.messages[i]["role"] == "user":
                    break
                remove_end = i + 1
            self.messages = self.messages[remove_end:]

    def compact_prompt(self) -> list:
        """Build a prompt asking LLM to summarize the conversation."""
        msgs = self.get_messages()
        msgs.append({
            "role": "user",
            "content": (
                "Summarize our conversation so far in 2-3 sentences. "
                "Focus on what was discussed, what was done, and what the current state is. "
                "Be concise."
            ),
        })
        return msgs

    def apply_compact(self, summary: str):
        """Replace all messages with a compact summary."""
        self.messages.clear()
        self.messages.append({
            "role": "user",
            "content": "[Previous conversation summary]",
        })
        self.messages.append({
            "role": "assistant",
            "content": summary,
        })

    def save(self, path: Path):
        """Save context to JSON file."""
        data = {
            "system_prompt": self.system_prompt,
            "messages": self.messages,
            "context_limit": self.context_limit,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, path: Path):
        """Load context from JSON file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        self.system_prompt = data.get("system_prompt")
        self.messages = data.get("messages", [])
        if "context_limit" in data:
            self.context_limit = data["context_limit"]
