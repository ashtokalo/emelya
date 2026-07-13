# Система субагентов (Agents)

## Концепция

Субагент — отдельный цикл inference с чистым контекстом, ограниченным набором tools
и жёстким лимитом шагов. Основной агент вызывает субагента как tool,
получает обратно только текстовый результат. Основной контекст не раздувается
промежуточными tool calls субагента.

```
Основной агент                    Субагент
────────────────                  ────────────────
user: "Где определена             prompt: "Найди определение
       функция parse?"                     parse в проекте"
  │                                 │
  ├─ agent(name="find_definition",  ├─ glob(**/*.py)
  │        args={symbol:"parse"})   ├─ grep(pattern="def parse")
  │                                 ├─ read(path="src/parser.py")
  │                                 └─ "Функция parse определена
  │                                      в src/parser.py:42..."
  │                                   │
  ← tool result: "Функция parse  ←────┘
     определена в src/parser.py:42..."
  │
  ├─ read(path="src/parser.py",
  │       offset=35, limit=20)
  └─ "Вот функция parse: ..."
```

## Каталоги субагентов

Субагенты загружаются из трёх источников в порядке приоритета:

```
встроенные  <app>/agents/        — стандартные агенты (поставляются с emelya)
глобальные  ~/.emelya/agents/    — пользовательские агенты для всех проектов
локальные   .emelya/agents/      — агенты конкретного проекта
```

При совпадении имени: локальный > глобальный > встроенный.

### Встроенные субагенты

```
<app>/agents/
├── explore_code.md          — системное исследование кодовой базы
├── find_definition.md       — поиск определения символа
├── find_usages.md           — поиск использований символа
├── find_related.md          — файлы связанные с данным (импорты)
├── explore_dir.md           — описание структуры каталога
├── summarize_file.md        — описание файла
├── explain_code.md          — объяснение кода
├── explain_error.md         — анализ ошибки/трейсбека
├── review_file.md           — ревью файла на баги и проблемы
├── code_review.md           — ревью git diff (между ветками, до коммита)
├── security_review.md       — проверка безопасности кода
├── lint_check.md            — запуск линтера для файла/каталога
├── analyze_binary.md        — анализ бинарного файла (ELF, архивы)
└── add_docstrings.md        — добавление docstrings
```

### Пользовательские субагенты

Создайте `.md`-файл в `~/.emelya/agents/` (глобально) или `.emelya/agents/` (в проекте):

```markdown
---
description: Проверка миграций базы данных
tools: read, grep, bash
max_steps: 8
---
Проверь безопасность миграции `{path}`.

1. Прочитай файл миграции
2. Найди потенциально опасные операции: DROP, ALTER, NOT NULL без DEFAULT
3. Проверь наличие индексов для FK
4. Запусти линтер: bash(command="sqlfluff lint {path}")

Верни список проблем или "OK, миграция безопасна".
```

После создания агент `db_migration_check` будет доступен через
`agent(name="db_migration_check", args={"path": "migrations/0042.sql"})`.

## Формат файла субагента

```markdown
---
description: Find where a symbol (function, class, variable) is defined
tools: glob, grep, read
max_steps: 6
---
Find the definition of `{symbol}` in the project.

Steps:
1. Use glob to find files with relevant extensions (.py, .js, .ts, etc.)
2. Use grep to search for definition patterns:
   - Python: "def {symbol}" or "class {symbol}"
   - JavaScript/TypeScript: "function {symbol}", "const {symbol}", "class {symbol}"
   - Go: "func {symbol}"
3. Use read to see the context around the definition

Return:
- File path and line number
- The full definition (signature + first few lines of body)
- Brief description of what the symbol does (2-3 sentences)

If the symbol is not found, say so explicitly.
```

### Frontmatter

| Поле | Обязательное | Default | Описание |
|------|-------------|---------|----------|
| `description` | да | — | Краткое описание (для списка в tool definition) |
| `tools` | да | — | Список tools, доступных субагенту (через запятую) |
| `max_steps` | нет | `5` | Максимум итераций агентского цикла |

### Тело

Промпт для субагента. Поддерживает подстановку `{переменная}`
из `args` при вызове tool `agent`.

Промпт отправляется как `user` message (не system).
Это работает лучше для маленьких моделей — они привыкли отвечать на запросы,
а не следовать сложным системным инструкциям.

## Определение параметров

Параметры субагента (поле `args` в tool `agent`) определяются
по переменным в шаблоне: `{symbol}` в тексте → параметр `symbol`.

При сканировании каталога `agents/` извлекаются имена переменных
из тела промпта. Они добавляются в JSON Schema tool `agent`.

Однако, так как все субагенты объединены в один tool `agent(name, args)`,
`args` определяется как свободный `object` — модель сама определяет какие
поля передавать на основе описания конкретного агента.

## Выполнение субагента

```python
def run_agent(self, name: str, args: dict) -> str:
    # 1. Загружаем шаблон
    template = load_agent_template(name)

    # 2. Подставляем аргументы
    prompt = template.body.format_map(args)

    # 3. Собираем ограниченный набор tools
    tools = tool_registry.get_tools(template.tools)

    # 4. Формируем начальный контекст (чистый)
    messages = [{"role": "user", "content": prompt}]

    # 5. Агентский цикл
    for step in range(template.max_steps):
        response = llm_client.chat(messages, tools=tools)

        # Добавляем ответ в локальный контекст
        messages.append(response.message)

        # Если нет tool_calls — финальный ответ
        if not response.tool_calls:
            return response.content

        # Выполняем tool calls
        for call in response.tool_calls:
            result = tool_registry.execute(call.name, call.args, context)
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": result
            })

    # 6. Лимит шагов — принудительный итог
    messages.append({
        "role": "user",
        "content": "Step limit reached. Provide your answer based on what you have found so far."
    })
    response = llm_client.chat(messages, tools=[])  # без tools
    return response.content or "Agent returned no result"
```

### Ключевые отличия от основного цикла

| | Основной агент | Субагент |
|---|---|---|
| System prompt | Да (из prompts/system.md) | Нет |
| Начальное сообщение | user input | Подготовленный prompt |
| Tools | Все (включая skill, agent) | Только из frontmatter |
| Лимит итераций | `--max-iterations` (20) | `max_steps` из шаблона (5) |
| Стриминг | Да (вывод в терминал) | Нет (внутренний процесс) |
| Контекст | Общий (весь диалог) | Изолированный (только prompt + tool results) |
| Может вызвать agent | Да | **Да**, если глубина < max_agent_depth (3) |
| Может вызвать skill | Да | **Нет** (skill не в списке tools) |
| Результат | Показывается пользователю | Возвращается как tool result |

## Безопасность

### Ограничение tools

Субагент получает **только** tools из frontmatter. Обычно это read-only
инструменты: `glob`, `grep`, `read`. Субагент для исследования кода
не может ничего модифицировать.

Если субагенту нужен `bash` — это должно быть явно указано в frontmatter
и осознанно одобрено при создании агента.

### Ограничение шагов

`max_steps` предотвращает зацикливание. Для исследовательских задач
достаточно 5-8 шагов. Для модификации — 3-5.

### Sandbox

Субагент использует тот же Sandbox что и основной агент.
Все файловые операции валидируются.

## Когда использовать субагентов

**Используй субагента:**
- Задача требует 3+ tool calls для исследования
- Промежуточные результаты (списки файлов, содержимое) не нужны в основном контексте
- Задача типовая и может быть описана шаблоном

**Не используй субагента:**
- Задача требует 1-2 tool calls (дешевле выполнить напрямую)
- Результат нужен в деталях (субагент возвращает только текстовый итог)
- Задача требует модификации файлов и пользовательского подтверждения

## Информирование пользователя

При запуске субагента — показать пользователю:
```
  [agent: find_definition] Searching for definition of "parse"...
```

При завершении — показать результат как часть ответа модели.
Промежуточные шаги субагента НЕ показываются пользователю
(в отличие от tool calls основного агента).

Опционально: флаг `--verbose` для отладки — показывать
все промежуточные шаги субагентов.

## Примеры субагентов

### find_usages.md

```markdown
---
description: Find all usages of a symbol in the project
tools: grep, read
max_steps: 4
---
Find all places where `{symbol}` is used in the project.

1. Use grep to find all occurrences: pattern="{symbol}", include relevant file types
2. For each unique file, use read to show the surrounding context (2-3 lines)

Return a structured list:
- File path, line number, and the line content
- Group by file
- Exclude the definition itself (import/include lines are OK)
- Show total count of usages
```

### summarize_dir.md

```markdown
---
description: Describe the structure and purpose of a directory
tools: glob, read
max_steps: 6
---
Describe the structure of directory `{path}`.

1. Use glob to list all files: pattern="{path}/**/*"
2. Identify the project type by file extensions and key files
   (package.json, setup.py, Cargo.toml, go.mod, etc.)
3. Read key files (README, main entry point) if they exist

Return:
- Project type / language
- Directory structure (tree-like, top 2 levels)
- Purpose of main directories
- Entry points
- Total file count by extension
```
