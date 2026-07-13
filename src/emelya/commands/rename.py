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
    self.session_name = name
    session_dir = self._session_dir()

    self.context.save(session_dir / "context.json")

    self.log.event(f"Session renamed: {old_name} -> {name}")
    old_log_path = self.log.path
    self.log.close()
    new_log_path = session_dir / "log.txt"
    if old_log_path != new_log_path:
        shutil.copy2(old_log_path, new_log_path)
    self.log = SessionLog(new_log_path)

    print(f"  {t('session_saved')}: {self.session_name}")
