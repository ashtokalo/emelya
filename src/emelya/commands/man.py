"""REPL /man command — show tool manual."""

from emelya.tools.man import execute as man_execute


def _cmd_man(self, arg: str = ""):
    if not arg:
        from emelya.tools.man import _TOOLS_DIR
        available = sorted(
            p.stem.replace("_tool", "")
            for p in _TOOLS_DIR.glob("*.md")
        )
        print(f"  Usage: /man <tool>")
        print(f"  Available: {', '.join(available)}")
        return
    result = man_execute({"tool": arg}, None)
    print(f"\n{result}")
