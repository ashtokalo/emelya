"""REPL /undo command."""


def _cmd_undo(self):
    result = self.tool_ctx.undo()
    print(f"  {result}")
