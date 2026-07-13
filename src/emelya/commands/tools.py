"""REPL /tools command."""

from emelya.i18n import t


def _cmd_tools(self):
    print(f"  {t('loaded_tools')}: {', '.join(self.registry.names)}")
    extensions = self.registry.get_extension_summary()
    if extensions:
        print(f"  {t('extensions')} (via load_tools):")
        for cat, tools in extensions.items():
            loaded = " [loaded]" if cat in self.registry._loaded else ""
            print(f"    {cat}: {', '.join(tools.keys())}{loaded}")
