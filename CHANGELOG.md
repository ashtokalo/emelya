# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0] - 2026-06-14

### Added
- Initial release
- **Core tools** (11): read, write, edit, bash, glob, grep, list_dir, web_fetch, skill, agent, load_tools
- **Extension tools** (5): doc_read, sheet_read (documents), hex_read, hex_write (binary), archive (archives)
- **Dynamic tool loading** via `load_tools` — extensions loaded on demand
- **Work context isolation** — tool calls don't pollute main context
- **Sub-agents** (14): explore_code, find_definition, find_usages, find_related, explore_dir, summarize_file, explain_code, explain_error, review_file, code_review, security_review, lint_check, analyze_binary, add_docstrings
- **Sub-agent nesting** up to 3 levels deep
- **Skills** (15): python, javascript, php, go, c_cpp, rust, git, docker, html, css, data, design, project, makefile, bash_scripting
- **Multi-model support** — llama.cpp, OpenAI, DeepSeek, OpenRouter, any OpenAI-compatible API
- **Per-model context limits** with auto-detection for known providers
- **MCP support** — stdio transport, lazy connection, cached descriptions, `/mcp add/remove/refresh`
- **Sessions** — `/save`, `/load`, `/new`, autosave after each request
- **Commands**: /clear, /compact, /undo, /drop, /save, /load, /new, /model, /tools, /mcp, /stats, /context, /history
- **Detailed logging** — full request/response dumps, tool execution timing, USAGE tracking
- **Token stats** — `/stats` with per-model and per-date breakdown
- **Undo** — file modification rollback stack
- **Path hints** — "Did you mean: src/repl.py?" for wrong paths
- **Consecutive error detection** — forces model to change approach after 3 errors
- **Progressive nudges** — warns model when iterations are running out
- **SSL error recovery** — graceful handling of stream interruptions
- **JSON repair** — handles truncated tool call arguments from cloud models
- **Project config** — `emelya.md` (repo) + `.emelya/config.md` (local)
- **45 unit tests** for core modules (context, sandbox, tools)
- MIT License
