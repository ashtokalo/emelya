#!/usr/bin/env python3
"""Emelya — CLI AI Agent for local LLMs."""

import sys
from pathlib import Path

# Ensure src/ is in Python path
src_dir = Path(__file__).parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from config import parse_args
from repl import REPL


def main():
    config = parse_args()
    config.work_dir.mkdir(parents=True, exist_ok=True)

    repl = REPL(config)
    repl.run()


if __name__ == "__main__":
    main()
