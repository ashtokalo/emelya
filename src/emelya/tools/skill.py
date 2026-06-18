"""Skill tool — load specialized instructions into context."""

from pathlib import Path

DEFINITION = {
    "type": "function",
    "function": {
        "name": "skill",
        "description": "Load a skill with specialized instructions.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name (e.g. 'git', 'data')",
                },
            },
            "required": ["name"],
        },
    },
}


def scan_skills(skills_dir: Path) -> dict:
    """Scan skills directory, return {name: description}."""
    skills = {}
    if not skills_dir.is_dir():
        return skills

    for f in sorted(skills_dir.glob("*.md")):
        try:
            content = f.read_text(encoding="utf-8")
            desc = _extract_description(content) or f.stem
            skills[f.stem] = desc
        except Exception:
            skills[f.stem] = f.stem
    return skills


def _extract_description(content: str) -> str:
    """Extract description from frontmatter or first heading."""
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                if line.startswith("description:"):
                    return line.split(":", 1)[1].strip()
            # Fall through to heading search in body
            content = parts[2]

    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return None


def _load_body(path: Path) -> str:
    """Load skill body (without frontmatter)."""
    content = path.read_text(encoding="utf-8").strip()
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return content


def execute(args: dict, ctx) -> str:
    name = args["name"]

    if not ctx.skills_dir:
        return "Error: skills directory not configured"

    # Normalize: strip .md if provided
    if name.endswith(".md"):
        name = name[:-3]

    if name in ctx.loaded_skills:
        return f"Skill '{name}' is already loaded"

    path = ctx.skills_dir / f"{name}.md"
    if not path.exists():
        available = scan_skills(ctx.skills_dir)
        avail_list = ", ".join(available.keys()) if available else "none"
        return f"Unknown skill '{name}'. Available: {avail_list}"

    try:
        body = _load_body(path)
        ctx.loaded_skills.add(name)
        return body
    except Exception as e:
        return f"Error loading skill '{name}': {e}"
