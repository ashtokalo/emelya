"""Internationalization — simple string table based on system locale."""

import locale
import os

_STRINGS = {
    "en": {
        "you": "You",
        "emelya": "Emelya",
        "greeting": "Hi! I'm a CLI AI agent. Type a task or /help for commands.",
        "ready": "ready",
        "ready_model": "{model} — ready",
        "connected": "Connected",
        "warning": "Warning",
        "use_model_switch": "Use /model to switch to another model",
        "workdir": "WorkDir",
        "session": "Session",
        "checking": "Checking",
        "server_unavailable": "Local model unavailable",
        "auth_failed": "Authentication failed (invalid API key)",
        "allow": "Allow? (y/n)",
        "cancelled": "Cancelled by user",
        "interrupted": "(interrupted)",
        "bye": "Bye!",
        "unknown_cmd": "Unknown command",
        "available": "Available",
        "session_saved": "Session saved",
        "session_loaded": "Session loaded",
        "session_not_found": "Session not found",
        "no_sessions": "No saved sessions",
        "available_sessions": "Available sessions",
        "new_session": "New session",
        "context_cleared": "Context cleared",
        "compacting": "Compacting context...",
        "compacted": "Compacted",
        "compaction_failed": "Compaction failed",
        "nothing_to_undo": "Nothing to undo",
        "undo_deleted": "Undo: deleted",
        "undo_restored": "Undo: restored",
        "removed": "Removed",
        "loaded_tools": "Loaded tools",
        "extensions": "Extensions",
        "no_mcp": "No MCP servers configured",
        "no_usage": "No usage data yet",
        "by_model": "By model",
        "by_date": "By date",
        "total": "Total",
        "project_total": "Project total",
        "step": "step",
        "summarizing": "summarizing...",
        "running": "Running...",
        "max_iter": "max iterations, forcing answer...",
        "steps_left": "Only {n} steps left. Start composing your answer.",
        "last_step": "LAST STEP. Give your final answer NOW. Do not call any more tools.",
        "help_file": "help_en.md",
        "config": "Config",
        "add_hint": "Add",
        "unknown_subcmd": "Unknown",
        "usage": "Usage",
        "switched_to": "Switched to",
        "added": "Added",
        "cannot_remove_local": "Cannot remove 'local' model",
        "unknown_model": "Unknown model",
        "error": "Error",
        "connected_status": "connected",
        "cached": "cached",
        "not_cached": "not cached",
        "stopped": "stopped",
        "refreshed": "refreshed",
        "messages": "messages",
        "tokens": "tokens",
    },
    "ru": {
        "you": "Вы",
        "emelya": "Емеля",
        "greeting": "Привет! Я CLI AI-агент. Введи задачу или /help для списка команд.",
        "ready": "готов к работе",
        "ready_model": "локальная модель {model} — готова к работе",
        "connected": "Подключено",
        "warning": "Предупреждение",
        "use_model_switch": "Используй /model для переключения",
        "workdir": "Каталог",
        "session": "Сессия",
        "checking": "Проверка",
        "server_unavailable": "Локальная модель недоступна",
        "auth_failed": "Ошибка аутентификации (неверный API ключ)",
        "allow": "Разрешить? (д/н)",
        "cancelled": "Отменено пользователем",
        "interrupted": "(прервано)",
        "bye": "Пока!",
        "unknown_cmd": "Неизвестная команда",
        "available": "Доступные",
        "session_saved": "Сессия сохранена",
        "session_loaded": "Сессия загружена",
        "session_not_found": "Сессия не найдена",
        "no_sessions": "Нет сохранённых сессий",
        "available_sessions": "Доступные сессии",
        "new_session": "Новая сессия",
        "context_cleared": "Контекст очищен",
        "compacting": "Сжатие контекста...",
        "compacted": "Сжато",
        "compaction_failed": "Ошибка сжатия",
        "nothing_to_undo": "Нечего отменять",
        "undo_deleted": "Отмена: удалён",
        "undo_restored": "Отмена: восстановлен",
        "removed": "Удалено",
        "loaded_tools": "Инструменты",
        "extensions": "Расширения",
        "no_mcp": "Нет настроенных MCP серверов",
        "no_usage": "Нет данных",
        "by_model": "По модели",
        "by_date": "По дате",
        "total": "Итого",
        "project_total": "Проект всего",
        "step": "шаг",
        "summarizing": "подводим итог...",
        "running": "Работает...",
        "max_iter": "лимит итераций, завершаем...",
        "steps_left": "Осталось {n} шагов. Начинай формировать ответ.",
        "last_step": "ПОСЛЕДНИЙ ШАГ. Дай финальный ответ СЕЙЧАС. Не вызывай инструменты.",
        "help_file": "help_ru.md",
        "config": "Конфиг",
        "add_hint": "Добавить",
        "unknown_subcmd": "Неизвестно",
        "usage": "Использование",
        "switched_to": "Переключено на",
        "added": "Добавлено",
        "cannot_remove_local": "Нельзя удалить 'local'",
        "unknown_model": "Неизвестная модель",
        "error": "Ошибка",
        "connected_status": "подключено",
        "cached": "кеш",
        "not_cached": "нет кеша",
        "stopped": "остановлен",
        "refreshed": "обновлено",
        "messages": "сообщений",
        "tokens": "токенов",
    },
}


def _detect_lang() -> str:
    """Detect language from environment."""
    for var in ("LANGUAGE", "LANG", "LC_ALL", "LC_MESSAGES"):
        val = os.environ.get(var, "")
        if val.startswith("ru"):
            return "ru"
    try:
        loc = locale.getdefaultlocale()[0] or ""
        if loc.startswith("ru"):
            return "ru"
    except Exception:
        pass
    return "en"


_lang = _detect_lang()
_strings = _STRINGS[_lang]


def t(key: str, **kwargs) -> str:
    """Get translated string by key. Supports {var} placeholders."""
    s = _strings.get(key, _STRINGS["en"].get(key, key))
    if kwargs:
        return s.format(**kwargs)
    return s


def get_lang() -> str:
    return _lang
