# Хуки (Hooks)

## Концепция

Хук — это внешняя программа или запрос, который Emelya запускает в ответ на определённое событие агентного цикла. Хуки позволяют встраивать собственную логику без правки исходного кода: запускать форматтер после редактирования файла, блокировать опасные команды, отправлять уведомление когда агент завершил задачу.

Хук получает данные о событии через stdin (JSON) и сообщает результат через exit code и stdout. Ненулевой специальный exit code блокирует операцию.

```
Событие в Emelya
      │
      ▼
  Загрузить хуки (глобальные → локальные)
      │
      ├─ type=command → запустить subprocess, stdin=JSON
      ├─ type=http    → POST к URL, body=JSON
      └─ type=prompt  → запрос к модели, получить решение
      │
      ▼
  Прочитать ответ хука
      │
      ├─ "block": true  → отменить операцию, показать reason
      └─ "block": false → продолжить (опционально: message, context)
```

---

## Конфигурация

Хуки задаются в файлах `hooks.json` в двух местах. Глобальные применяются ко всем проектам, локальные — только к текущему. При конфликте (одинаковое событие) хуки **объединяются**: выполняются все, и глобальные и локальные.

```
~/.emelya/hooks.json        — глобальные (все проекты)
.emelya/hooks.json          — локальные (текущий проект)
```

### Структура файла

```json
{
  "hooks": {
    "ИмяСобытия": [
      {
        "type": "command",
        "command": "скрипт или команда",
        "timeout": 30
      },
      {
        "type": "http",
        "url": "https://example.com/webhook",
        "timeout": 10
      }
    ],
    "ДругоеСобытие": [...]
  }
}
```

Каждое событие содержит список хуков. Хуки одного события выполняются **по порядку**. Если любой из них блокирует операцию, выполнение следующих прерывается.

---

## Типы хуков

### `command` — shell-команда

Запускается через `sh -c "команда"`. Данные события передаются в **stdin** в виде JSON. Ответ читается из **stdout** (JSON) и **exit code**.

```json
{
  "type": "command",
  "command": "python3 ~/.emelya/hooks/check_secrets.py",
  "timeout": 15
}
```

**Exit codes:**

| Код | Смысл |
|-----|-------|
| `0` | Успех. Читаем stdout как JSON. |
| `2` | **Блокировка.** Операция отменяется. Первая строка stderr показывается пользователю. |
| `1`, `3+` | Нефатальная ошибка. Операция продолжается, первая строка stderr — в лог. |

Команде доступны переменные окружения:

| Переменная | Значение |
|------------|----------|
| `EMELYA_EVENT` | Имя события (`PreBash`, `SessionStart`, …) |
| `EMELYA_WORK_DIR` | Рабочий каталог проекта |
| `EMELYA_SESSION` | Имя текущей сессии |
| `EMELYA_TOOL` | Имя инструмента (для tool-событий) |
| `EMELYA_PATH` | Путь к файлу (для PreWrite) |
| `EMELYA_COMMAND` | Команда (для PreBash/PostBash) |

Это позволяет писать простые хуки без парсинга JSON:

```bash
#!/bin/bash
# Блокировать git push --force
if echo "$EMELYA_COMMAND" | grep -q 'push.*--force[^-]'; then
  echo "git push --force запрещён" >&2
  exit 2
fi
```

---

### `http` — HTTP-запрос

Отправляет **POST-запрос** к указанному URL. Тело запроса — тот же JSON что в stdin у command-хука. Ответ читается из тела HTTP-ответа.

```json
{
  "type": "http",
  "url": "https://audit.company.com/hooks/emelya",
  "headers": {
    "Authorization": "Bearer $AUDIT_TOKEN",
    "X-Project": "my-project"
  },
  "allowedEnvVars": ["AUDIT_TOKEN"],
  "timeout": 10
}
```

Переменные окружения в заголовках подставляются только для тех, что перечислены в `allowedEnvVars`. Остальные становятся пустой строкой — это защита от случайной утечки токенов.

**Коды HTTP-ответа:**

| Статус | Смысл |
|--------|-------|
| `2xx` + JSON | Успех, читаем JSON для решений |
| `2xx` + пустой | Успех, продолжаем |
| Остальные | Нефатальная ошибка, продолжаем |

---

### `prompt` — запрос к модели

Отправляет **одиночный LLM-вызов** без истории диалога и без инструментов. Модель получает описание события и должна вернуть простое решение: блокировать или нет. Запрос изолирован — не влияет на текущий контекст и не виден пользователю.

```json
{
  "type": "prompt",
  "prompt": "Проверь, выглядит ли эта команда деструктивной или опасной. Верни JSON: {\"block\": true, \"reason\": \"причина\"} если опасна, {\"block\": false} если нет.",
  "model": "local",
  "timeout": 20
}
```

Поле `model` — имя модели из `models.json` (`local`, `gpt4`, `deepseek` и т.д.). По умолчанию используется активная модель сессии. Prompt hook удобен когда нужно суждение модели о событии, но файлы читать и команды запускать не требуется.

Данные события подставляются в промпт через placeholder `{event}`:

```json
{
  "type": "prompt",
  "prompt": "Событие:\n{event}\n\nЭто безопасно? Верни {\"block\": false} если да, {\"block\": true, \"reason\": \"...\"} если нет.",
  "model": "local"
}
```

`{event}` заменяется на отформатированный текст с именем события и ключевыми полями (команда, путь, инструмент).

---

## Формат ответа хука

Все три типа хуков возвращают JSON с одинаковой структурой:

```json
{
  "block": false,
  "reason": "...",
  "message": "...",
  "context": "...",
  "result": "...",
  "command": "..."
}
```

| Поле | Тип | События | Смысл |
|------|-----|---------|-------|
| `block` | bool | все | `true` — отменить операцию |
| `reason` | string | все | Показывается пользователю при блокировке |
| `message` | string | все | Предупреждение пользователю (без блокировки) |
| `context` | string | Post* | Дополнительный текст, **добавляемый** к tool result (после оригинального вывода) |
| `result` | string | Post* | Полная **замена** tool result. Модель видит только это. Оригинал сохраняется в `log.txt`. |
| `command` | string | PreBash | **Замена команды** перед выполнением. Bash запустит эту команду вместо оригинальной. |

Поле `context` добавляет текст к результату инструмента — например, предупреждение линтера поверх вывода команды. Поле `result` заменяет результат целиком — это основной инструмент для снижения количества токенов.

Если `result` и `context` заданы одновременно, `result` имеет приоритет: `context` игнорируется.

Если stdout пустой или содержит невалидный JSON — операция продолжается без изменений.

### Цепочки хуков и подмена данных

Когда для одного события задано несколько хуков, они выполняются последовательно. Подмена данных распространяется по цепочке: **каждый следующий хук работает с актуальным состоянием**, а не с исходным.

**`command` в PreBash** — следующий хук в цепочке получает уже подменённую команду:

```
Модель вызывает bash("git diff HEAD")
        │
        ▼
  Хук 1 (rtk_wrapper.sh)
  stdin.command = "git diff HEAD"
  → возвращает command: "rtk git diff HEAD"
        │
        ▼
  Хук 2 (audit_logger.sh)
  stdin.command = "rtk git diff HEAD"   ← уже подменённая
  → логирует, не меняет command
        │
        ▼
  Bash выполняет: "rtk git diff HEAD"
  log.txt содержит: оригинал "git diff HEAD" и итоговую "rtk git diff HEAD"
```

**`result` в PostBash** — следующий хук получает уже отфильтрованный вывод:

```
Bash выполнил → stdout: 500 строк cargo build
        │
        ▼
  Хук 1 (filter_cargo.py)
  stdin.stdout = "500 строк"
  → возвращает result: "3 ошибки компиляции"
        │
        ▼
  Хук 2 (audit_logger.sh)
  stdin.stdout = "3 ошибки компиляции"   ← уже подменённый
  → логирует, не меняет result
        │
        ▼
  Модель видит: "3 ошибки компиляции"
  log.txt содержит: оригинальные 500 строк
```

Если хук не возвращает `command` / `result`, следующий хук получает текущее состояние (либо оригинал, либо результат предыдущего хука).

---

## События

### `PreToolUse` — перед любым инструментом

Срабатывает **до** выполнения любого инструмента. Может блокировать вызов.

**Фильтр по инструментам** — поле `tools` в конфиге хука:

```json
{
  "type": "command",
  "command": "~/.emelya/hooks/audit.sh",
  "tools": ["edit", "bash"]
}
```

Если `tools` не задано — хук срабатывает для всех инструментов.

**Входной JSON:**

```json
{
  "event": "PreToolUse",
  "session": "tmp20260713183042",
  "work_dir": "/home/user/myproject",
  "tool": "bash",
  "args": {
    "command": "npm run build"
  }
}
```

**Пример:** аудит-лог всех вызовов инструментов:

```bash
#!/bin/bash
# ~/.emelya/hooks/audit.sh
jq -c '{ts: now | todate, event, tool, args}' >> ~/.emelya/audit.jsonl
```

---

### `PostToolUse` — после выполнения инструмента

Срабатывает **после** успешного выполнения инструмента. Не блокирует (операция уже выполнена), но может добавить контекст или отправить уведомление.

**Входной JSON:**

```json
{
  "event": "PostToolUse",
  "session": "tmp20260713183042",
  "work_dir": "/home/user/myproject",
  "tool": "edit",
  "args": {
    "path": "src/parser.py",
    "content": "..."
  },
  "result": "Written 87 lines to src/parser.py",
  "elapsed_ms": 14
}
```

**Пример:** автоматический запуск black после записи .py файла:

```bash
#!/bin/bash
# .emelya/hooks/post_write.sh
FILE=$(echo "$EMELYA_PATH")
if [[ "$FILE" == *.py ]]; then
  black "$FILE" --quiet 2>/dev/null
  echo '{"context": "File was auto-formatted with black."}'
fi
```

```json
{
  "type": "command",
  "command": ".emelya/hooks/post_write.sh",
  "tools": ["edit"]
}
```

---

### `PreBash` — перед выполнением bash-команды

Срабатывает **до** запуска shell-команды, после проверки прав. Может блокировать.

**Входной JSON:**

```json
{
  "event": "PreBash",
  "session": "tmp20260713183042",
  "work_dir": "/home/user/myproject",
  "command": "git push origin main",
  "is_readonly": false,
  "auto_approved": false
}
```

`is_readonly: true` означает что команда автоодобрена как read-only (git status, ls и т.д.).

**Пример:** подмена команды — прозрачная обёртка `rtk`:

```bash
#!/bin/bash
# ~/.emelya/hooks/rtk_wrapper.sh
# Автоматически подставляет rtk перед git, cargo, pytest и другими
# поддерживаемыми командами чтобы снизить объём вывода.

CMD=$(python3 -c "import sys,json; print(json.load(sys.stdin)['command'])")

# Список префиксов которые rtk умеет обрабатывать
RTK_PREFIXES="git cargo pytest pytest rg grep ls find npm pnpm yarn tsc"

for prefix in $RTK_PREFIXES; do
  if echo "$CMD" | grep -qE "^(sudo )?$prefix( |$)"; then
    # Ещё не обёрнуто в rtk
    if ! echo "$CMD" | grep -q "^rtk "; then
      python3 -c "import json,sys; print(json.dumps({'command': 'rtk ' + sys.argv[1]}))" "$CMD"
    fi
    exit 0
  fi
done
```

```json
{
  "type": "command",
  "command": "~/.emelya/hooks/rtk_wrapper.sh",
  "timeout": 3
}
```

Если хук не вернул `command` (команда не подпадает под фильтр) — bash выполняет оригинал без изменений.

**Пример:** блокировать push в main:

```bash
#!/bin/bash
CMD=$(python3 -c "import sys,json; print(json.load(sys.stdin)['command'])")
if echo "$CMD" | grep -qE 'push.+main|push.+master'; then
  echo "Прямой push в main запрещён. Создайте PR." >&2
  exit 2
fi
```

**Пример через prompt:** попросить модель оценить команду:

```json
{
  "type": "prompt",
  "prompt": "Оцени команду на риск потери данных или необратимых изменений.\n{event}\n\nВерни {\"block\": false} если безопасно, {\"block\": true, \"reason\": \"...\"} если есть серьёзный риск.",
  "model": "local"
}
```

---

### `PostBash` — после выполнения bash-команды

Срабатывает после завершения команды. Получает stdout, stderr и код возврата.

**Входной JSON:**

```json
{
  "event": "PostBash",
  "session": "tmp20260713183042",
  "work_dir": "/home/user/myproject",
  "command": "pytest tests/",
  "stdout": "===== 3 failed, 42 passed in 5.21s =====\n...",
  "stderr": "",
  "returncode": 1,
  "elapsed_ms": 5210
}
```

**Пример:** фильтрация вывода для снижения токенов (`result` — замена вывода):

```python
#!/usr/bin/env python3
# ~/.emelya/hooks/filter_output.py
import sys, json, re

data = json.load(sys.stdin)
command = data["command"]
stdout  = data["stdout"]
rc      = data["returncode"]

# cargo/rustc — оставить только строки с error/warning
if any(x in command for x in ("cargo", "rustc")):
    lines = [l for l in stdout.splitlines()
             if re.match(r"^\s*(error|warning)\b", l)]
    if lines:
        print(json.dumps({"result": "\n".join(lines[:40])}))
        sys.exit(0)

# pytest — оставить только FAILED-блоки и итоговую строку
if "pytest" in command:
    lines, keep, capture = stdout.splitlines(), [], False
    for l in lines:
        if re.match(r"^(FAILED|ERROR)\b", l): capture = True
        if l.startswith("==="):               capture = False; keep.append(l)
        elif capture:                          keep.append(l)
    if keep:
        print(json.dumps({"result": "\n".join(keep)}))
        sys.exit(0)

# npm/yarn build — убрать прогресс-бары и пустые строки
if re.search(r"\bnpm\b|\byarn\b|\bpnpm\b", command) and "build" in command:
    lines = [l for l in stdout.splitlines()
             if l.strip() and not re.match(r"^[\s>⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]", l)]
    if len(lines) < len(stdout.splitlines()) // 2:   # убрали хотя бы половину
        print(json.dumps({"result": "\n".join(lines)}))
        sys.exit(0)

# для остальных — не трогать вывод
```

```json
{
  "type": "command",
  "command": "python3 ~/.emelya/hooks/filter_output.py",
  "timeout": 5
}
```

**Пример:** добавить краткую сводку ошибок поверх вывода (`context` — дополнение):

```bash
#!/bin/bash
# Добавить количество ошибок в контекст не трогая оригинальный вывод
python3 - <<'EOF'
import sys, json
data = json.load(sys.stdin)
errors = data["stdout"].lower().count("error")
if errors > 0 and data["returncode"] != 0:
    print(json.dumps({"context": f"[{errors} error(s) detected]"}))
EOF
```

---

### `PreWrite` — перед записью или редактированием файла

Срабатывает перед каждой итоговой файловой операцией `edit` **до**
записи на диск. Для многофайлового edit все `PreWrite` hooks выполняются до
изменения первого файла; блокировка любой операции отклоняет edit целиком.

**Входной JSON для create через edit:**

```json
{
  "event": "PreWrite",
  "session": "tmp20260713183042",
  "work_dir": "/home/user/myproject",
  "tool": "edit",
  "path": "/home/user/myproject/src/auth.py",
  "operation": "create",
  "old_content": null,
  "new_content": "import os\nSECRET_KEY = os.getenv('SECRET')\n..."
}
```

**Входной JSON для edit:**

```json
{
  "event": "PreWrite",
  "session": "tmp20260713183042",
  "work_dir": "/home/user/myproject",
  "tool": "edit",
  "path": "/home/user/myproject/.env",
  "operation": "update",
  "old_content": "DEBUG=false\n",
  "new_content": "DEBUG=true\n"
}
```

Для `add` значение `old_content` равно `null`, для `delete` значение
`new_content` равно `null`.

**Пример:** блокировать запись хардкоженных секретов:

```bash
#!/bin/bash
CONTENT=$(python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('content','') or d.get('new_content',''))")
if echo "$CONTENT" | grep -qiE '(password|secret|api_key)\s*=\s*["\x27][^"\x27]{6,}'; then
  echo "Обнаружен потенциальный секрет в коде. Используйте переменные окружения." >&2
  exit 2
fi
```

**Пример:** запрет правки production конфигов:

```bash
#!/bin/bash
PATH_VAR="$EMELYA_PATH"
if echo "$PATH_VAR" | grep -q 'prod\|production'; then
  echo "Редактирование production-конфигов запрещено." >&2
  exit 2
fi
```

---

### `SessionStart` — при запуске REPL

Срабатывает однократно при инициализации сессии, после загрузки всей конфигурации. Не блокирует.

**Входной JSON:**

```json
{
  "event": "SessionStart",
  "session": "tmp20260713183042",
  "work_dir": "/home/user/myproject",
  "model": "local",
  "base_url": "http://localhost:8080",
  "resumed": false
}
```

`resumed: true` если сессия загружена через `--resume` или `/resume`.

**Пример:** проверить наличие нужных сервисов:

```bash
#!/bin/bash
# ~/.emelya/hooks/session_start.sh — глобальный
if ! curl -sf http://localhost:8080/health > /dev/null 2>&1; then
  echo '{"message": "⚠ llama.cpp не отвечает на localhost:8080"}'
fi
```

**Пример:** показать статус git при старте:

```bash
#!/bin/bash
# .emelya/hooks/session_start.sh — локальный
BRANCH=$(git -C "$EMELYA_WORK_DIR" branch --show-current 2>/dev/null)
DIRTY=$(git -C "$EMELYA_WORK_DIR" status --short 2>/dev/null | wc -l)
if [ -n "$BRANCH" ]; then
  MSG="Ветка: $BRANCH"
  [ "$DIRTY" -gt 0 ] && MSG="$MSG, незакомиченных файлов: $DIRTY"
  echo "{\"message\": \"$MSG\"}"
fi
```

---

### `SessionEnd` — при завершении REPL

Срабатывает при выходе (Ctrl+C, `/exit`, `--resume` с новым именем). Не блокирует.

**Входной JSON:**

```json
{
  "event": "SessionEnd",
  "session": "tmp20260713183042",
  "work_dir": "/home/user/myproject",
  "model": "local",
  "total_tokens": 45300,
  "tool_calls": 23,
  "duration_s": 1834
}
```

**Пример:** desktop-уведомление о завершении:

```bash
#!/bin/bash
# ~/.emelya/hooks/session_end.sh
TOKENS=$(python3 -c "import sys,json; print(json.load(sys.stdin)['total_tokens'])")
notify-send "Emelya" "Сессия завершена. Токенов использовано: $TOKENS" 2>/dev/null || true
```

**Пример:** пушить статистику использования:

```bash
#!/bin/bash
python3 -c "
import sys, json, datetime, pathlib
data = json.load(sys.stdin)
data['date'] = datetime.date.today().isoformat()
log = pathlib.Path.home() / '.emelya' / 'usage_history.jsonl'
log.open('a').write(json.dumps(data) + '\n')
"
```

---

## Таблица событий

| Событие | Когда | Может блокировать | Фильтр `tools` |
|---------|-------|:-----------------:|:--------------:|
| `PreToolUse` | Перед любым инструментом | ✅ | ✅ |
| `PostToolUse` | После инструмента | — | ✅ |
| `PreBash` | Перед shell-командой | ✅ | — |
| `PostBash` | После shell-команды | — | — |
| `PreWrite` | Перед файловой операцией edit | ✅ | — |
| `SessionStart` | При запуске REPL | — | — |
| `SessionEnd` | При выходе | — | — |

---

## Примеры конфигураций

### Минимальная глобальная (`~/.emelya/hooks.json`)

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "type": "command",
        "command": "notify-send 'Emelya' 'Сессия завершена' 2>/dev/null || true"
      }
    ]
  }
}
```

### Проектная (`.emelya/hooks.json`)

```json
{
  "hooks": {
    "PreBash": [
      {
        "type": "command",
        "command": ".emelya/hooks/check_bash.sh",
        "timeout": 5
      }
    ],
    "PostToolUse": [
      {
        "type": "command",
        "command": ".emelya/hooks/format.sh",
        "tools": ["edit"],
        "timeout": 30
      }
    ],
    "PreWrite": [
      {
        "type": "command",
        "command": ".emelya/hooks/check_secrets.sh",
        "timeout": 10
      }
    ],
    "SessionStart": [
      {
        "type": "http",
        "url": "https://team-monitor.example.com/session/start",
        "headers": { "Authorization": "Bearer $MONITOR_TOKEN" },
        "allowedEnvVars": ["MONITOR_TOKEN"],
        "timeout": 5
      }
    ]
  }
}
```

### С prompt-хуком для PreBash

```json
{
  "hooks": {
    "PreBash": [
      {
        "type": "prompt",
        "model": "local",
        "prompt": "Оцени shell-команду на необратимость (rm, drop, truncate, force push и т.п.).\n{event}\n\nВерни {\"block\": false} если обратимо или безопасно, {\"block\": true, \"reason\": \"краткое объяснение\"} если необратимо.",
        "timeout": 15
      }
    ]
  }
}
```

---

## Рекомендации

**Тайм-ауты.** По умолчанию 30 секунд. Хуки в критическом пути (`PreBash`, `PreWrite`) должны укладываться в 5–10 секунд, иначе они заметно замедляют работу.

**Порядок выполнения.** Хуки одного события выполняются последовательно в порядке: сначала глобальные (`~/.emelya/hooks.json`), затем локальные (`.emelya/hooks.json`). Первый заблокировавший хук прерывает цепочку.

**Отладка.** Установите переменную `EMELYA_HOOKS_DEBUG=1` чтобы видеть в терминале что и с каким ответом запускается. В будущем: `emelya --hooks-dry-run` симулирует события без реального выполнения.

**Безопасность.** Хуки запускаются с правами текущего пользователя. `allowedEnvVars` в http-хуках — обязательная практика, не опциональная. Не передавайте секреты через аргументы командной строки — они видны в `ps aux`.

**Prompt-хуки и стоимость.** Каждый вызов prompt-хука — это отдельный LLM-запрос. На медленных локальных моделях он добавляет заметную задержку. Используйте быструю модель (`agent_light`-класса) или переключитесь на `command`-хук с жёсткими правилами для частых событий (`PreBash`).
