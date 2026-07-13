"""REPL /context command."""


def _cmd_context(self):
    tokens = self.context.estimate_tokens()
    n_msgs = len(self.context.messages)
    print(f"  Context: {tokens} tokens, {n_msgs} messages (limit: {self.config.context_limit})")
