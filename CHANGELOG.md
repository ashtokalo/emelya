# Changelog

All notable changes to this project will be documented in this file.

## [0.5.0] - 2026-06-18

### Added
- **Human-readable tool display** — tool calls show localized messages: "Смотрю file.py" / "Reading file.py", "Ищу в файлах pattern" / "Searching for pattern", etc. Unified `● Action` format with i18n
- **Edit diff preview** — edit tool calls show a compact diff with line numbers, colored added/removed lines (green/red background), and summary ("Добавлено 3 строк, Удалено 1 строк")
- **Write preview** — write tool calls show first 10 lines of content with line numbers
- **Clean read display** — read results hide `[Lines N-M of K]` metadata, show only content lines
- **Relative paths** — tool display strips work_dir prefix for cleaner output
- **Suppressed redundant results** — "Replaced N occurrence(s)" and "Written N lines" no longer shown after diff/preview
- **`/export [path]`** — exports full conversation including all tool calls, diffs, and results to Markdown
- **`/branch [name]`** — creates a branch of the current conversation at this point
- **`/rename <name>`** — renames current session (was `/save`)
- **`/resume [name]`** — resumes a previous session (was `/load`); also `--resume SESSION` CLI arg
- **`/clear`** — now saves current session and starts fresh (was `/new`); old clear behavior merged in
- **Command name autocompletion** — `/re<Tab>` → `/rename`, `/resume`; all `/commands` completable
- **Semi-auto compact** — prompts user when context exceeds 75% of limit: compact, clear, or continue
- **Settings file** — `.emelya/settings.json` for persistent defaults (temperature, confirm, use, etc.); CLI args override
- **SIGTERM handler** — clean MCP shutdown on kill signal
- **Parallel tool execution** — read-only tools (read, grep, glob, list_dir, man, web_search) run in parallel via ThreadPoolExecutor
- **Grep timeout** — 10s deadline prevents pathological regex from hanging
- **Read limits** — `limit` capped at 2000 lines, long lines truncated at 500 chars
- **LRU eviction** — bash output cache limited to 20 entries

### Fixed
- **Security: command chaining bypass** — `_is_readonly` now blocks `;`, `&&`, `||` before pipe split
- **Security: SSRF** — `web_fetch` blocks loopback, RFC 1918, link-local IPs
- **Security: debug dumps** — removed full request dumps (contained prompts), added rotation (max 20 files), restricted directory permissions
- **Security: API keys** — `models.json` gets `chmod 0600` on write
- **agent.py summary routing** — step-limit summary now uses the routed `llm_client`, not `ctx.llm_client`
- **README: models.json** — fixed example to use actual keys (`base_url`/`model`, not `host`/`port`) with `{"models": {...}}` wrapper
- **README: max-iterations** — corrected default from 20 to 10
- **Removed duplicated code** — `is_binary()` extracted to `tools/__init__.py`; `strip_model_suffix()` extracted to `models.py`; removed `import time as _time` inside loop

### Changed
- **Package structure** — `src/*.py` → `src/emelya/*.py` proper Python package; `pip install -e .` now works via pyproject.toml
- **pyproject.toml** — version 0.5.0, classifiers, `[tool.setuptools.package-dir] "" = "src"`
- **218 tests** (was 45) — added tests for LLM client (mock HTTP), agent loop, commands, MCP, web_fetch, documents/archives, line_editor, i18n, log

## [0.4.0] - 2026-06-17

### Added
- **Native tool-call format fallback** — detects and parses native formats that leak through instead of OpenAI function calling: DeepSeek DSML, Qwen/Hermes `<tool_call>`, Llama 3.1+ `<|python_tag|>` (both `call()` and JSON), Mistral `[TOOL_CALLS]`. Parsed tool names validated against available tools to prevent false positives
- **Duplicate tool call detection** — repeated calls with identical arguments return an error instead of re-executing; works in main loop and sub-agents
- **Forced last step** — `tools=None` on the final iteration so models physically cannot ignore the LAST STEP nudge
- **Smart path suggestions** — `suggest_path()` now uses `rglob` to find files anywhere in the project; grep tool also suggests on path-not-found
- **Auto-strip `cd <work_dir>`** — redundant `cd /path && ` prefix is silently removed from bash commands
- **Auto-approve read-only bash commands** — extensive whitelist of safe commands (git/hg/svn read-only, ls, cat, grep, find, diff, jq, xxd, base64, man, linters for PHP/Python/JS/Go/Rust/C/C++/Ada/Ruby/Shell, version checks) — no confirmation prompt even in `--confirm all` mode
- **Bash output limit with cache** — `max_output` parameter (default 8192 chars, max 512K); truncated output is cached so re-running with larger limit doesn't re-execute the command
- **Display truncation** — tool results in terminal limited by lines (10), chars per line (200), and total chars (2000) to prevent screen flooding from grep/curl output
- **Direct MCP interaction** — `/mcp tools <name>` lists tools with descriptions and parameters; `/mcp call <name> <tool> [args]` executes tools without the model. Args can be JSON or plain text (auto-mapped to first parameter). JSON responses pretty-printed (≤100 lines) or compact. Tab completion for server names and tool names
- **REPL decomposition** — `repl.py` split into `models.py`, `mcp_manager.py`, `commands.py` via mixin pattern

## [0.3.0] - 2026-06-14

### Added
- **Model routing** — failover chains per operation in `models.json` `routing` key; `/model auto` enables, `/model <name>` overrides
- **Agent auto-classification** — agents with read-only tools (read, glob, grep) routed as `agent_light`; agents with write tools routed as `agent`; planner routed as `planner`
- **Routing operations**: `default`, `planner`, `agent`, `agent_light` — each with its own failover chain
- **Routed model display** — sub-agents show which model they're using (`via deepseek`)

### Fixed
- **Nudge placement** — nudges moved before LLM call (after tool results), preventing 400 errors when nudge broke tool_call→tool_result chain
- **400 error poisoning** — clean error summary saved to main context instead of orphaned tool_calls
- **Usage pollution** — work context LLM calls no longer update main context's last_usage (prevented incorrect trimming)

## [0.2.0] - 2026-06-14

### Added
- **New core tools**: web_search (DuckDuckGo), task (progress tracking), plan_mode (structured planning), man (detailed tool help) — core tools now 15
- **New extension tool**: plan_step in `planning` category — extension tools now 6 in 4 categories
- **New agents**: explore (general exploration), planner (plan creation) — agents now 16
- **New commands**: `/skill` (load skill into context), `/agent` (run sub-agent directly)
- **Plan mode** — model can enter structured planning before execution via `plan_mode` tool
- **Task progress tracking** — multi-step progress reporting via `task` tool
- **Web search** — DuckDuckGo HTML lite search via `web_search` tool
- **Compact tool descriptions** — parameter descriptions stripped for models with <32K context to save tokens
- **man tool** — detailed `.md`-based help for any tool, loaded on demand
- **i18n** — Russian and English interface, auto-detected from locale (`src/i18n.py`)
- **Colored output** — themed terminal output via `src/theme.py`
- **Interactive line editor** — custom line editor with input history, `@path` completion, and paste block support (`src/line_editor.py`, `src/input_history.py`)
- **Wait spinner with timer** — visual feedback during LLM requests
- **Auto-detect context limit** — reads `n_ctx` from llama.cpp `/props` endpoint
- **Per-model max_tokens** — default raised to 8192, configurable per model in `models.json`

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
