# Aigent — CLI AI Agent for Local LLMs

Interactive command-line AI agent that works with local models via llama.cpp.
Designed for programming, file analysis, debugging, and general-purpose tasks.

## Key Features

- **Fully local** — no cloud, no API keys, no internet required for core functionality
- **Any model** — works with any model running on llama.cpp server (Qwen, Llama, Mistral, Gemma, etc.)
- **Tool calling** — structured function calling, not text parsing
- **Work context isolation** — tool calls don't pollute the main context (critical for 8-32K models)
- **Dynamic tool loading** — extension tools loaded on demand via `load_tools`
- **MCP support** — connect external services with lazy loading and cached descriptions
- **Sessions** — save/load/autosave conversations
- **Pure Python** — stdlib only, no pip dependencies

## Quick Start

```bash
# Start llama.cpp server
llama-server -m model.gguf -c 8192

# Run aigent
./run.sh

# With options
./run.sh --host 192.168.1.100 --port 9090 --work-dir /path/to/project
./run.sh --confirm none --no-stream --context-limit 32000
```

## Tools

### Core (always available, 11 tools)

| Tool | Description |
|------|-------------|
| `read` | Read text files with line numbers |
| `write` | Create or overwrite files (requires read first) |
| `edit` | Find-and-replace by exact string match |
| `bash` | Execute shell commands with confirmation |
| `glob` | Find files by name pattern |
| `grep` | Search file contents with regex |
| `list_dir` | List directory with file sizes |
| `web_fetch` | Fetch URL, convert HTML to text |
| `skill` | Load specialized instructions |
| `agent` | Run sub-agent for focused tasks |
| `load_tools` | Dynamically load extension tools |

### Extensions (loaded via `load_tools`)

| Category | Tools | Description |
|----------|-------|-------------|
| `documents` | doc_read, sheet_read | Office documents (docx, odt, pdf, xlsx, ods, csv) |
| `binary` | hex_read, hex_write | Binary file inspection and patching |
| `archives` | archive | Archive listing/extraction (zip, tar, gz, 7z, rar, deb) |

### MCP (external services)

Connect any MCP server. Tools are cached and loaded lazily:

```
/mcp add playwright npx -y @playwright/mcp@latest
/mcp add myapi python3 ./my_server.py --env API_KEY=xxx
```

## Agents (14)

Sub-agents run in isolated context with limited tools. The main context only receives the final result.
Supports nesting up to 3 levels deep.

**Code exploration:**
explore_code, find_definition, find_usages, find_related, explore_dir, summarize_file

**Analysis & review:**
explain_code, explain_error, review_file, code_review, security_review, lint_check

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
| `/clear` | Clear conversation context |
| `/compact` | Summarize context via LLM |
| `/undo` | Undo last file modification |
| `/save [name]` | Save session |
| `/load [name]` | Load session (no args = list sessions) |
| `/new [name]` | Start new session |
| `/tools` | Show loaded tools and extensions |
| `/mcp` | MCP server status |
| `/mcp add <name> <cmd>` | Add MCP server |
| `/mcp remove <name>` | Remove MCP server |
| `/mcp refresh [name]` | Reconnect and update cache |
| `/context` | Show context size |
| `/history` | Dump full context |
| `/exit` | Exit |

## CLI Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--host` | localhost | llama.cpp server host |
| `--port` | 8080 | llama.cpp server port |
| `--model` | default | Model name |
| `--temperature` | 0.7 | Generation temperature |
| `--max-tokens` | 1024 | Max tokens per response |
| `--context-limit` | 8000 | Context window budget (tokens) |
| `--work-dir` | . | Working directory (sandbox root) |
| `--max-iterations` | 20 | Max tool-call iterations per query |
| `--no-stream` | | Disable streaming |
| `--confirm` | all | Bash confirm mode: all, write, none |

## Architecture

```
src/                          — 4100 lines Python, stdlib only
├── __main__.py               — entry point
├── config.py                 — CLI args
├── repl.py                   — REPL + agent loop + session management
├── llm.py                    — HTTP client (urllib) with streaming
├── context.py                — message history, trimming, save/load
├── sandbox.py                — path validation
├── log.py                    — detailed session logging
├── mcp.py                    — MCP client (stdio JSON-RPC)
└── tools/                    — tool registry + 16 tools
    ├── __init__.py            — ToolRegistry (core/extension separation)
    ├── read.py, write.py, edit.py
    ├── bash.py, glob_tool.py, grep.py, list_dir.py
    ├── web_fetch.py, skill.py, agent.py, load_tools.py
    ├── doc_read.py, sheet_read.py        (ext: documents)
    ├── hex_read.py, hex_write.py         (ext: binary)
    └── archive.py                        (ext: archives)

prompts/system.md             — system prompt template
skills/*.md                   — 15 skill files
agents/*.md                   — 14 agent templates
docs/                         — architecture documentation
.aigent/                      — per-project data (created at runtime)
├── sessions/                 — saved sessions (context.json + log.txt)
├── mcp.json                  — MCP server configuration
└── mcp_cache/                — cached MCP tool descriptions
```

### Context Management

The agent uses **work context isolation**: when the model calls tools, the tool calls and results
live in a temporary work context. Only the user message and final text response are saved to the
main context. This is critical for small models with 8-32K context windows.

### Token Budget (typical 8K model)

```
System prompt:           ~350 tokens (always)
Core tools (11):        ~1200 tokens (always)
Per conversation turn:   ~100 tokens (user + assistant only, no tool results)
Extension tools:        ~200-300 tokens (only when loaded)
```

## Project Configuration

Place `aigent.md` in the working directory for project-specific instructions:

```markdown
This is a Python 3.12 project using FastAPI.
Run tests with: pytest -x
Use ruff for linting.
```

## Requirements

- Python 3.8+
- llama.cpp server with a model that supports tool calling (Qwen 2.5/3, Llama 3.1+, Mistral 7B v0.3+)
