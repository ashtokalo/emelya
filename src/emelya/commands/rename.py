"""REPL /rename command."""

import shutil

from emelya.i18n import t
from emelya.log import SessionLog


def _cmd_rename(self, name: str = ""):
    if not name:
        print(f"  Usage: /rename <new_name>")
        print(f"  Current: {self.session_name}")
        return

    old_name = self.session_name
    old_session_dir = self.sessions_dir / old_name
    self.session_name = name
    session_dir = self._session_dir()

    self.context.save(session_dir / "context.json")
    for dirname in ("memory", "tasks"):
        old_state = old_session_dir / dirname
        new_state = session_dir / dirname
        if old_state.is_dir() and old_state != new_state:
            shutil.copytree(old_state, new_state, dirs_exist_ok=True)

    self.log.event(f"Session renamed: {old_name} -> {name}")
    old_log_path = self.log.path
    self.log.close()
    new_log_path = session_dir / "log.txt"
    if old_log_path != new_log_path:
        shutil.copy2(old_log_path, new_log_path)
    self.log = SessionLog(new_log_path)

    print(f"  {t('session_saved')}: {self.session_name}")
