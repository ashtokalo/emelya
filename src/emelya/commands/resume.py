"""REPL /resume command."""

import os

from emelya.i18n import t
from emelya.input_history import InputHistory
from emelya.terminal_markdown import render_terminal_markdown
from emelya.theme import C


def _replay_dialog(self):
    """Replay saved user/assistant messages in their normal conversational form."""
    visible = [
        message for message in self.context.messages
        if message.get("role") in ("user", "assistant") and message.get("content")
    ]
    if not visible:
        return
    print()
    for message in visible:
        content = render_terminal_markdown(str(message["content"]))
        if message["role"] == "user":
            label, color = t("you"), C.user_prompt
        else:
            label, color = t("emelya"), C.agent_prefix
        separator = "\n" if content.startswith("┌") else " "
        print(f"{color}{label}:{C.reset}{separator}{content}")
    print()


def _cmd_resume(self, name: str = ""):
    if not name:
        if not self.sessions_dir.is_dir():
            print(f"  {t('no_sessions')}")
            return
        sessions = sorted(d.name for d in self.sessions_dir.iterdir() if d.is_dir())
        if not sessions:
            print(f"  {t('no_sessions')}")
            return
        print(f"  {t('available_sessions')}:")
        for s in sessions:
            ctx_file = self.sessions_dir / s / "context.json"
            marker = " *" if s == self.session_name else ""
            size = f" ({os.path.getsize(ctx_file)} bytes)" if ctx_file.exists() else ""
            print(f"    {s}{size}{marker}")
        return

    ctx_path = self.sessions_dir / name / "context.json"
    if not ctx_path.exists():
        print(f"  {t('session_not_found')}: {name}")
        return

    self._autosave()
    self.log.event(f"Switching to session: {name}")
    self.log.close()

    self.session_name = name
    self.context.load(ctx_path)
    # The transient export log is not stored separately. Rebuild its durable
    # user/assistant portion from Context so /export works after /resume.
    self.conversation_log = [
        {"type": message["role"], "content": message.get("content", "")}
        for message in self.context.messages
        if message.get("role") in ("user", "assistant")
    ]
    self.log = self._create_log()
    self.log.event(f"Session resumed: {name}")
    self.tool_ctx.files_read.clear()
    self.tool_ctx.loaded_skills.clear()
    self.input_history = InputHistory(str(self._session_dir() / "history"))

    tokens = self.context.estimate_tokens()
    n_msgs = len(self.context.messages)
    print(f"  {t('session_loaded')}: {name} ({n_msgs} messages, {tokens} tokens)")
    _replay_dialog(self)
