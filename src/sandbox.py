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
