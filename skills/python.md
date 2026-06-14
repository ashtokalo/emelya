---
description: Python development — venv, pip, testing, linting, project structure
---
# Python Development

## Virtual environments

Always check for an existing venv before creating one:
- `ls -d .venv venv env 2>/dev/null` — find existing venv
- `python3 -m venv .venv` — create new venv
- `source .venv/bin/activate && pip install -r requirements.txt` — activate and install deps

When running commands, prefer `.venv/bin/python` or `.venv/bin/pip` directly over activating.

## Package management

- `pip install <package>` — install package
- `pip install -e .` — install project in editable mode
- `pip freeze > requirements.txt` — snapshot dependencies
- `pip install -r requirements.txt` — install from requirements

## Running and testing

- `python3 script.py` — run a script
- `python3 -m pytest` — run tests (discovers test_*.py files)
- `python3 -m pytest -x -v tests/test_foo.py::test_name` — run single test, stop on first failure
- `python3 -m pytest --tb=short` — shorter tracebacks

## Linting and formatting

- `ruff check .` — fast linter (preferred)
- `ruff format .` — auto-format code
- `flake8 src/` — traditional linter
- `python3 -m py_compile script.py` — quick syntax check

## Project structure

Check for project config to understand the setup:
- `pyproject.toml` — modern config (PEP 621), check `[project]` and `[tool.*]` sections
- `setup.py` / `setup.cfg` — legacy packaging
- `tox.ini` / `noxfile.py` — test automation

Use read tool to inspect config files. Use edit tool for modifications. Use bash tool for all commands.