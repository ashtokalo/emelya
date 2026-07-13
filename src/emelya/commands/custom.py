"""REPL custom commands — /commands from .emelya/commands/*.md"""

from pathlib import Path

from emelya.i18n import t


def _extract_cmd_desc(content: str) -> str:
    """Extract description from YAML frontmatter or first heading."""
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                if line.startswith("description:"):
                    return line.split(":", 1)[1].strip()
            content = parts[2]
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _scan_custom_commands(self) -> dict:
    """Scan global and local .emelya/commands/ for user-defined .md commands."""
    result = {}
    dirs = [
        Path.home() / ".emelya" / "commands",
        self.config.work_dir / ".emelya" / "commands",
    ]
    for commands_dir in dirs:
        if not commands_dir.is_dir():
            continue
        for f in sorted(commands_dir.glob("*.md")):
            try:
                content = f.read_text(encoding="utf-8").strip()
                desc = _extract_cmd_desc(content) or f.stem
                result[f"/{f.stem}"] = {"path": f, "desc": desc}
            except Exception:
                result[f"/{f.stem}"] = {"path": f, "desc": f.stem}
    return result


def _run_custom_command(self, name: str, args: str = ""):
    """Execute a custom command from .emelya/commands/<name>.md."""
    info = self._custom_commands.get(f"/{name}")
    if not info:
        return
    try:
        content = info["path"].read_text(encoding="utf-8").strip()
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                content = parts[2].strip()
        if args:
            content = content.replace("{args}", args)
        self._agent_loop(content)
    except Exception as e:
        print(f"  Error running /{name}: {e}")


def _cmd_list_commands(self):
    """List custom commands from .emelya/commands/."""
    if not self._custom_commands:
        print("  No custom commands. Create .md files in .emelya/commands/ to add /commands.")
        return
    print(f"  {t('available')}:")
    for cmd, info in self._custom_commands.items():
        print(f"    {cmd} — {info['desc']}")
