"""CLI subcommand dispatcher."""

import sys
from pathlib import Path


def _extract_work_dir(argv: list) -> Path:
    for i, a in enumerate(argv):
        if a == "--work-dir" and i + 1 < len(argv):
            return Path(argv[i + 1]).resolve()
        if a.startswith("--work-dir="):
            return Path(a.split("=", 1)[1]).resolve()
    return Path(".").resolve()


def dispatch(argv: list, scaffold_fn=None) -> bool:
    """Handle CLI subcommands. Returns True if handled, False to fall through to REPL."""
    non_flag = [a for a in argv if not a.startswith("-")]

    if non_flag and non_flag[0] == "help" or argv and argv[0] in ("--help", "-h"):
        from emelya.cli.help import run
        run()
        return True

    if "mcp" not in non_flag:
        return False

    mcp_idx = argv.index("mcp")
    post_mcp = argv[mcp_idx + 1:]
    work_dir = _extract_work_dir(argv[:mcp_idx])
    subcmd = post_mcp[0] if post_mcp else ""

    if subcmd == "add":
        if scaffold_fn:
            scaffold_fn(work_dir)
        from emelya.cli.mcp_add import run
        run(work_dir, post_mcp[1:])
    elif subcmd == "list":
        from emelya.cli.mcp_list import run
        run(work_dir)
    elif subcmd == "remove":
        from emelya.cli.mcp_remove import run
        run(work_dir, post_mcp[1] if len(post_mcp) > 1 else "")
    elif subcmd == "call":
        from emelya.cli.mcp_call import run
        run(work_dir, post_mcp[1:])
    elif subcmd in ("help", "--help", ""):
        from emelya.cli.mcp_help import run
        run()

    return True
