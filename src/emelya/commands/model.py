"""REPL /model command."""

from emelya.i18n import t


def _cmd_model(self, arg: str = ""):
    parts = arg.split(None, 1) if arg else []
    subcmd = parts[0] if parts else ""
    subarg = parts[1].strip() if len(parts) > 1 else ""

    if subcmd == "add":
        self._model_add(subarg)
    elif subcmd == "remove":
        self._model_remove(subarg)
    elif subcmd == "":
        self._list_models()
    elif subcmd in self._models:
        self._switch_model(subcmd)
    else:
        print(f"  {t('unknown_model')}: {subcmd}")
        print("  Usage: /model, /model <name>, /model add <name> <url> <model> [api_key], /model remove <name>")
