# Emelya — CLI AI Agent for Local and Cloud LLMs

Interactive command-line AI agent that works with local and cloud models via OpenAI-compatible API.
Designed for programming, file analysis, debugging, and general-purpose tasks.

## Key Features

- **Multi-model** — supports llama.cpp (local), OpenAI, DeepSeek, OpenRouter, and any OpenAI-compatible API
- **Any model** — works with Qwen, Llama, Mistral, Gemma, GPT, DeepSeek, and more
- **Model routing** — failover chains per operation (planner → cloud, agents → local) with `/model auto`
- **Agent auto-classification** — read-only agents routed as `agent_light`, write agents as `agent`
- **Tool calling** — structured function calling, not text parsing
- **Interactive sandbox grants** — approve an external file once or for the session
- **Confirmation feedback** — deny an operation with an optional comment for the model
- **Work context isolation** — tool calls don't pollute the main context (critical for 8-32K models)
- **Auto-detect context limit** — reads llama.cpp `/props` endpoint; per-model `max_tokens` (default 8192)
- **Compact tool descriptions** — parameter descriptions stripped for small models (<32K context)
- **Plan mode** — structured planning before execution (`plan_mode` tool)
- **Task progress tracking** — multi-step progress reporting (`task` tool)
- **Web search** — DuckDuckGo search via `web_search` tool
- **Consecutive error detection** — after 3 errors in a row, forces the model to change approach
- **Dynamic tool loading** — extension tools loaded on demand via `load_tools`
- **MCP support** — connect external services with lazy loading and cached descriptions
- **Sessions** — save/load/autosave conversations
- **i18n** — Russian and English interface (auto-detected)
- **Interactive line editor** — history, `@path` completion, paste block support
- **Colored output** — themed terminal output with wait spinner and timer
- **Pure Python** — stdlib only, no pip dependencies

## Quick Start

```bash
# Local model via llama.cpp
llama-server -m model.gguf -c 8192
./run.sh

# Use a specific configured model
./run.sh --use deepseek

# With options
./run.sh --host 192.168.1.100 --port 9090 --work-dir /path/to/project
./run.sh --confirm none --no-stream --context-limit 32000
```

## Multi-Model Support

Configure models in `models.json`:

```json
{
  "models": {
    "deepseek": {
      "base_url": "https://api.deepseek.com",
      "model": "deepseek-chat",
      "api_key": "sk-..."
    },
    "openrouter": {
      "base_url": "https://openrouter.ai/api",
      "model": "anthropic/claude-3.5-sonnet",
      "api_key": "sk-or-..."
    }
  },
  "routing": {
    "default": ["local"],
    "planner": ["deepseek", "local"],
    "agent": ["deepseek", "local"],
    "agent_light": ["local"]
  }
}
```

The `local` model is always available (configured via CLI args `--host`/`--port`). File is stored at `.emelya/models.json`.

Switch models at runtime with `/model` command or at startup with `--use NAME`.

The prompt displays the active model name: `Emelya(local, 350 tok):` or `Emelya(deepseek, 350 tok):`.

### Model Routing

Routing assigns different models to different operations via failover chains:

| Operation | Description |
|-----------|-------------|
| `default` | Main conversation loop |
| `planner` | Planner agent (needs strong reasoning) |
| `agent` | Write agents (edit, bash, etc.) |
| `agent_light` | Read-only agents (explore, grep, read only) |

Agents are **auto-classified** by their tools: if an agent only uses read-only tools (read, glob, grep), it's routed as `agent_light`. Agents with write tools (edit, bash) are routed as `agent`.

- `/model auto` — enable routing (use chains from models.json)
- `/model <name>` — manual override (disables routing, all operations use this model)

### GigaChat, YandexGPT and other non-OpenAI providers

Providers with incompatible APIs (GigaChat, YandexGPT, etc.) can be connected via [LiteLLM](https://github.com/BerriAI/litellm) proxy:

```bash
pip install litellm

# GigaChat (Sber)
GIGACHAT_API_KEY=... litellm --model gigachat/GigaChat-Pro --port 4000

# YandexGPT
YANDEX_API_KEY=... litellm --model yandex/yandexgpt --port 4000
```

Then in emelya:
```
/model add gigachat http://localhost:4000 GigaChat-Pro
/model add yandex http://localhost:4000 yandexgpt
```

LiteLLM supports 100+ providers and exposes a standard OpenAI-compatible API.

## Tools

### Core (always available, 14 tools)

| Tool | Description |
|------|-------------|
| `read` | Read text files with line numbers |
| `edit` | Atomically apply one or more exact changes across files |
| `bash` | Execute shell commands (read-only auto-approved) |
| `glob` | Find files by name pattern |
| `grep` | Search file contents with regex |
| `list_dir` | List directory with file sizes |
| `web_fetch` | Fetch URL, convert HTML to text |
| `web_search` | Search the web via DuckDuckGo |
| `skill` | Load specialized instructions |
| `agent` | Run sub-agent for focused tasks |
| `task` | Report progress on multi-step work |
| `plan_mode` | Enter structured planning before execution |
| `man` | Show detailed help for any tool |
| `load_tools` | Dynamically load extension tools |

### Extensions (loaded via `load_tools`, 6 tools in 4 categories)

| Category | Tools | Description |
|----------|-------|-------------|
| `documents` | doc_read, sheet_read | Office documents (docx, odt, pdf, xlsx, ods, csv) |
| `binary` | hex_read, hex_write | Binary file inspection and patching |
| `archives` | archive | Archive listing/extraction (zip, tar, gz, 7z, rar, deb) |
| `planning` | plan_step | Add steps to a plan (used by planner agent) |

### MCP (external services)

Connect any MCP server. Tools are cached and loaded lazily:

```
/mcp add playwright npx -y @playwright/mcp@latest
/mcp add myapi python3 ./my_server.py --env API_KEY=xxx
```

## Agents (16)

Sub-agents run in isolated context with limited tools. The main context only receives the final result.
Supports nesting up to 3 levels deep.

**Code exploration:**
explore_code, find_definition, find_usages, find_related, explore_dir, summarize_file, explore

**Analysis & review:**
explain_code, explain_error, review_file, code_review, security_review, lint_check

**Planning:**
planner

**Binary & modification:**
analyze_binary, add_docstrings

## Skills (15)

Instructions loaded into context on demand. Model decides when to load them.

**Languages:** python, javascript, php, go, c_cpp, rust

**Web:** html, css

**DevOps:** docker, makefile, bash_scripting, git

**Other:** data (JSON/YAML/CSV), design (SOLID/patterns), project (README/CHANGELOG/LICENSE)

## Commands

| Command | Description |
|---------|-------------|
| `/clear` | Clear conversation history, start fresh session |
| `/compact` | Summarize context via LLM |
| `/undo` | Undo last file modification |
| `/rename <name>` | Rename current session |
| `/resume [name]` | Resume a previous session (no args = list) |
| `/branch [name]` | Branch conversation into a new session |
| `/export [path]` | Export conversation to a text file |
| `/model [name]` | Switch active model (no args = list models) |
| `/skill [name]` | Load a skill into context (no args = list skills) |
| `/agent <name> <task>` | Run a sub-agent with a task |
| `/drop` | Drop last message pair from context |
| `/stats` | Show session statistics |
| `/tools` | Show loaded tools and extensions |
| `/mcp` | MCP server status |
| `/mcp add <name> <cmd>` | Add MCP server |
| `/mcp remove <name>` | Remove MCP server |
| `/mcp refresh [name]` | Reconnect and update cache |
| `/mcp tools <name>` | List server tools with parameters |
| `/mcp call <name> <tool> [args]` | Call MCP tool directly (JSON or plain text) |
| `/context` | Show context size |
| `/history` | Dump full context |
| `/exit` | Exit |
| `!<command>` | Run directly; output is stored in session memory and referenced from conversation context |
| `!|<command>` | Pipe the last raw model response to the command's stdin, e.g. `!|glow` |

## CLI Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--use` | | Use a named model from `models.json` |
| `--resume` | | Resume a previous session by name |
| `--host` | localhost | llama.cpp server host |
| `--port` | 8080 | llama.cpp server port |
| `--model` | default | Model name |
| `--temperature` | 0.7 | Generation temperature |
| `--max-tokens` | 8192 | Max tokens per response (per-model override in models.json) |
| `--context-limit` | 8000 | Context window budget (auto-detected from llama.cpp `/props`) |
| `--work-dir` | . | Working directory (sandbox root) |
| `--max-iterations` | 10 | Max tool-call iterations per query |
| `--no-stream` | | Disable streaming |
| `--confirm` | all | Bash confirm mode: all, write, none |

## Architecture

```
src/emelya/                   — Python package, ~9700 lines, stdlib only
├── __init__.py               — package init, version
├── __main__.py               — entry point
├── config.py                 — CLI args
├── repl.py                   — REPL + agent loop
├── models.py                 — multi-model management, routing, failover
├── mcp_manager.py            — MCP server lifecycle, /mcp commands
├── commands.py               — /command handling, session management, UI
├── llm.py                    — HTTP client (urllib) with streaming, native format parsing
├── context.py                — message history, trimming, save/load
├── sandbox.py                — path validation, suggest_path
├── log.py                    — detailed session logging
├── mcp.py                    — MCP client (stdio JSON-RPC)
├── i18n.py                   — internationalization (ru/en)
├── theme.py                  — colored terminal output
├── line_editor.py            — interactive line editor with history and @path completion
├── input_history.py          — input history persistence
└── tools/                    — tool registry + 21 tools
    ├── __init__.py            — ToolRegistry (core/extension, parameter validation)
    ├── read.py, edit.py
    ├── bash.py, glob_tool.py, grep.py, list_dir.py
    ├── web_fetch.py, web_search.py, skill.py, agent.py, load_tools.py
    ├── task.py, plan_mode.py, man.py
    ├── doc_read.py, sheet_read.py        (ext: documents)
    ├── hex_read.py, hex_write.py         (ext: binary)
    ├── archive.py                        (ext: archives)
    └── plan_step.py                      (ext: planning)

.emelya/models.json           — multi-model configuration (per-project)
prompts/system.md             — system prompt template
skills/*.md                   — 15 skill files
agents/*.md                   — 16 agent templates
docs/                         — architecture documentation
.emelya/                      — per-project data (created at runtime)
├── sessions/                 — saved sessions (context.json + log.txt)
├── mcp.json                  — MCP server configuration
└── mcp_cache/                — cached MCP tool descriptions
```

### Context Management

The agent uses **work context isolation**: when the model calls tools, the tool calls and results
live in a temporary work context. Only the user message and final text response are saved to the
main context. This is critical for small models with 8-32K context windows.

Work context is bounded by `--max-iterations` (default 10). Progressive nudges warn the model
when iterations are running out, and the last step forces a final answer.

### Token Budget (typical 8K model)

```
System prompt:           ~350 tokens (always)
Core tools (13):        ~1500 tokens (always, compact mode for <32K context)
Per conversation turn:   ~100 tokens (user + assistant only, no tool results)
Extension tools:        ~200-300 tokens (only when loaded)
```

## Project Configuration

Place `emelya.md` in the working directory for project-specific instructions:

```markdown
Use виртуальное окружение .venv.
Тесты запускай через pytest -x.
Линтинг — ruff check.
```

This file is NOT about emelya itself — it's instructions for the AI when working in YOUR project.

## Requirements

- Python 3.8+
- Any OpenAI-compatible API: llama.cpp, OpenAI, DeepSeek, OpenRouter, etc.
- Model must support tool calling (Qwen 2.5/3, Llama 3.1+, Mistral 7B v0.3+, GPT-4o, DeepSeek-V3, etc.)
