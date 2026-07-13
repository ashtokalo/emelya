"""REPL /drop command."""


def _cmd_drop(self, arg: str):
    if not arg or not arg.isdigit():
        print("  Usage: /drop <index> (use /history to see indexes)")
    else:
        result = self.context.drop(int(arg))
        print(f"  {result}")
