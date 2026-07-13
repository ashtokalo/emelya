"""REPL /compact command."""

from emelya.i18n import t


def _cmd_compact(self):
    tokens_before = self.context.estimate_tokens()
    print(f"  {t('compacting')}")
    try:
        msgs = self.context.compact_prompt()
        response = self.llm.chat(
            messages=msgs,
            tools=None,
            temperature=0.3,
            max_tokens=300,
        )
        summary = response.get("content", "")
        if summary:
            self.context.apply_compact(summary)
            tokens_after = self.context.estimate_tokens()
            print(f"  Compacted: {tokens_before} -> {tokens_after} tokens")
        else:
            print("  Compaction failed: empty response")
    except Exception as e:
        print(f"  Compaction failed: {e}")
