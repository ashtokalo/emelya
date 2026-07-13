# MCP-серверы (Model Context Protocol)

## Обзор

MCP (Model Context Protocol) — стандарт для подключения внешних инструментов к LLM.
Emelya поддерживает MCP-серверы через stdio transport: сервер запускается как дочерний
процесс, взаимодействие идёт через stdin/stdout.

Инструменты MCP-сервера появляются в модели как обычные tools и вызываются ею
так же, как встроенные инструменты (read, bash, etc.).

---

## Конфигурационный файл

### Формат

```json
{
  "servers": {
    "имя_сервера": {
      "command": "команда",
      "args": ["аргумент1", "аргумент2"],
      "env": {
        "API_KEY": "значение"
      },
      "description": "Описание сервера (необязательно)"
    }
  }
}
```

### Поля сервера

| Поле | Тип | Обязательное | Описание |
|------|-----|-------------|----------|
| `command` | string | да | Исполняемый файл или команда |
| `args` | array | нет | Аргументы командной строки |
| `env` | object | нет | Дополнительные переменные окружения |
| `description` | string | нет | Описание для отображения в `/mcp` и tool definition |

### Пример

```json
{
  "servers": {
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp@latest"],
      "description": "Browser automation"
    },
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/docs"]
    },
    "myapi": {
      "command": "python3",
      "args": ["/path/to/server.py"],
      "env": {
        "API_KEY": "secret",
        "BASE_URL": "https://api.example.com"
      }
    }
  }
}
```

---

## Глобальная и локальная конфигурация

MCP-серверы настраиваются на двух уровнях:

```
~/.emelya/mcp.json        ← глобальный (общий для всех проектов)
.emelya/mcp.json          ← локальный (только для текущего проекта)
```

### Порядок слияния

1. Сначала загружается глобальный файл
2. Затем локальный — серверы с одинаковым именем перекрывают глобальные

```
Глобальный:  {"playwright": {...}, "filesystem": {...}}
Локальный:   {"playwright": {...custom...}, "myapi": {...}}
Итого:       {"playwright": <local>, "filesystem": <global>, "myapi": <local>}
```

Это позволяет подключить playwright глобально, но переопределить его настройки
в конкретном проекте.

### Кэш инструментов

Кэш хранится рядом с конфигурацией, которая определила сервер:

```
~/.emelya/mcp_cache/<server>.json   ← глобальный сервер
.emelya/mcp_cache/<server>.json     ← локальный сервер
```

Это гарантирует что глобальные серверы не требуют доступа к каталогу проекта,
а локальные — не записывают в домашний каталог.

---

## Жизненный цикл сервера

### Запуск (lazy)

Серверы **не подключаются при старте**. При наличии кэша инструменты регистрируются
сразу — модель уже видит их в списке tools. Соединение устанавливается только когда
модель впервые вызывает инструмент этого сервера.

Если кэша нет — сервер подключается при старте для получения списка инструментов.

### Регистрация в категории

Инструменты MCP-сервера регистрируются в категории `mcp:<name>` в ToolRegistry.
Модель может загружать категорию через `load_tools(category="mcp:playwright")`.

### Автозагрузка инструментов

Все MCP-категории автоматически добавляются в tool definition `load_tools` при запуске.
Модель видит: `load_tools: Available: mcp:playwright (browser_navigate, browser_click, ...)`.

---

## Команды `/mcp`

```
/mcp                              — статус всех серверов
/mcp add <name> <command> [args]  — добавить сервер
/mcp remove <name>                — удалить сервер
/mcp refresh [name]               — переподключить сервер(ы)
/mcp tools <name>                 — список инструментов сервера
/mcp call <name> <tool> [args]    — вызвать инструмент напрямую
```

### `/mcp add`

Добавляет сервер, подключается, кэширует инструменты и сохраняет в `.emelya/mcp.json`.

```
/mcp add playwright npx -y @playwright/mcp@latest
/mcp add myserver python3 /path/to/server.py
/mcp add remote npx mcp-remote https://example.com/mcp
/mcp add gemini node server.mjs --env GEMINI_API_KEY=xxx
```

Опция `--env KEY=VALUE` задаёт переменные окружения и поддерживается многократно.

### `/mcp tools <name>`

Выводит список инструментов с описаниями и параметрами:

```
/mcp tools playwright

  === MCP playwright: 12 tools ===

  browser_navigate
    Navigate to a URL
    url * (string) — Target URL

  browser_click
    Click an element on the page
    selector * (string) — CSS selector or text
```

### `/mcp call <name> <tool> [args]`

Прямой вызов инструмента без участия модели. Удобно для отладки.

```bash
# JSON-аргументы
/mcp call playwright browser_navigate {"url": "https://example.com"}

# Plain text → автоматически как первый параметр
/mcp call playwright browser_navigate https://example.com

# Без аргументов
/mcp call myserver list_resources
```

Ответ JSON автоматически форматируется (до 100 строк — pretty-print).

### `/mcp refresh [name]`

Переподключает сервер и обновляет кэш инструментов. Без имени — обновляет все.

```
/mcp refresh playwright
/mcp refresh
```

---

## Ручная настройка глобального сервера

Чтобы подключить сервер глобально (для всех проектов), отредактируйте
`~/.emelya/mcp.json` вручную:

```json
{
  "servers": {
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp@latest"],
      "description": "Browser automation"
    }
  }
}
```

После этого сервер будет доступен во всех проектах. Команда `/mcp add` всегда
сохраняет в локальный `.emelya/mcp.json` — для глобального редактируйте вручную.

---

## Диагностика

### Статус серверов

```
/mcp

  playwright: connected, 12 tools (playwright) [loaded]
  filesystem: cached, 8 tools
  myapi: not cached
  Config: .emelya/mcp.json
```

Статусы:
- `connected` — сервер подключён и работает
- `cached` — инструменты известны из кэша, сервер ещё не подключался
- `stopped` — процесс сервера завершился
- `not cached` — нет ни кэша, ни соединения

### Переподключение при ошибке

Если сервер был добавлен, но не запустился (ошибка конфигурации):

```
/mcp refresh myserver
```

После исправления конфигурации в `.emelya/mcp.json` — рестарт подхватит изменения.
