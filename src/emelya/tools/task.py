"""Persistent task progress tool for multi-step work."""

import sys


DEFINITION = {
    "type": "function",
    "function": {
        "name": "task",
        "description": "Track persistent progress for multi-step work",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "update", "complete", "fail", "list"],
                    "description": "Operation to perform",
                },
                "title": {"type": "string", "description": "Task title"},
                "id": {"type": "integer", "description": "Task ID"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "done", "failed", "skipped"],
                },
                "note": {"type": "string", "description": "Failure or progress note"},
            },
            "required": ["action"],
        },
    },
}


# Compatibility fallback for isolated tests/contexts without a FileTaskStore.
_tasks = []


def reset():
    _tasks.clear()


def get_tasks(ctx=None):
    store = getattr(ctx, "task_store", None) if ctx else None
    return store.list() if store else list(_tasks)


def _legacy_find(task_id):
    return next((task for task in _tasks if task["id"] == task_id), None)


def execute(args: dict, ctx) -> str:
    try:
        from emelya.theme import C
    except ImportError:
        class C:
            info = success = warning = muted = reset = ""

    action = args.get("action", "")
    title = args.get("title", "")
    task_id = args.get("id")
    store = getattr(ctx, "task_store", None)

    try:
        if action == "create":
            if store:
                task = store.create(title, status=args.get("status", "in_progress"))
            else:
                if not title:
                    return "Error: title is required"
                task = {"id": len(_tasks) + 1, "title": title, "status": "in_progress"}
                _tasks.append(task)
            _print_tasks(get_tasks(ctx), C)
            return f"Task #{task['id']} created: {title}"

        if action in ("update", "complete", "fail"):
            if task_id is None:
                return "Error: id is required"
            status = args.get("status")
            if action == "complete":
                status = "done"
            elif action == "fail":
                status = "failed"
            if store:
                changes = {}
                if title:
                    changes["title"] = title
                if status:
                    changes["status"] = status
                if args.get("note"):
                    changes["error"] = args["note"]
                task = store.update(task_id, **changes)
            else:
                task = _legacy_find(task_id)
                if not task:
                    return f"Error: task #{task_id} not found"
                if title:
                    task["title"] = title
                if status:
                    task["status"] = status
            _print_tasks(get_tasks(ctx), C)
            return f"Task #{task_id} {task['status']}"

        if action == "list":
            tasks = get_tasks(ctx)
            if not tasks:
                return "No tasks"
            return "\n".join(
                f"  {_mark(task['status'])} #{task['id']} [{task['status']}] {task['title']}"
                for task in tasks
            )

        return "Error: unknown action. Use: create, update, complete, fail, list"
    except (ValueError, KeyError, OSError, TypeError) as exc:
        return f"Error: {exc}"


def _mark(status):
    return {"done": "✓", "failed": "✗", "skipped": "-"}.get(status, "→")


def _print_tasks(tasks, C):
    if not tasks:
        return
    done = sum(1 for task in tasks if task["status"] == "done")
    current = next((task for task in tasks if task["status"] == "in_progress"), None)
    label = current["title"] if current else "..."
    sys.stdout.write(f"\r\033[K  {C.muted}[{done}/{len(tasks)}] {label}{C.reset}")
    sys.stdout.flush()
