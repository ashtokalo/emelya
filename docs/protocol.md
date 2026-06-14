# Протокол взаимодействия с LLM

## Endpoint

```
POST http://{host}:{port}/v1/chat/completions
Content-Type: application/json
```

По умолчанию: `http://localhost:8080/v1/chat/completions`

## Формат запроса

```json
{
  "model": "default",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."},
    ...
  ],
  "tools": [...],
  "temperature": 0.7,
  "max_tokens": 1024,
  "stream": false
}
```

### Поле messages

Массив сообщений — полная история диалога. Роли:

| role | Описание |
|------|----------|
| `system` | Системный промпт (всегда первый, всегда один) |
| `user` | Сообщение пользователя |
| `assistant` | Ответ модели (текст и/или tool_calls) |
| `tool` | Результат выполнения tool call |

### Поле tools

Массив доступных инструментов. Формат каждого элемента:

```json
{
  "type": "function",
  "function": {
    "name": "tool_name",
    "description": "What this tool does",
    "parameters": {
      "type": "object",
      "properties": {
        "param1": {"type": "string", "description": "..."},
        "param2": {"type": "integer", "description": "..."}
      },
      "required": ["param1"]
    }
  }
}
```

## Формат ответа

### Текстовый ответ (финальный)

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "Текст ответа для пользователя"
    },
    "finish_reason": "stop"
  }]
}
```

### Ответ с tool calls

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "call_abc123",
          "type": "function",
          "function": {
            "name": "read",
            "arguments": "{\"path\": \"main.py\", \"limit\": 50}"
          }
        }
      ]
    },
    "finish_reason": "tool_calls"
  }]
}
```

Обратите внимание: `arguments` — это **строка** с JSON, не объект.
Нужен `json.loads(tool_call["function"]["arguments"])`.

### Смешанный ответ (текст + tool calls)

Модель может вернуть и `content`, и `tool_calls` одновременно.
В этом случае `content` — это рассуждение модели, а `tool_calls` — действия.
Текст показывается пользователю, tool calls выполняются.

## Агентский цикл (Tool calling loop)

```
messages = [system_prompt, user_message]

while iterations < max_iterations:
    response = POST /v1/chat/completions {messages, tools}
    assistant_message = response.choices[0].message

    # Добавляем ответ модели в историю
    messages.append(assistant_message)

    if not assistant_message.tool_calls:
        # Финальный ответ — выход из цикла
        break

    # Выполняем каждый tool call
    for tool_call in assistant_message.tool_calls:
        result = execute_tool(tool_call)

        # Добавляем результат в историю
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": result
        })

    iterations += 1
```

### Важно: tool_call_id

Каждый `tool` message должен содержать `tool_call_id`, соответствующий `id`
из `tool_calls` в предыдущем `assistant` message. Это связывает результат
с конкретным вызовом.

### Несколько tool_calls в одном ответе

Модель может вернуть несколько tool_calls за раз. Все выполняются,
все результаты добавляются как отдельные `tool` messages,
затем отправляется следующий запрос.

```json
messages: [
  {"role": "assistant", "tool_calls": [
    {"id": "call_1", "function": {"name": "read", "arguments": "..."}},
    {"id": "call_2", "function": {"name": "glob", "arguments": "..."}}
  ]},
  {"role": "tool", "tool_call_id": "call_1", "content": "..."},
  {"role": "tool", "tool_call_id": "call_2", "content": "..."}
]
```

## Стриминг

### Запрос

```json
{"stream": true, ...}
```

### Формат ответа

Server-Sent Events (SSE). Каждая строка:

```
data: {"choices":[{"delta":{"content":"фрагмент текста"}}]}

data: {"choices":[{"delta":{"content":null,"tool_calls":[...]}}]}

data: [DONE]
```

### Обработка стриминга

Для текстовых ответов (`content`) — выводить посимвольно в терминал.
Для tool calls — собирать JSON из дельт, выполнять после получения `[DONE]`.

```python
def stream_response(self, raw_response):
    content = ""
    tool_calls = []

    for line in raw_response.iter_lines():
        if line == b"data: [DONE]":
            break
        if not line.startswith(b"data: "):
            continue

        chunk = json.loads(line[6:])
        delta = chunk["choices"][0]["delta"]

        if "content" in delta and delta["content"]:
            fragment = delta["content"]
            content += fragment
            print(fragment, end="", flush=True)  # стриминг в терминал

        if "tool_calls" in delta:
            # Собираем tool calls из дельт
            self._accumulate_tool_calls(tool_calls, delta["tool_calls"])

    return content, tool_calls
```

### Сборка tool calls из дельт при стриминге

Tool calls приходят частями:

```
delta: {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "read", "arguments": ""}}]}
delta: {"tool_calls": [{"index": 0, "function": {"arguments": "{\"pa"}}]}
delta: {"tool_calls": [{"index": 0, "function": {"arguments": "th\":"}}]}
delta: {"tool_calls": [{"index": 0, "function": {"arguments": " \"main.py\"}"}}]}
```

Алгоритм: по `index` определяем какой tool call дополняется,
конкатенируем `arguments`. После `[DONE]` парсим собранный JSON.

## Обработка ошибок

### Ошибки соединения

```python
try:
    response = urllib.request.urlopen(request, timeout=timeout)
except urllib.error.URLError as e:
    # Сервер недоступен
except socket.timeout:
    # Таймаут запроса
```

Стратегия повторных попыток:
- Максимум `retry` попыток (по умолчанию 2)
- Задержка между попытками: `retry_delay` секунд (по умолчанию 2)
- Повторяются только ошибки соединения, не ошибки API (4xx/5xx)

### Ошибки API

| Код | Значение | Действие |
|-----|----------|----------|
| 400 | Неверный запрос | Ошибка в формате, не повторять |
| 404 | Endpoint не найден | Проверить URL |
| 500 | Ошибка сервера | Можно повторить |
| 503 | Сервер перегружен | Повторить с задержкой |

### Невалидный JSON в arguments

Модель (особенно маленькая) может сгенерировать невалидный JSON в `arguments`.
Стратегия:
1. Попытка `json.loads(arguments)`
2. При ошибке — попытка починить: убрать trailing comma, закрыть скобки
3. Если не удалось — вернуть модели ошибку как tool result:
   `"Error: invalid JSON in arguments: {raw}"`

### Пустой ответ

Если `choices` пустой или `content` и `tool_calls` оба отсутствуют —
повторить запрос (до retry лимита).

## Проверка совместимости

При старте агента — тестовый запрос для проверки:
1. Что сервер доступен
2. Что поддерживается endpoint `/v1/chat/completions`
3. Что модель умеет tool calling

```python
def check_connection(self) -> tuple[bool, str]:
    """Возвращает (ok, message)"""
    # 1. Проверяем доступность
    try:
        health = GET /health
    except:
        return False, "Server not available"

    # 2. Проверяем tool calling
    response = POST /v1/chat/completions {
        messages: [{"role": "user", "content": "hi"}],
        tools: [{"type": "function", "function": {
            "name": "test", "parameters": {"type": "object", "properties": {}}
        }}],
        max_tokens: 50
    }

    if response has tool_calls or response has content:
        return True, "OK"
    else:
        return False, "Model may not support tool calling"
```

## HTTP-клиент (urllib)

Так как используется только стандартная библиотека:

```python
import json
import urllib.request
import urllib.error

def post_json(url: str, data: dict, timeout: int = 120) -> dict:
    body = json.dumps(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())
```

Для стриминга — читать `response` построчно без закрытия:

```python
def post_stream(url: str, data: dict, timeout: int = 120):
    data["stream"] = True
    body = json.dumps(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    response = urllib.request.urlopen(request, timeout=timeout)
    for raw_line in response:
        line = raw_line.decode("utf-8").strip()
        if line.startswith("data: "):
            yield line[6:]
    response.close()
```
