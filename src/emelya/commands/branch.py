"""REPL /branch command."""

import shutil
from datetime import datetime

from emelya.i18n import t
from emelya.input_history import InputHistory


def _cmd_branch(self, name: str = ""):
    branch_name = name or (self.session_name + "-branch-" + datetime.now().strftime("%H%M%S"))

    self._autosave()
    old_name = self.session_name
    old_session_dir = self.sessions_dir / old_name

    self.session_name = branch_name
    session_dir = self._session_dir()

    self.context.save(session_dir / "context.json")
    for dirname in ("memory", "tasks"):
        old_state = old_session_dir / dirname
        new_state = session_dir / dirname
        if old_state.is_dir() and old_state != new_state:
            shutil.copytree(old_state, new_state, dirs_exist_ok=True)

    self.log.event(f"Branched to: {branch_name}")
    self.log.close()
    self.log = self._create_log()
    self.log.session_start(
        self.config.base_url, self.config.model,
        str(self.config.work_dir), self.session_name,
    )
    self.log.event(f"Branched from: {old_name}")
    self.input_history = InputHistory(str(session_dir / "history"))

    tokens = self.context.estimate_tokens()
    n_msgs = len(self.context.messages)
    print(f"  Branched: {old_name} → {branch_name} ({n_msgs} messages, {tokens} tokens)")
