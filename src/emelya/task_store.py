"""Session-scoped persistent task state stored as independent JSON files."""

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Union


_STATUSES = {"pending", "in_progress", "done", "failed", "skipped"}


class FileTaskStore:
    """Small dependency-free task store that follows session switches."""

    def __init__(self, root: Union[Path, Callable[[], Path]]):
        self._root = root
        self._lock = threading.RLock()

    @property
    def root(self) -> Path:
        value = self._root() if callable(self._root) else self._root
        return Path(value)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _path(root: Path, task_id: int) -> Path:
        task_id = int(task_id)
        if task_id < 1:
            raise ValueError("task id must be positive")
        return root / f"task-{task_id:06d}.json"

    @staticmethod
    def _atomic_write(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".emelya-task-", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def list(self) -> list:
        if not self.root.is_dir():
            return []
        tasks = []
        for path in self.root.glob("task-*.json"):
            try:
                tasks.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(tasks, key=lambda task: int(task.get("id", 0)))

    def get(self, task_id: int) -> dict:
        path = self._path(self.root, task_id)
        if not path.is_file():
            raise KeyError(f"task #{int(task_id)} not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def create(self, title: str, status: str = "pending", description: str = "",
               dependencies=None, metadata=None) -> dict:
        title = str(title or "").strip()
        if not title:
            raise ValueError("title is required")
        if status not in _STATUSES:
            raise ValueError(f"invalid task status: {status}")
        with self._lock:
            existing = self.list()
            task_id = max((int(task.get("id", 0)) for task in existing), default=0) + 1
            now = self._now()
            task = {
                "schema": 1,
                "id": task_id,
                "title": title,
                "description": str(description or ""),
                "status": status,
                "dependencies": [int(value) for value in (dependencies or [])],
                "metadata": metadata or {},
                "created_at": now,
                "updated_at": now,
            }
            self._atomic_write(self._path(self.root, task_id), task)
        return task

    def update(self, task_id: int, **changes) -> dict:
        allowed = {
            "title", "description", "status", "dependencies", "metadata",
            "artifact", "memory_id", "error",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported task fields: {', '.join(sorted(unknown))}")
        with self._lock:
            task = self.get(task_id)
            if "status" in changes and changes["status"] not in _STATUSES:
                raise ValueError(f"invalid task status: {changes['status']}")
            for key, value in changes.items():
                if value is not None:
                    task[key] = value
            task["updated_at"] = self._now()
            self._atomic_write(self._path(self.root, task_id), task)
        return task
