# Спецификация инструментов (Tools)

## Обзор

14 core tools (всегда в контексте) + extension tools (загружаются через load_tools)
+ MCP tools (внешние серверы, загружаются по категориям).

Extension tools определяются наличием `CATEGORY = "имя_категории"` в модуле.
MCP tools регистрируются автоматически из .emelya/mcp.json.

Каждый tool реализуется как Python-модуль в `src/tools/`, экспортирующий:
- `DEFINITION` — dict с описанием для API (name, description, parameters)
- `execute(args, context)` — функция выполнения, возвращает строку результата

`context` — объект `ToolContext` с полями:
- `work_dir: Path` — текущий рабочий каталог
- `sandbox: Sandbox` — валидатор путей
- `files_read: set` — множество прочитанных файлов (для защиты Write/Edit)
- `undo_stack: list[ChangeSet]` — хронологический стек одно- и многофайловых изменений
- `confirm: Callable` — функция запроса подтверждения у пользователя
- `skills_dirs: list[Path]` — упорядоченный список каталогов навыков (builtin → global → local)
- `agents_dirs: list[Path]` — упорядоченный список каталогов агентов (builtin → global → local)
- `hook_runner` — экземпляр `HookRunner` для запуска событийных хуков (см. `docs/hooks.md`)
- `llm_client` — LLM-клиент для субагентов
- `config` — конфигурация REPL
- `agent_depth: int` — текущая глубина вложенности субагентов
- `max_agent_depth: int` — максимально допустимая глубина (default: 3)

---

## 1. Read

Чтение текстовых файлов с нумерацией строк.

### Определение

```json
{
  "type": "function",
  "function": {
    "name": "read",
    "description": "Read a text file. Returns numbered lines. Read the file before editing it.",
    "parameters": {
      "type": "object",
      "properties": {
        "path": {
          "type": "string",
          "description": "File path (relative to working directory)"
        },
        "offset": {
          "type": "integer",
          "description": "Start from this line number (1-based, default: 1)"
        },
        "limit": {
          "type": "integer",
          "description": "Number of lines to read (default: 200)"
        }
      },
      "required": ["path"]
    }
  }
}
```

### Поведение

- Возвращает строки в формате `{номер}\t{содержимое}`
- Если файл не существует — ошибка
- Если файл бинарный (определяется по первым 8KB: наличие null-байтов) — ошибка с рекомендацией использовать `bash` + `xxd`/`hexdump`
- `offset` по умолчанию 1, `limit` по умолчанию 200
- В конце вывода добавляется строка: `[Lines {start}-{end} of {total}]`
- Путь проходит через Sandbox-валидацию
- Прочитанный файл добавляется в `context.files_read`

### Кодировка

Попытка чтения в UTF-8. При ошибке — fallback на latin-1.
Определение кодировки через BOM (UTF-8 BOM, UTF-16 LE/BE).

---

## 2. Edit

Атомарный набор точных изменений одного или нескольких файлов.

### Определение

```json
{
  "type": "function",
  "function": {
    "name": "edit",
    "description": "Atomically apply one or more exact text changes. Existing files MUST be read first.",
    "parameters": {
      "type": "object",
      "properties": {
        "changes": {"type": "array", "items": {"type": "object"}}
      },
      "required": ["changes"]
    }
  }
}
```

### Поведение

- Update/write/delete требуют предварительного Read, create — нет
- Поддерживаются ключи пути `update`, `write`, `create`, `delete`
- Все операции проверяются до первой записи
- Несколько update одного файла применяются последовательно
- Один вызов создаёт один ChangeSet для `/undo`
- Пути могут быть относительными или абсолютными внутри work directory;
  выход за границы Sandbox запрещён

### Примеры использования моделью

```json
{"changes": [{"update": "main.py", "old_string": "if x > 0:", "new_string": "if x >= 0:"}]}
```

---

## 3. Bash

Выполнение shell-команды.

### Определение

```json
{
  "type": "function",
  "function": {
    "name": "bash",
    "description": "Execute a shell command. Use for: ls, git, running scripts, installing packages, and any operation not covered by other tools. Prefer read/edit/glob/grep for file operations.",
    "parameters": {
      "type": "object",
      "properties": {
        "command": {
          "type": "string",
          "description": "Shell command to execute"
        },
        "description": {
          "type": "string",
          "description": "What this command does (shown to user for confirmation)"
        },
        "timeout": {
          "type": "integer",
          "description": "Timeout in seconds (default: 30, max: 300)"
        }
      },
      "required": ["command", "description"]
    }
  }
}
```

### Поведение

- Команда выполняется через `subprocess.run` с `shell=True`
- Рабочий каталог: `context.work_dir`
- Таймаут по умолчанию 30 секунд, максимум 300
- Вывод обрезается до 10000 символов (с пометкой `[truncated]`)
- stderr объединяется с stdout

### Безопасность

Все команды показываются пользователю с `description` для подтверждения.
Три режима подтверждения (настраивается):

1. **confirm_all** (default) — подтверждение для каждой команды
2. **confirm_write** — подтверждение только для команд, модифицирующих файлы.
   Read-only команды (ls, cat, grep, find, git status, git log, git diff, pwd, which,
   file, wc, head, tail, sort, uniq, du, df, echo, env, date, uname)
   выполняются автоматически.
3. **no_confirm** — без подтверждений (опасно, только для доверенных моделей)

Формат запроса подтверждения:
```
  bash: Показать статус git репозитория
  $ git status
  Разрешить (д), запретить (н) или запретить с комментарием (к)?
```

При обычном отказе модель получает явный tool-result о том, что команда не
выполнялась и изменений нет. При отказе с комментарием Emelya дополнительно
запрашивает текст пользователя и передаёт его модели в том же error-result,
чтобы она могла выбрать другой подход.

### Переменные окружения

Каждый вызов — новый процесс. Состояние shell не сохраняется между вызовами.
Переменные окружения наследуются от процесса emelya.

---

## 4. Glob

Поиск файлов по паттерну имени.

### Определение

```json
{
  "type": "function",
  "function": {
    "name": "glob",
    "description": "Find files by name pattern. Supports ** for recursive matching.",
    "parameters": {
      "type": "object",
      "properties": {
        "pattern": {
          "type": "string",
          "description": "Glob pattern (e.g. '**/*.py', 'src/**/*.js', '*.md')"
        },
        "path": {
          "type": "string",
          "description": "Base directory (default: working directory)"
        }
      },
      "required": ["pattern"]
    }
  }
}
```

### Поведение

- Использует `pathlib.Path.glob()` (или `rglob()` для `**/` паттернов)
- Результаты сортируются по пути
- Возвращает по одному пути на строку (относительно work_dir)
- Скрытые файлы/каталоги (начинающиеся с `.`) исключаются, кроме случаев когда паттерн явно начинается с `.`
- Каталог `.git` всегда исключается
- Лимит результатов: 500 путей. При превышении — пометка `[truncated, {total} total matches]`
- Путь `path` проходит через Sandbox-валидацию

---

## 5. Grep

Поиск по содержимому файлов.

### Определение

```json
{
  "type": "function",
  "function": {
    "name": "grep",
    "description": "Search file contents using regular expressions.",
    "parameters": {
      "type": "object",
      "properties": {
        "pattern": {
          "type": "string",
          "description": "Regular expression pattern"
        },
        "path": {
          "type": "string",
          "description": "File or directory to search (default: working directory)"
        },
        "include": {
          "type": "string",
          "description": "Glob pattern to filter files (e.g. '*.py')"
        },
        "ignore_case": {
          "type": "boolean",
          "description": "Case-insensitive search (default: false)"
        },
        "context": {
          "type": "integer",
          "description": "Number of context lines before and after match (default: 0)"
        },
        "max_results": {
          "type": "integer",
          "description": "Maximum number of matches (default: 50)"
        }
      },
      "required": ["pattern"]
    }
  }
}
```

### Поведение

- Рекурсивный поиск по каталогу (или в одном файле)
- Использует `re` модуль Python
- Бинарные файлы пропускаются (определяются по null-байтам в первых 8KB)
- Каталог `.git` всегда исключается
- Формат вывода: `{файл}:{строка}: {содержимое}`
- При `context > 0` — блоки разделяются `--`
- Ошибки чтения отдельных файлов молча пропускаются (логируются)
- При превышении `max_results` — пометка `[truncated, showing {max_results} of {total} matches]`

---

## 6. Skill

Загрузка навыка — специализированных инструкций для модели.

### Определение

```json
{
  "type": "function",
  "function": {
    "name": "skill",
    "description": "Load a skill with specialized instructions. Available skills: {skills_list}",
    "parameters": {
      "type": "object",
      "properties": {
        "name": {
          "type": "string",
          "description": "Skill name (e.g. 'git', 'data')"
        }
      },
      "required": ["name"]
    }
  }
}
```

### Поведение

- `{skills_list}` в description заполняется динамически при формировании tools —
  перечень доступных навыков из всех каталогов (builtin → global → local)
- Загружает .md файл: сначала ищет в локальном каталоге, затем глобальном, затем встроенном
- Содержимое файла возвращается как tool result — модель получает инструкции в контексте
- Если навык уже загружен в текущей сессии — возвращает `"Skill '{name}' is already loaded"`
- Если навык не найден — возвращает `"Unknown skill '{name}'. Available: {list}"`
- Подробнее — см. `skills.md`

---

## 7. Agent

Запуск субагента — отдельного цикла inference для изолированной задачи.

### Определение

```json
{
  "type": "function",
  "function": {
    "name": "agent",
    "description": "Run a sub-agent for a focused task. The agent runs in a separate context and returns only the result. Available agents: {agents_list}",
    "parameters": {
      "type": "object",
      "properties": {
        "name": {
          "type": "string",
          "description": "Agent name (e.g. 'find_definition', 'summarize_file')"
        },
        "args": {
          "type": "object",
          "description": "Arguments for the agent (depends on agent type)"
        }
      },
      "required": ["name", "args"]
    }
  }
}
```

### Поведение

- `{agents_list}` в description заполняется динамически из всех каталогов (builtin → global → local)
- Загружает .md файл: сначала ищет в локальном каталоге, затем глобальном, затем встроенном
- Запускает отдельный агентский цикл (свой контекст, свой набор tools)
- Возвращает финальный текстовый ответ субагента как tool result
- Подробнее — см. `agents.md`

---

## Tool Registry

### Инициализация

При запуске:
1. Импортируются все модули из `src/tools/`
2. Из каждого берётся `DEFINITION` и `execute`
3. Для `skill` tool — сканируется каталог `skills/`, формируется список в description
4. Для `agent` tool — сканируется каталог `agents/`, формируется список в description

### Формирование массива tools для API

```python
def get_tools(self, names: list[str] | None = None) -> list[dict]:
    """Возвращает массив tool definitions для отправки в LLM API.
    Если names=None — все зарегистрированные tools.
    Если names — только указанные (для субагентов)."""
```

### Выполнение tool call

```python
def execute(self, name: str, args: dict, context: ToolContext) -> str:
    """Выполняет tool call, возвращает строку результата.
    Все ошибки ловятся и возвращаются как строка 'Error: ...'"""
```

---

## Sandbox

### Валидация путей

Все файловые tools (read, edit, glob, grep) валидируют пути через Sandbox:

- пути внутри work directory и заранее разрешённых областей доступны сразу;
- для внешнего пути пользователь выбирает: разрешить один tool call,
  разрешить на всю текущую сессию или запретить;
- одноразовый доступ действует до завершения текущего tool call;
- разрешение директории распространяется на её содержимое;
- разрешения сессии не сохраняются после выхода из Emelya.

```python
class Sandbox:
    def __init__(self, work_dir: Path, extra_allowed: list = None):
        self.work_dir = work_dir.resolve()
        # extra_allowed — дополнительные разрешённые префиксы (абсолютные пути)

    def validate(self, path: str) -> Path:
        """Резолвит путь относительно work_dir.
        Проверяет что результат внутри work_dir или в extra_allowed.
        Возвращает абсолютный Path.
        Поднимает PermissionError если путь вне sandbox."""
```

Разрешённые пути по умолчанию:
- Рабочий каталог и всё внутри него
- `/tmp` (для временных файлов)
- `~/.emelya/memory/` — добавляется автоматически если каталог существует,
  чтобы модель могла читать глобальные файлы памяти

Запрещённые пути:
- Всё что выше рабочего каталога (`../`)
- Системные каталоги
- Symlink-и, ведущие за пределы sandbox (проверяется через `.resolve()`)

### extra_allowed

При необходимости разрешить доступ к дополнительным путям (например, в хуках или
при интеграционном тестировании), Sandbox принимает `extra_allowed`:

```python
sandbox = Sandbox(work_dir, extra_allowed=[Path("/var/data/shared")])
```

## Пользовательские инструменты

Extension tools можно добавлять через механизм загрузки категорий. Встроенные
extension tools лежат в `src/emelya/tools/` и определяются константой `CATEGORY`.

Для добавления собственного инструмента через MCP — см. `docs/mcp.md`.

## Хуки

Все вызовы bash, edit, а также вызовы любых tools оборачиваются хуками.
Хуки могут блокировать выполнение, подменять команды или фильтровать вывод.

Подробнее — см. `docs/hooks.md`.
