#!/usr/bin/env python3
"""Emelya — CLI AI Agent for local LLMs."""

import json
import sys
from pathlib import Path

# Ensure src/ is in Python path (for both direct run and package install)
src_dir = Path(__file__).parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from emelya.config import parse_args
from emelya.repl import REPL


def _scaffold_emelya(work_dir: Path):
    """Create .emelya directory with default resources on first run."""
    emelya_dir = work_dir / ".emelya"
    if emelya_dir.exists():
        return

    emelya_dir.mkdir(parents=True, exist_ok=True)

    # commands/ — user-defined /command shortcuts
    (emelya_dir / "commands").mkdir()

    # memory/ — project knowledge files (indexed automatically by first heading)
    (emelya_dir / "memory").mkdir()

    # mcp.json — MCP server configuration
    mcp_data = {"servers": {}}
    (emelya_dir / "mcp.json").write_text(
        json.dumps(mcp_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # models.json — named models and routing
    # 'local' is always the model from CLI args (--url / --port / --model).
    # Add named models here and adjust routing to use them per operation.
    models_data = {
        "models": {},
        "routing": {
            "default":      ["local"],
            "planner":      ["local"],
            "agent":        ["local"],
            "agent_light":  ["local"],
        },
    }
    models_path = emelya_dir / "models.json"
    models_path.write_text(
        json.dumps(models_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        models_path.chmod(0o600)
    except OSError:
        pass


def main():
    config = parse_args()
    config.work_dir.mkdir(parents=True, exist_ok=True)
    _scaffold_emelya(config.work_dir)

    repl = REPL(config)
    repl.run()


if __name__ == "__main__":
    main()
