"""Sandbox — path validation for file operations."""

import os
import threading
from pathlib import Path


class Sandbox:
    """Validates that all file paths stay within allowed boundaries."""

    ALLOWED_PREFIXES = ["/tmp"]

    def __init__(self, work_dir: Path, extra_allowed: list = None,
                 request_access=None):
        self.work_dir = work_dir.resolve()
        self._allowed = list(self.ALLOWED_PREFIXES)
        self._request_access = request_access
        self._session_grants = []  # [(resolved Path, includes_children)]
        self._scope = threading.local()
        if extra_allowed:
            for p in extra_allowed:
                self._allowed.append(str(Path(p).resolve()))

    def begin_access_scope(self):
        """Begin one tool call; one-time grants live until its completion."""
        self._scope.active = True
        self._scope.grants = []

    def end_access_scope(self):
        """Discard one-time grants for the current tool call/thread."""
        self._scope.active = False
        self._scope.grants = []

    @staticmethod
    def _matches_grant(resolved: Path, grant) -> bool:
        path, includes_children = grant
        if resolved == path:
            return True
        if includes_children:
            try:
                resolved.relative_to(path)
                return True
            except ValueError:
                pass
        return False

    def _is_granted(self, resolved: Path) -> bool:
        grants = list(self._session_grants)
        grants.extend(getattr(self._scope, "grants", []))
        return any(self._matches_grant(resolved, grant) for grant in grants)

    def validate(self, path: str) -> Path:
        """Resolve path relative to work_dir and verify it's within sandbox.

        Returns absolute Path.
        Raises PermissionError if path escapes sandbox.
        """
        if not path:
            raise PermissionError("Empty path")

        # Resolve relative to work_dir
        resolved = (self.work_dir / path).resolve()

        # Check if within work_dir
        try:
            resolved.relative_to(self.work_dir)
            return resolved
        except ValueError:
            pass

        # Check allowed prefixes
        resolved_str = str(resolved)
        for prefix in self._allowed:
            if resolved_str == prefix or resolved_str.startswith(prefix + os.sep):
                return resolved

        if self._is_granted(resolved):
            return resolved

        if self._request_access:
            decision = self._request_access(resolved)
            includes_children = resolved.is_dir()
            grant = (resolved, includes_children)
            if decision == "session":
                self._session_grants.append(grant)
                return resolved
            if decision == "once":
                if getattr(self._scope, "active", False):
                    self._scope.grants.append(grant)
                return resolved

        raise PermissionError(
            f"Access denied: '{path}' is outside working directory"
        )

    def suggest_path(self, path: str) -> str:
        """If file not found, try to suggest a similar existing path.

        Strategy:
        1. Try removing each directory level: src/aigent/repl.py → src/repl.py
        2. Glob for **/{filename} — finds file anywhere in project
        """
        from pathlib import PurePosixPath
        parts = PurePosixPath(path).parts
        # Try removing each directory level
        for skip in range(len(parts) - 1):
            candidate_parts = parts[:skip] + parts[skip + 1:]
            candidate = str(Path(*candidate_parts)) if candidate_parts else ""
            if candidate and (self.work_dir / candidate).exists():
                return candidate

        # Glob for filename anywhere in the project
        filename = parts[-1] if parts else ""
        if filename:
            matches = []
            for found in self.work_dir.rglob(filename):
                # Skip hidden dirs and common junk
                rel = str(found.relative_to(self.work_dir))
                skip_prefixes = (".git/", "node_modules/", "__pycache__/", ".venv/")
                if any(rel.startswith(p) or f"/{p}" in rel for p in skip_prefixes):
                    continue
                matches.append(rel)
                if len(matches) >= 5:
                    break
            if len(matches) == 1:
                return matches[0]
            if matches:
                return ", ".join(matches[:3])
        return ""
