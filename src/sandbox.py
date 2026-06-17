"""Sandbox — path validation for file operations."""

import os
from pathlib import Path


class Sandbox:
    """Validates that all file paths stay within allowed boundaries."""

    ALLOWED_PREFIXES = ["/tmp"]

    def __init__(self, work_dir: Path):
        self.work_dir = work_dir.resolve()

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
        for prefix in self.ALLOWED_PREFIXES:
            if resolved_str == prefix or resolved_str.startswith(prefix + os.sep):
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
