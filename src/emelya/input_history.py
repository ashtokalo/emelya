"""Input history — persistent command history for line editor."""

import json
from pathlib import Path


class InputHistory:
    """Stores input history as plain strings. Persists to JSONL file."""

    def __init__(self, path: str = None, max_size: int = 500):
        self._items = []
        self._max = max_size
        self._path = path
        if path:
            self._load()

    def _load(self):
        try:
            p = Path(self._path)
            if not p.exists():
                return
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    self._items.append(json.loads(line))
                except json.JSONDecodeError:
                    self._items.append(line)
            self._items = self._items[-self._max:]
        except Exception:
            pass

    def save(self):
        if not self._path:
            return
        try:
            p = Path(self._path)
            p.parent.mkdir(parents=True, exist_ok=True)
            lines = [json.dumps(item, ensure_ascii=False)
                     for item in self._items[-self._max:]]
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            pass

    def add(self, text: str):
        if not text:
            return
        if self._items and self._items[-1] == text:
            return
        self._items.append(text)
        if len(self._items) > self._max:
            self._items.pop(0)
        self.save()

    @property
    def items(self):
        return self._items
