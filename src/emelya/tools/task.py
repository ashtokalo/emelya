"""Task tools — progress tracking for multi-step work."""

import sys

DEFINITION = {
    "type": "function",
    "function": {
        "name": "task",
        "description": "Track progress: create/update/complete tasks shown to user",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "create, update, complete, or list",
                },
                "title": {
                    "type": "string",
                    "description": "Task title (for create/update)",
                },
                "id": {
                    "type": "integer",
                    "description": "Task ID (for update/complete)",
                },
            },
            "required": ["action"],
        },
    },
}

# In-memory task list — reset per session
_tasks = []


def reset():
    _tasks.clear()


def get_tasks():
    return list(_tasks)


def execute(args: dict, ctx) -> str:
    try:
        from emelya.theme import C
    except ImportError:
        class C:
            info = success = warning = muted = reset = ""

    action = args.get("action", "")
    title = args.get("title", "")
    task_id = args.get("id")

    if action == "create":
        if not title:
            return "Error: title is required"
        _tasks.append({"id": len(_tasks) + 1, "title": title, "status": "in_progress"})
        task = _tasks[-1]
        _print_tasks(C)
        return f"Task #{task['id']} created: {title}"

    if action == "update":
        if task_id is None:
            return "Error: id is required"
        task = _find(task_id)
        if not task:
            return f"Error: task #{task_id} not found"
        if title:
            task["title"] = title
        _print_tasks(C)
        return f"Task #{task_id} updated"

    if action == "complete":
        if task_id is None:
            return "Error: id is required"
        task = _find(task_id)
        if not task:
            return f"Error: task #{task_id} not found"
        task["status"] = "done"
        _print_tasks(C)
        return f"Task #{task_id} completed"

    if action == "list":
        if not _tasks:
            return "No tasks"
        lines = []
        for t in _tasks:
            mark = "✓" if t["status"] == "done" else "→"
            lines.append(f"  {mark} #{t['id']} {t['title']}")
        return "\n".join(lines)

    return f"Error: unknown action '{action}'. Use: create, update, complete, list"


def _find(task_id):
    for t in _tasks:
        if t["id"] == task_id:
            return t
    return None


def _print_tasks(C):
    """Print compact task status line."""
    if not _tasks:
        return
    done = sum(1 for t in _tasks if t["status"] == "done")
    total = len(_tasks)
    current = next((t for t in _tasks if t["status"] == "in_progress"), None)
    label = current["title"] if current else "..."
    sys.stdout.write(f"\r\033[K  {C.muted}[{done}/{total}] {label}{C.reset}")
    sys.stdout.flush()
