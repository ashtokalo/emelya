"""Atomic structured edits across one or more text files."""

import os
import tempfile
from pathlib import Path

from emelya.tools import FileBackup, is_binary, _restore_backup


DEFINITION = {
    "type": "function",
    "function": {
        "name": "edit",
        "description": (
            "Atomically change files. Each item has exactly one path key: "
            "update (exact replacement), write (overwrite existing), create "
            "(new file only), or delete. Existing files MUST be read first. "
            "Changes to the same file are applied in order."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "changes": {
                    "type": "array",
                    "description": "Ordered file changes applied as one operation",
                    "items": {
                        "type": "object",
                        "properties": {
                            "update": {"type": "string", "description": "Existing file path inside sandbox for exact replacement"},
                            "write": {"type": "string", "description": "Existing file path inside sandbox to overwrite completely"},
                            "create": {"type": "string", "description": "New file path inside sandbox; fails if it exists"},
                            "delete": {"type": "string", "description": "Existing file path inside sandbox to delete"},
                            "old_string": {"type": "string"},
                            "new_string": {"type": "string"},
                            "content": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["changes"],
        },
    },
}


def _default_mode():
    current = os.umask(0)
    os.umask(current)
    return 0o666 & ~current


def _write_temp(path, content, mode):
    fd, name = tempfile.mkstemp(prefix=".emelya-edit-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode if mode is not None else _default_mode())
        return Path(name)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def _validate_path(raw_path, ctx):
    """Accept relative or absolute paths as long as sandbox contains them."""
    return ctx.sandbox.validate(raw_path)


def _prepare(args, ctx):
    changes = args.get("changes")
    if not isinstance(changes, list) or not changes:
        raise ValueError("changes must be a non-empty array")

    states = {}
    operation_count = 0
    action_names = ("update", "write", "create", "delete")
    for change in changes:
        if not isinstance(change, dict):
            raise ValueError("each change must be an object")
        actions = [name for name in action_names if name in change]
        if len(actions) != 1:
            raise ValueError("each change must contain exactly one of: update, write, create, delete")
        action = actions[0]
        raw_path = change[action]
        if not isinstance(raw_path, str):
            raise ValueError("%s path must be a string" % action)
        path = _validate_path(raw_path, ctx)
        key = str(path)
        operation_count += 1

        if action == "create":
            lexical = ctx.work_dir / raw_path
            if key in states or path.exists() or os.path.lexists(str(lexical)):
                raise ValueError("create target already exists: %s" % raw_path)
            content = change.get("content")
            if not isinstance(content, str):
                raise ValueError("create requires string content: %s" % raw_path)
            states[key] = {
                "path": path, "raw_path": raw_path, "original": None,
                "content": content.encode("utf-8"), "mode": None,
            }
            continue

        if key not in states:
            if not path.exists() or not path.is_file():
                raise ValueError("file not found: %s" % raw_path)
            if key not in ctx.files_read:
                raise ValueError("file was not read first: %s" % raw_path)
            if is_binary(path):
                raise ValueError("binary files are not supported: %s" % raw_path)
            original = path.read_bytes()
            try:
                original.decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError("file is not valid UTF-8: %s" % raw_path)
            states[key] = {
                "path": path, "raw_path": raw_path, "original": original,
                "content": original, "mode": path.stat().st_mode,
            }

        state = states[key]
        if state["original"] is None:
            raise ValueError("%s requires an existing file: %s" % (action, raw_path))
        if state["content"] is None:
            raise ValueError("cannot change a file after deleting it: %s" % raw_path)
        if action == "delete":
            state["content"] = None
            continue

        if action == "write":
            content = change.get("content")
            if not isinstance(content, str):
                raise ValueError("write requires string content: %s" % raw_path)
            state["content"] = content.encode("utf-8")
            continue

        old_string = change.get("old_string")
        new_string = change.get("new_string")
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            raise ValueError("update requires old_string and new_string: %s" % raw_path)
        if not old_string:
            raise ValueError("old_string must not be empty: %s" % raw_path)
        content = state["content"].decode("utf-8")
        count = content.count(old_string)
        if count == 0:
            raise ValueError("old_string not found: %s" % raw_path)
        if count > 1:
            raise ValueError("old_string matches %d times, provide more context: %s" %
                             (count, raw_path))
        state["content"] = content.replace(old_string, new_string, 1).encode("utf-8")

    return list(states.values()), operation_count


def _verify_unchanged(states):
    for state in states:
        path, original = state["path"], state["original"]
        if original is None:
            if path.exists() or path.is_symlink():
                raise OSError("file appeared before commit: %s" % state["raw_path"])
        else:
            if not path.is_file() or path.read_bytes() != original:
                raise OSError("file changed before commit: %s" % state["raw_path"])


def _commit(states):
    staged, committed, created_dirs = {}, [], []
    try:
        _verify_unchanged(states)
        for state in states:
            if state["content"] is None:
                continue
            missing = []
            parent = state["path"].parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            state["path"].parent.mkdir(parents=True, exist_ok=True)
            created_dirs.extend(reversed(missing))
            staged[state["path"]] = _write_temp(
                state["path"], state["content"], state["mode"])
        _verify_unchanged(states)
        for state in states:
            if state["content"] is None:
                state["path"].unlink()
            else:
                os.replace(str(staged.pop(state["path"])), str(state["path"]))
            committed.append(state)
    except Exception:
        for state in reversed(committed):
            _restore_backup(FileBackup(
                str(state["path"]), state["original"], state["mode"]))
        raise
    finally:
        for temp in staged.values():
            try:
                temp.unlink()
            except OSError:
                pass
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass


def execute(args: dict, ctx) -> str:
    try:
        states, operation_count = _prepare(args, ctx)
    except (ValueError, PermissionError, OSError) as exc:
        return "Error: " + str(exc)

    summary = "%d files, %d changes" % (len(states), operation_count)
    if ctx.confirm and not ctx.confirm("Apply edits", summary):
        return ctx.cancellation_result()

    hook_runner = getattr(ctx, "hook_runner", None)
    if hook_runner:
        for state in states:
            old_content = (state["original"].decode("utf-8")
                           if state["original"] is not None else None)
            new_content = (state["content"].decode("utf-8")
                           if state["content"] is not None else None)
            operation = ("create" if state["original"] is None else
                         "delete" if state["content"] is None else "update")
            hr = hook_runner.run("PreWrite", {
                "path": str(state["path"]), "operation": operation,
                "old_content": old_content, "new_content": new_content,
                "tool": "edit",
            })
            if hr.block:
                return "Blocked by hook: %s" % (hr.reason or hr.message or "hook")

    try:
        _commit(states)
    except Exception as exc:
        return "Error: commit failed: %s" % exc

    backups = [FileBackup(str(s["path"]), s["original"], s["mode"]) for s in states]
    ctx.push_changeset("Edit %d files" % len(states), backups)
    return "Applied edits: " + summary
