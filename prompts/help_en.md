  Emelya — CLI AI Agent

  Just type a task or question. Emelya uses tools to work with files,
  code, archives, documents, and external services.

  Use @path/to/file to reference a file (Tab for completion).
  Multi-line paste from clipboard collapses into a placeholder.

  Commands:

  /help                  — this help
  /clear                 — clear history, start fresh session
  /compact               — compress context (model summarizes history)
  /undo                  — undo last file change
  /drop N                — remove message N from context (/history for numbers)

  /rename <name>         — rename current session
  /resume [name]         — resume a previous session (no args = list)
  /branch [name]         — branch conversation at this point into a new session
  /export [path]         — export conversation to a text file

  /model                 — list models
  /model name            — switch model
  /model add name url model [key]  — add model

  /skill                 — list skills
  /skill name            — load and apply skill
  /agent                 — list agents
  /agent name task       — run agent with task

  /tools                 — show loaded tools
  /mcp                   — MCP server status
  /mcp add name command  — connect MCP server

  /commands              — list custom commands
  /mycmd [args]          — run a custom command (from .emelya/commands/mycmd.md)

  /stats                 — token stats (session + project)
  /context               — current context size
  /history               — full context dump
  /exit                  — exit
  !<command>             — run a shell command directly (interactive TTY supported)

  Tips:

  - The model picks tools and agents automatically
  - For long tasks the model may call agents (subprocesses with isolated context)
  - Project config: emelya.md (repo) and .emelya/config.md (personal)
  - MCP servers connect lazily — on first tool call
  - /compact helps when context grows and model slows down
  - Custom commands: add .md files to .emelya/commands/ (use {args} as placeholder)
  - Memory: add .md files to .emelya/memory/ — indexed automatically by their first heading
