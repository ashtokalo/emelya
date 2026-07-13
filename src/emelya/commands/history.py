"""REPL /history command."""


def _print_full_context(self):
    msgs = self.context.get_messages()
    print(f"\n  === Context: {len(msgs)} messages, ~{self.context.estimate_tokens()} tokens ===\n")
    for i, msg in enumerate(msgs):
        role = msg["role"].upper()
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls")
        tool_call_id = msg.get("tool_call_id")

        if role == "TOOL":
            preview = content[:200] + "..." if len(content) > 200 else content
            print(f"  [{i}] TOOL (id={tool_call_id}):\n  {preview}\n")
        elif tool_calls:
            tc_summary = ", ".join(
                f"{tc['function']['name']}({tc['function'].get('arguments', '')[:80]})"
                for tc in tool_calls
            )
            if content:
                print(f"  [{i}] {role}: {content[:200]}")
            print(f"  [{i}] {role} tool_calls: {tc_summary}\n")
        else:
            preview = content[:500] + "..." if len(content) > 500 else content
            print(f"  [{i}] {role}: {preview}\n")
