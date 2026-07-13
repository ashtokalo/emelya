"""REPL /clear command."""

from datetime import datetime

from emelya.i18n import t
from emelya.input_history import InputHistory


def _cmd_clear(self):
    if self.context.messages:
        self._autosave()

    self.log.close()

    self.session_name = datetime.now().strftime("tmp%Y%m%d%H%M%S")
    self.context.clear()
    self.conversation_log.clear()
    self.tool_ctx.files_read.clear()
    self.tool_ctx.loaded_skills.clear()
    self._init_system_prompt()

    self.log = self._create_log()
    self.log.session_start(
        self.config.base_url, self.config.model,
        str(self.config.work_dir), self.session_name,
    )
    self.input_history = InputHistory(str(self._session_dir() / "history"))

    tokens = self.context.estimate_tokens()
    print(f"  {t('context_cleared')} [{tokens} tokens]")
