"""REPL /resume command."""

import os

from emelya.i18n import t
from emelya.input_history import InputHistory


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
    self.log = self._create_log()
    self.log.event(f"Session resumed: {name}")
    self.tool_ctx.files_read.clear()
    self.tool_ctx.loaded_skills.clear()
    self.input_history = InputHistory(str(self._session_dir() / "history"))

    tokens = self.context.estimate_tokens()
    n_msgs = len(self.context.messages)
    print(f"  {t('session_loaded')}: {name} ({n_msgs} messages, {tokens} tokens)")
