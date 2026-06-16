# Архитектура Emelya

## Цель

Консольный AI-агент для диалоговой работы с файлами (программирование, анализ, отладка).
Поддерживает локальные и облачные LLM через OpenAI-совместимый API (llama.cpp, OpenAI, DeepSeek, OpenRouter).

## Принципы

1. **Только стандартная библиотека Python** — никаких pip-зависимостей
2. **Код отдельно, тексты отдельно** — системные промпты, навыки, агенты в .md файлах
3. **Tool calling вместо парсинга текста** — OpenAI-совместимый API функций
4. **Multi-model** — конфигурация в models.json, переключение через /model или --use
5. **Минимум core tools** — базовый набор всегда в контексте, расширения через load_tools
6. **Work context isolation** — tool calls не попадают в основной контекст
7. **Безопасность** — sandbox для файловых операций, подтверждение команд

## Компоненты

```
┌─────────────────────────────────────────────────┐
│                     REPL                         │
│  Ввод → /commands или отправка в LLM             │
│  Сессии: save/load/autosave                      │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│                  Context                         │
│  system prompt + user/assistant messages          │
│  (tool calls живут в work context, не здесь)     │
│  trimming, compact, save/load                    │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│                 LLM Client                       │
│  HTTP → /v1/chat/completions (любой API)         │
│  стриминг (SSE) + обычный режим                  │
│  retry, JSON repair, SSL recovery, token counts  │
└──────────────────────┬──────────────────────────┘
                       │
              ┌────────┴────────┐
              ▼                 ▼
        tool_calls           content
              │                 │
              ▼                 ▼
┌──────────────────┐    Финальный ответ →
│  Tool Registry   │    основной контекст
│                  │
│  Core (11 tools) │
│  Extensions (5)  │
│  MCP (dynamic)   │
│                  │
│  ┌────────────┐  │
│  │ Sandbox    │  │
│  │ Undo stack │  │
│  └────────────┘  │
│                  │
│  результат ──────┼──→ work context → LLM (повтор)
└──────────────────┘
```

## Агентский цикл

```
User input
  → добавить в основной Context
  → отправить в LLM (messages + tools)
  → получить response
  │
  ├─ content (текст) → показать пользователю, сохранить в Context
  │
  └─ tool_calls → переключиться в Work Context:
       │
       │  Work Context = копия основного + tool_calls + results
       │  (временный, удаляется после завершения)
       │
       ├─ выполнить каждый tool call
       ├─ добавить results в Work Context
       ├─ отправить Work Context в LLM
       └─ повторять до content без tool_calls или лимита итераций
       │
       → в основной Context сохраняется ТОЛЬКО финальный текст
```

## Multi-Model

Конфигурация моделей в `models.json`. Каждая запись — именованный провайдер с host, port, api_key, model.
Поддерживаются llama.cpp (локальный), OpenAI, DeepSeek, OpenRouter и любой OpenAI-совместимый API.

- **CLI**: `--use NAME` — запуск с конкретной моделью
- **Команда**: `/model [NAME]` — переключение в рантайме (без аргументов — список моделей)
- **Промпт**: отображает активную модель: `Emelya(local, 350 tok):` или `Emelya(deepseek, 350 tok):`

### Edit tool limit

Для облачных моделей edit ограничен 30 строками на операцию. Это предотвращает обрезку JSON при генерации длинных old_string/new_string.

### Consecutive error detection

После 3 ошибок подряд система вставляет сообщение, заставляющее модель сменить подход.

### Required parameter validation

Валидация обязательных параметров централизована в ToolRegistry. При пропуске параметра возвращается информативное сообщение с указанием на возможную обрезку JSON.

## Структура файлов

```
emelya/
├── models.json                    — конфигурация моделей (multi-model)
├── src/                           — 4100 строк Python
│   ├── __main__.py                — точка входа
│   ├── config.py                  — CLI аргументы
│   ├── repl.py                    — REPL + агентский цикл + сессии + MCP управление
│   ├── llm.py                     — HTTP клиент (urllib, стриминг, SSL recovery)
│   ├── context.py                 — контекст (сообщения, trimming, save/load)
│   ├── sandbox.py                 — валидация путей
│   ├── log.py                     — детальное логирование сессий
│   ├── mcp.py                     — MCP клиент (stdio, JSON-RPC 2.0)
│   └── tools/
│       ├── __init__.py            — ToolRegistry (core/extension/MCP, parameter validation)
│       ├── read.py                — чтение файлов (core)
│       ├── write.py               — создание/перезапись (core)
│       ├── edit.py                — find-and-replace (core)
│       ├── bash.py                — shell команды (core)
│       ├── glob_tool.py           — поиск файлов (core)
│       ├── grep.py                — поиск содержимого (core)
│       ├── list_dir.py            — листинг каталога (core)
│       ├── web_fetch.py           — HTTP запросы (core)
│       ├── skill.py               — загрузка навыков (core)
│       ├── agent.py               — субагенты (core)
│       ├── load_tools.py          — загрузка расширений (core)
│       ├── doc_read.py            — офисные документы (ext: documents)
│       ├── sheet_read.py          — таблицы (ext: documents)
│       ├── hex_read.py            — hex dump (ext: binary)
│       ├── hex_write.py           — hex patch (ext: binary)
│       └── archive.py             — архивы (ext: archives)
│
├── prompts/
│   └── system.md                  — шаблон системного промпта
│
├── skills/                        — 15 навыков (.md)
│   ├── python.md, javascript.md, php.md, go.md, c_cpp.md, rust.md
│   ├── html.md, css.md
│   ├── docker.md, makefile.md, bash_scripting.md, git.md
│   └── data.md, design.md, project.md
│
├── agents/                        — 14 субагентов (.md)
│   ├── explore_code.md, find_definition.md, find_usages.md, find_related.md
│   ├── explore_dir.md, summarize_file.md, explain_code.md, explain_error.md
│   ├── review_file.md, code_review.md, security_review.md, lint_check.md
│   ├── analyze_binary.md, add_docstrings.md
│
├── docs/                          — документация
│
└── .emelya/                       — данные проекта (создаётся при запуске)
    ├── sessions/{name}/           — сохранённые сессии
    │   ├── context.json           — контекст диалога
    │   └── log.txt                — детальный лог
    ├── mcp.json                   — конфигурация MCP серверов
    └── mcp_cache/                 — кешированные описания MCP tools

```

## Управление контекстом

### Бюджет токенов (модель 8K)

```
System prompt:           ~350 токенов  (всегда)
Core tools (11):        ~1200 токенов  (всегда)
Итого база:             ~1550 токенов
Доступно на диалог:     ~6450 токенов

Extension tools:        +200-300 токенов  (только когда загружены)
MCP tools:              +100-200 токенов  (на категорию, из кеша)
```

### Work Context Isolation

При каждом запросе с tool calls создаётся временный work context:
- Копия основного контекста + tool_calls + results
- Может вырастать до нескольких тысяч токенов (результаты grep, read и т.д.)
- После завершения — удаляется
- В основной контекст попадает только: user message + финальный текстовый ответ

Это экономит 200-500 токенов на каждый запрос с tool calls.

### Trimming

Основной контекст: когда оценка токенов превышает context_limit, удаляются самые старые пары user/assistant. System prompt никогда не удаляется. Используются реальные token counts из `usage` если доступны.

Work context: автоматически обрезается при достижении 80% от context_limit. Удаляются самые старые tool call/result пары из work context, предотвращая переполнение при длинных цепочках tool calls.

### Compact

`/compact` — отправляет запрос модели суммировать диалог.
Результат заменяет все сообщения кроме system prompt.

## Сессии

```
/save myproject     → .emelya/sessions/myproject/context.json + log.txt
/load myproject     → восстанавливает контекст
/new [name]         → сохраняет текущую + начинает новую
```

Автосохранение: context.json сохраняется после каждого завершённого запроса.

По умолчанию имя сессии: `tmpYYYYMMDDHHMMSS`.

## MCP

### Жизненный цикл

1. **Конфиг** → `.emelya/mcp.json` (добавляется через `/mcp add`)
2. **Запуск** → при старте emelya читает конфиг, но НЕ подключается
3. **Кеш** → tool descriptions загружаются из `.emelya/mcp_cache/` (если есть)
4. **Регистрация** → MCP tools регистрируются как extension category `mcp:{name}`
5. **Lazy connect** → подключение к серверу происходит при первом вызове tool
6. **Refresh** → `/mcp refresh` переподключается и обновляет кеш

### Компактинг описаний

MCP серверы могут иметь длинные описания tools. При кешировании:
- Description обрезается до первого предложения
- Parameter descriptions обрезаются до 60 символов
- Убираются необязательные параметры из описания

## Безопасность

- **Sandbox** — все файловые tools валидируют пути (work_dir + /tmp)
- **Bash confirm** — 3 режима: all (каждая команда), write (только модифицирующие), none
- **Write guard** — обязательный read перед write/edit
- **Undo** — стек бэкапов, `/undo` откатывает последнюю операцию
- **Субагенты** — ограниченный набор tools, лимит шагов, лимит глубины (3)
- **MCP lazy loading** — tools не активны пока не загружены через load_tools

## Субагенты

### Вложенность

Поддерживается до 3 уровней: основной агент → субагент → субагент → субагент.
Tool `agent` автоматически добавляется в разрешённые tools субагента если глубина позволяет.
На максимальной глубине `agent` недоступен.

Выполнение последовательное — один llama.cpp сервер, один запрос за раз.

### Шаблоны

Каждый субагент — .md файл с frontmatter:
```yaml
---
description: What this agent does
tools: grep, read, glob      # allowed tools
max_steps: 6                  # iteration limit
---
Prompt template with {variables}...
```
