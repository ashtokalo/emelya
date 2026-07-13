"""REPL /help command."""

from emelya.i18n import t


def _print_help(self):
    help_path = self.app_dir / "prompts" / t("help_file")
    try:
        text = help_path.read_text(encoding="utf-8").strip()
        print(f"\n{text}")
    except FileNotFoundError:
        print("  Help file not found: prompts/help.md")


def _print_banner(self):
    from emelya import __version__
    from emelya.theme import C

    logo = f"""{C.agent_prefix}\
  ██████╗ ██║  ██║ ██████╗   ██████╗  ╔██████╗
  ██╔═══╝ ███╗███║ ██╔═══╝  ██║  ██║  ██╔══██║
  ████╗   ██║█║██║ ████╗    ██║  ██║  ╚██████║
  ██╔═╝   ██║ ╚██║ ██╔═╝    ██║  ██║  ██╔══██║
  ██████╗ ██║  ██║ ██████╗ ██╔╝  ██║ ██╔╝  ██║
  ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚═╝   ╚═╝ ╚═╝   ╚═╝{C.reset}
  {C.muted}Emelya — CLI AI Agent v{__version__}, Fairy-tale dimwit{C.reset}"""
    print(logo)
    print()
    print(f"  {C.info}{t('workdir')}{C.reset} {self.config.work_dir}")
    print(f"  {C.info}{t('session')}{C.reset} {self.session_name}")
    print()
