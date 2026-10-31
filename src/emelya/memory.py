"""Small file-backed shared memory for one Emelya session."""

import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union


_ID_RE = re.compile(r"^(mem|link)-[0-9TZ.-]+-[0-9a-f]{8}$")


class FileMemoryStore:
    """Durable file records shared by the main agent and sub-agents.

    Records are independent JSON files, so concurrent writers do not contend on
    a central index. Reviews rewrite one record atomically while preserving its
    status history. A callable root lets the store follow session switches.
    """

    def __init__(self, root: Union[Path, Callable[[], Path]]):
        self._root = root
        self._lock = threading.RLock()

    @property
    def root(self) -> Path:
        value = self._root() if callable(self._root) else self._root
        return Path(value)

    @property
    def entries_dir(self) -> Path:
        return self.root / "entries"

    @property
    def links_dir(self) -> Path:
        return self.root / "links"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @classmethod
    def _new_id(cls, prefix: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _validate_id(record_id: str, prefix: Optional[str] = None) -> str:
        if not isinstance(record_id, str) or not _ID_RE.fullmatch(record_id):
            raise ValueError("invalid memory id")
        if prefix and not record_id.startswith(prefix + "-"):
            raise ValueError(f"expected {prefix} id")
        return record_id

    @staticmethod
    def _atomic_write(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".emelya-memory-", dir=str(path.parent))
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

    def put(self, summary: str, kind: str = "fact", scope: str = "",
            details: str = "", evidence: Optional[dict] = None,
            tags: Optional[list] = None, status: str = "proposed",
            author: str = "agent") -> dict:
        summary = str(summary or "").strip()
        if not summary:
            raise ValueError("summary is required")
        if len(summary) > 4000:
            raise ValueError("summary is too long (max 4000 characters)")
        if len(str(details or "")) > 200000:
            raise ValueError("details are too long (max 200000 characters)")

        record_id = self._new_id("mem")
        record = {
            "schema": 1,
            "id": record_id,
            "kind": str(kind or "fact"),
            "summary": summary,
            "scope": str(scope or ""),
            "details": str(details or ""),
            "evidence": evidence or {},
            "tags": sorted({str(tag) for tag in (tags or []) if str(tag).strip()}),
            "status": str(status or "proposed"),
            "author": str(author or "agent"),
            "created_at": self._now(),
        }
        # Validate serializability before creating the destination file.
        json.dumps(record, ensure_ascii=False)
        with self._lock:
            self._atomic_write(self.entries_dir / f"{record_id}.json", record)
        return record

    def get(self, record_id: str) -> dict:
        record_id = self._validate_id(record_id, "mem")
        path = self.entries_dir / f"{record_id}.json"
        if not path.is_file():
            raise KeyError(f"memory entry not found: {record_id}")
        record = json.loads(path.read_text(encoding="utf-8"))
        record["links"] = self.links(record_id)
        return record

    def review(self, record_id: str, status: str, note: str = "",
               author: str = "agent") -> dict:
        """Change verification status while preserving an audit trail."""
        record_id = self._validate_id(record_id, "mem")
        allowed = {"proposed", "verified", "rejected", "needs_review"}
        if status not in allowed:
            raise ValueError("status must be proposed, verified, rejected, or needs_review")
        path = self.entries_dir / f"{record_id}.json"
        with self._lock:
            if not path.is_file():
                raise KeyError(f"memory entry not found: {record_id}")
            record = json.loads(path.read_text(encoding="utf-8"))
            previous = record.get("status", "proposed")
            reviewed_at = self._now()
            record.setdefault("reviews", []).append({
                "from": previous,
                "to": status,
                "note": str(note or ""),
                "author": str(author or "agent"),
                "created_at": reviewed_at,
            })
            record["status"] = status
            record["updated_at"] = reviewed_at
            self._atomic_write(path, record)
        record["links"] = self.links(record_id)
        return record

    def search(self, query: str = "", kind: str = "", scope: str = "",
               status: str = "", tags: Optional[list] = None,
               limit: int = 10) -> list:
        limit = max(1, min(int(limit), 50))
        terms = [term for term in str(query or "").casefold().split() if term]
        required_tags = {str(tag).casefold() for tag in (tags or []) if str(tag).strip()}
        matches = []

        if not self.entries_dir.is_dir():
            return []
        for path in self.entries_dir.glob("mem-*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if kind and record.get("kind") != kind:
                continue
            if status and record.get("status") != status:
                continue
            if scope and str(scope).casefold() not in str(record.get("scope", "")).casefold():
                continue
            record_tags = {str(tag).casefold() for tag in record.get("tags", [])}
            if required_tags and not required_tags.issubset(record_tags):
                continue

            haystack = " ".join([
                str(record.get("summary", "")),
                str(record.get("details", "")),
                str(record.get("scope", "")),
                " ".join(record.get("tags", [])),
                json.dumps(record.get("evidence", {}), ensure_ascii=False),
            ]).casefold()
            if terms and not all(term in haystack for term in terms):
                continue
            score = sum(haystack.count(term) for term in terms)
            matches.append((score, record.get("created_at", ""), record))

        matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [record for _, _, record in matches[:limit]]

    def link(self, source_id: str, target_id: str, relation: str) -> dict:
        source_id = self._validate_id(source_id, "mem")
        target_id = self._validate_id(target_id, "mem")
        relation = str(relation or "").strip()
        if not relation:
            raise ValueError("relation is required")
        # Refuse dangling links.
        self.get(source_id)
        self.get(target_id)

        link_id = self._new_id("link")
        record = {
            "schema": 1,
            "id": link_id,
            "source_id": source_id,
            "target_id": target_id,
            "relation": relation,
            "created_at": self._now(),
        }
        with self._lock:
            self._atomic_write(self.links_dir / f"{link_id}.json", record)
        return record

    def links(self, record_id: str) -> list:
        record_id = self._validate_id(record_id, "mem")
        if not self.links_dir.is_dir():
            return []
        found = []
        for path in self.links_dir.glob("link-*.json"):
            try:
                link = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if record_id in (link.get("source_id"), link.get("target_id")):
                found.append(link)
        return sorted(found, key=lambda item: item.get("created_at", ""))
