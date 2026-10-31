"""REPL /export command."""

import json
from datetime import datetime

from emelya.i18n import t


def _cmd_export(self, path: str = ""):
    if not path:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = f"{self.session_name}-{ts}.md"

    export_path = self.config.work_dir / path

    lines = [f"# Conversation: {self.session_name}\n"]

    entries = self.conversation_log
    if not entries:
        entries = [
            {"type": message["role"], "content": message.get("content", "")}
            for message in self.context.messages
            if message.get("role") in ("user", "assistant")
        ]

    for entry in entries:
        etype = entry["type"]
        if etype == "user":
            lines.append(f"## User\n\n{entry['content']}\n")
        elif etype == "assistant":
            lines.append(f"## Assistant\n\n{entry['content']}\n")
        elif etype == "tool_call":
            name = entry["name"]
            args = entry.get("args", {})
            if name == "edit":
                changes = args.get("changes", [])
                lines.append(f"**● {t('tool_edit', path=f'{len(changes)} change(s)')}**\n")
                for change in changes:
                    operation = next((op for op in ("update", "write", "create", "delete")
                                      if op in change), "?")
                    lines.append(f"- `{operation}` `{change.get(operation, '')}`")
                lines.append("")
            elif name == "bash":
                cmd = args.get("command", "")
                desc = args.get("description", cmd)
                lines.append(f"**● {t('tool_bash', desc=desc)}**\n```bash\n{cmd}\n```\n")
            elif name == "read":
                lines.append(f"**● {t('tool_read', path=args.get('path', ''))}**\n")
            elif name == "grep":
                lines.append(f"**● {t('tool_grep', pattern=args.get('pattern', ''))}**\n")
            elif name == "glob":
                lines.append(f"**● {t('tool_glob', pattern=args.get('pattern', ''))}**\n")
            elif name == "agent":
                lines.append(f"**● {t('tool_agent', name=args.get('name', ''))}** {json.dumps(args.get('args', {}), ensure_ascii=False)}\n")
            else:
                lines.append(f"**● {t('tool_other', name=name)}** `{json.dumps(args, ensure_ascii=False)}`\n")
        elif etype == "tool_result":
            content = entry.get("content", "")
            if content.startswith("Error:"):
                lines.append(f"❌ {content}\n")
            else:
                preview = content[:1000]
                if len(content) > 1000:
                    preview += f"\n... ({len(content)} chars total)"
                lines.append(f"<details><summary>Result ({len(content)} chars)</summary>\n\n```\n{preview}\n```\n</details>\n")

    n_entries = len(entries)
    text = "\n".join(lines)
    export_path.write_text(text, encoding="utf-8")
    print(f"  Exported: {export_path} ({n_entries} entries, {len(text)} chars)")
