# Спецификация инструментов (Tools)

## Обзор

11 core tools (всегда в контексте) + 5 extension tools (загружаются через load_tools)
+ MCP tools (внешние серверы, загружаются по категориям).

Extension tools определяются наличием `CATEGORY = "имя_категории"` в модуле.
MCP tools регистрируются автоматически из .emelya/mcp.json.

Каждый tool реализуется как Python-модуль в `src/tools/`, экспортирующий:
- `DEFINITION` — dict с описанием для API (name, description, parameters)
- `execute(args, context)` — функция выполнения, возвращает строку результата

`context` — объект с полями:
- `work_dir: Path` — текущий рабочий каталог
- `sandbox: Sandbox` — валидатор путей
- `files_read: set` — множество прочитанных файлов (для защиты Write/Edit)
- `confirm: Callable` — функция запроса подтверждения у пользователя

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

## 2. Write

Создание нового файла или полная перезапись существующего.

### Определение

```json
{
  "type": "function",
  "function": {
    "name": "write",
    "description": "Create a new file or overwrite an existing file. You MUST read the file first before overwriting. Use edit for partial changes.",
    "parameters": {
      "type": "object",
      "properties": {
        "path": {
          "type": "string",
          "description": "File path (relative to working directory)"
        },
        "content": {
          "type": "string",
          "description": "Complete file content"
        }
      },
      "required": ["path", "content"]
    }
  }
}
```

### Поведение

- Если файл существует и НЕ был ранее прочитан через Read — ошибка:
  `"File exists but was not read first. Use read to see current content before overwriting."`
- Если файл новый — создаёт, включая промежуточные каталоги (mkdir -p)
- Запрос подтверждения у пользователя если файл существует
- Путь проходит через Sandbox-валидацию
- Возвращает: `"Written {n} lines to {path}"`

---

## 3. Edit

Поиск и замена фрагмента текста в файле. Основной инструмент для редактирования.

### Определение

```json
{
  "type": "function",
  "function": {
    "name": "edit",
    "description": "Find and replace exact text in a file. The old_string must match exactly (including whitespace and indentation). You MUST read the file first.",
    "parameters": {
      "type": "object",
      "properties": {
        "path": {
          "type": "string",
          "description": "File path (relative to working directory)"
        },
        "old_string": {
          "type": "string",
          "description": "Exact text to find (must be unique in the file)"
        },
        "new_string": {
          "type": "string",
          "description": "Replacement text"
        },
        "replace_all": {
          "type": "boolean",
          "description": "Replace all occurrences (default: false)"
        }
      },
      "required": ["path", "old_string", "new_string"]
    }
  }
}
```

### Поведение

- Файл ДОЛЖЕН быть предварительно прочитан через Read — иначе ошибка
- `old_string` ищется как точная подстрока в содержимом файла
- Если `replace_all: false` (default) и найдено больше одного вхождения — ошибка:
  `"Found {n} occurrences. Provide more context to make old_string unique, or use replace_all."`
- Если не найдено ни одного вхождения — ошибка:
  `"old_string not found in file. Make sure it matches exactly, including whitespace."`
- Запрос подтверждения у пользователя
- Возвращает: `"Replaced {n} occurrence(s) in {path}"`

### Примеры использования моделью

Удаление строки:
```json
{"path": "main.py", "old_string": "    debug_print(x)\n", "new_string": ""}
```

Вставка после строки:
```json
{"path": "main.py", "old_string": "import os", "new_string": "import os\nimport sys"}
```

Замена фрагмента:
```json
{"path": "main.py", "old_string": "    if x > 0:", "new_string": "    if x >= 0:"}
```

---

## 4. Bash

Выполнение shell-команды.

### Определение

```json
{
  "type": "function",
  "function": {
    "name": "bash",
    "description": "Execute a shell command. Use for: ls, git, running scripts, installing packages, and any operation not covered by other tools. Prefer read/write/edit/glob/grep for file operations.",
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
  Выполнить? (y/n):
```

### Переменные окружения

Каждый вызов — новый процесс. Состояние shell не сохраняется между вызовами.
Переменные окружения наследуются от процесса emelya.

---

## 5. Glob

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

## 6. Grep

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

## 7. Skill

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
  перечень доступных навыков из каталога `skills/`
- Загружает .md файл из каталога `skills/`
- Содержимое файла возвращается как tool result — модель получает инструкции в контексте
- Если навык уже загружен в текущей сессии — возвращает `"Skill '{name}' is already loaded"`
- Если навык не найден — возвращает `"Unknown skill '{name}'. Available: {list}"`
- Подробнее — см. `skills.md`

---

## 8. Agent

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

- `{agents_list}` в description заполняется динамически
- Загружает .md файл из каталога `agents/`
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

Все файловые tools (read, write, edit, glob, grep) валидируют пути через Sandbox:

```python
class Sandbox:
    def __init__(self, work_dir: Path):
        self.work_dir = work_dir.resolve()

    def validate(self, path: str) -> Path:
        """Резолвит путь относительно work_dir.
        Проверяет что результат внутри work_dir.
        Возвращает абсолютный Path.
        Поднимает PermissionError если путь вне sandbox."""
```

Разрешённые пути:
- Рабочий каталог и всё внутри него
- `/tmp` (для временных файлов)

Запрещённые пути:
- Всё что выше рабочего каталога (`../`)
- Системные каталоги
- Symlink-и, ведущие за пределы sandbox (проверяется через `.resolve()`)
